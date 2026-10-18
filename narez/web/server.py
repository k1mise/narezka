"""Веб-интерфейс narez: FastAPI на localhost. Запуск: python run.py"""
from __future__ import annotations

import glob
import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlsplit

import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import banner as bn
from .. import filler as filler_mod
from .. import schedule, service
from ..cleanup import discard_clip
from ..config import FOLDER_TITLES, folder, load_config
from ..editor import make_thumb
from ..db import DB, PLATFORMS
from ..downloader import SourceError, normalize_source, safe_name
from ..history import import_videos, parse_video_ids
from ..meta import normalize_tags
from ..pipeline import run as run_pipeline
from .. import proxies as px
from ..publishers import get_publisher
from ..publishers.base import PLATFORM_TITLES, REAL_PLATFORMS, token_path
from .tasks import Busy, TaskManager

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
BANNER_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".mp4", ".mov", ".mkv", ".webm", ".m4v"}
MAX_BANNER_BYTES = 500 * 1024 * 1024


class NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        r.headers["Cache-Control"] = "no-cache"
        return r


class GroupIn(BaseModel):
    name: str


class AccountIn(BaseModel):
    platform: str
    label: str = ""


class KeysIn(BaseModel):
    client_id: str = ""
    client_secret: str = ""


class CutIn(BaseModel):
    mode: str = "moments"        # moments = интересные моменты, full = всё видео по частям
    length: float = 45.0         # длина части (режим full), секунды
    overlap: float = 2.5         # нахлёст: следующая часть начинается за столько секунд до конца предыдущей
    audio: str = "ru"            # ru = русская дорожка, если у видео есть дубляж; original = оригинал
    adcut_sb: Optional[bool] = None    # вырезать рекламу по SponsorBlock (None = как в config.toml)
    adcut_text: Optional[bool] = None  # искать рекламу в тексте (None = как в config.toml)
    subs_fallback: str = "youtube"     # если ручных субтитров YouTube нет: youtube = взять автоматические, whisper = распознать речь


def cut_args(b: CutIn) -> dict:
    if b.audio not in ("ru", "original"):
        raise HTTPException(400, "Звук: ru или original")
    if b.subs_fallback not in ("youtube", "whisper"):
        raise HTTPException(400, "Субтитры: youtube или whisper")
    if b.mode not in ("moments", "full"):
        raise HTTPException(400, "Режим нарезки: moments или full")
    if b.mode == "full":
        if not 5 <= b.length <= 180:
            raise HTTPException(400, "Длина клипа — от 5 до 180 секунд")
        if not 0 <= b.overlap <= min(30, b.length - 5):
            raise HTTPException(400, "Нахлёст — от 0 секунд и не больше длины клипа минус 5 секунд")
    return {"mode": b.mode, "length": b.length, "overlap": b.overlap, "audio": b.audio,
            "adcut_sb": b.adcut_sb, "adcut_text": b.adcut_text, "subs_fallback": b.subs_fallback}


class ProcessIn(CutIn):
    url: str
    group_id: int = 0
    test: bool = False
    template_id: Optional[int] = None   # шаблон: клипы уходят во все его группы, каждая со своим пресетом баннеров


class ProxyAddIn(BaseModel):
    text: str
    scheme: str = "http"


class ProxyUseIn(BaseModel):
    on: bool = True


class PresetIn(BaseModel):
    id: Optional[int] = None     # задан — правка существующего пресета (можно переименовать), иначе создание/перезапись по имени
    name: str
    banner: Optional[dict] = None
    banners: Optional[list[dict]] = None
    tags: str = ""
    footer: str = ""


class ApplyIn(BaseModel):
    clip_ids: list[int]


class RenameIn(BaseModel):
    folder: str
    name: str
    new_name: str


class FileIn(CutIn):
    folder: str
    name: str
    group_id: int = 0
    test: bool = False
    template_id: Optional[int] = None


class TemplateItemIn(BaseModel):
    group_id: int
    preset_id: Optional[int] = None
    tags: str = ""                 # хэштеги только для этой группы


class TemplateIn(BaseModel):
    id: Optional[int] = None
    name: str
    tags: str = ""                 # хэштеги для клипов всех групп шаблона
    items: list[TemplateItemIn]


class ClipPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    banner: Optional[dict] = None
    banners: Optional[list[dict]] = None
    filler: Optional[dict] = None             # {"position": "top"|"bottom"|"" (без вставки), "file": "" (случайное) | имя файла}
    publish_at: Optional[str] = None
    group_id: Optional[int] = None


class PublishIn(BaseModel):
    group_id: int
    clip_ids: Optional[list[int]] = None
    account_ids: Optional[list[int]] = None   # публикация на выбранные аккаунты (любых групп), а не на все аккаунты группы
    mode: str = "keep"  # now: очистить время клипа и выложить сразу; keep: учитывать время клипа


class BatchPublishIn(BaseModel):
    clip_ids: list[int]                      # порядок в списке = порядок выхода
    start: str = ""                          # время выхода первого клипа (ISO UTC); пусто = сразу
    interval_minutes: float = 0              # промежуток между выходами; 0 = все сразу
    account_ids: Optional[list[int]] = None  # None = все аккаунты группы каждого клипа
    filler: Optional[dict] = None            # видео-вставка для всех клипов пачки: {"position": "top"|"bottom"|"" (убрать), "file": ""}; не задано = как в клипах
    privacy: str = ""                        # видимость YouTube для клипов, которые выходят сразу: public/unlisted/private; пусто = из config.toml


class MoveClipsIn(BaseModel):
    clip_ids: list[int]
    group_id: int


class ImportIn(BaseModel):
    urls: str
    group_id: int


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or load_config()
    DB(cfg["paths"]["db"])  # создаёт/обновляет схему один раз при старте
    app = FastAPI(title="narez", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg = cfg
    wd: Path = cfg["paths"]["workdir"]
    tasks = app.state.tasks = TaskManager(store=wd / "logs")   # журнал задач хранится на диске (workspace/logs)
    (wd / "banners").mkdir(parents=True, exist_ok=True)
    health_cache: dict = {"t": 0, "v": None}
    logins: dict = {}

    # ---------- защита: только localhost, запросы-изменения только со своей страницы ----------
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (urlparse("//" + request.headers.get("host", "")).hostname or "").lower()
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Недопустимый адрес"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and (urlparse(origin).hostname or "") not in ALLOWED_HOSTS:
                return JSONResponse({"detail": "Запрос с чужого сайта"}, status_code=403)
        return await call_next(request)

    def get_db():
        d = DB(cfg["paths"]["db"], init=False)
        try:
            yield d
        finally:
            d.c.close()

    # ---------- вспомогательное ----------
    def library() -> dict[str, Path]:
        files = {p.name: p for p in sorted((wd / "banners").iterdir()) if p.is_file() and p.suffix.lower() in BANNER_EXT}
        dflt = bn.resolve(cfg["banner"].get("image", ""), cfg) if cfg["banner"].get("image") else None
        if dflt and dflt.exists() and dflt.name not in files:
            files[dflt.name] = dflt
        return files

    def banners_json(spec_json: str) -> list[dict]:
        return [{**sp, "name": Path(sp["path"]).name} for sp in bn.specs_from_json(spec_json)]

    def max_banners() -> int:
        return max(1, int(cfg["banner"].get("max_banners", 5)))

    def clean_specs(raw: list[dict] | None) -> list[dict]:
        """Баннеры из интерфейса -> проверенные параметры с путями к файлам библиотеки (жёсткий лимит max_banners на клип)."""
        raw = [r for r in (raw or []) if r]
        if len(raw) > max_banners():
            raise HTTPException(400, f"На один клип можно наложить не больше {max_banners()} баннеров (настройка [banner] max_banners)")
        lib, out = library(), []
        for r in raw:
            name = str(r.get("name", ""))
            if name not in lib:
                raise HTTPException(400, "Баннер не найден в библиотеке — загрузи файл заново")
            out.append(bn.normalize_spec({**r, "path": str(lib[name])}, cfg))
        return out

    def filler_json(raw) -> str:
        spec = filler_mod.normalize({"position": (raw or {}).get("position"), "file": (raw or {}).get("file")}) if raw else None
        if spec and spec["file"] and spec["file"] not in {p.name for p in filler_mod.library(cfg)}:
            raise HTTPException(400, "Такого видео нет в папке вставок")
        return json.dumps(spec, ensure_ascii=False) if spec else ""

    def clip_json(c, d: DB) -> dict:
        job = d.job(c["job_id"])
        return {"id": c["id"], "job_id": c["job_id"], "title": c["title"] or "", "description": c["reason"] or "",
                "tags": c["tags"] or "", "duration": round((c["end"] or 0) - (c["start"] or 0), 1),
                "score": round(c["score"], 1) if c["score"] else None,
                "filler": filler_mod.normalize(c["filler"]),
                "banners": banners_json(c["banner"]),
                "banner": (banners_json(c["banner"]) or [None])[0], "publish_at": c["publish_at"] or "",
                "has_file": bool(c["path"]) and Path(c["path"]).exists(),
                "has_thumb": (bool(c["thumb"]) and Path(c["thumb"]).exists()) or (bool(c["path"]) and Path(c["path"]).exists()),
                "source": job["title"] if job else "", "status": c["status"],
                "group_id": d.clip_group(c), "group": (d.group(d.clip_group(c)) or {"name": ""})["name"] if d.clip_group(c) else "",
                "test": bool(job["test"]) if job else False,
                "number": 1 + d.c.execute("SELECT COUNT(*) FROM clips WHERE job_id=? AND id<?", (c["job_id"], c["id"])).fetchone()[0]}

    def eff_cfg(d: DB, log=None) -> dict:
        """Настройки для скачивания: если выбран рабочий прокси — видео качается через него."""
        a = d.active_proxy()
        if not a or a["status"] != "ok":
            return cfg
        if log:
            log(f"Скачивание через прокси {px.mask(a['url'])} ({a['country'] or a['ip']})")
        return {**cfg, "download": {**cfg.get("download", {}), "proxy": a["url"]}}

    def need_clip(d: DB, clip_id: int):
        c = d.clip(clip_id)
        if not c:
            raise HTTPException(404, "Клип не найден")
        return c

    def need_group(d: DB, group_id: int):
        g = d.group(group_id)
        if not g:
            raise HTTPException(404, "Группа не найдена")
        return g

    def started(kind, title, fn, exclusive=False):
        try:
            return tasks.start(kind, title, fn, exclusive).public(after=0)
        except Busy as e:
            raise HTTPException(409, str(e)) from None

    # ---------- состояние и настройки ----------
    @app.get("/api/health")
    def health():
        if time.time() - health_cache["t"] > 8:
            ollama = {"ok": False, "model": cfg["llm"]["model"], "model_ok": False}
            try:
                r = requests.get(cfg["llm"]["host"].rstrip("/") + "/api/tags", timeout=1.5)
                names = [m.get("name", "") for m in r.json().get("models", [])]
                ollama.update(ok=True, model_ok=any(n == cfg["llm"]["model"] or n.startswith(cfg["llm"]["model"] + ":")
                                                    for n in names))
            except Exception:
                pass
            try:
                from importlib.metadata import version
                ytdlp = version("yt-dlp")
            except Exception:
                ytdlp = ""
            health_cache.update(t=time.time(), v={"ffmpeg": bool(shutil.which("ffmpeg")),
                                                  "ffprobe": bool(shutil.which("ffprobe")), "ollama": ollama, "yt_dlp": ytdlp})
        return {**health_cache["v"], "process_running": tasks.running("process"), "publish_running": tasks.running("publish")}

    @app.get("/api/config")
    def config():
        from .. import adcut
        return {"adcut_sb": adcut.settings(cfg)["sponsorblock"], "adcut_text": adcut.settings(cfg)["text"],
                "llm_model": cfg["llm"]["model"], "delay_minutes": cfg["youtube"].get("delay_minutes", 0),
                "privacy": cfg["youtube"].get("privacy"), "min_clip": cfg["clips"]["min_seconds"],
                "max_clip": cfg["clips"]["max_seconds"], "max_banners": max_banners(), "youtube_privacy": cfg["youtube"].get("privacy", "private"), "min_area_percent": cfg["banner"].get("min_area_percent", 25),
                "min_ahead_minutes": int(schedule.MIN_AHEAD.total_seconds() // 60), "platforms": PLATFORMS,
                "real_platforms": sorted(REAL_PLATFORMS),
                "banner_defaults": {"image": {k: v for k, v in bn.default_spec(cfg, "x.png").items() if k != "path"},
                                    "video": {k: v for k, v in bn.default_spec(cfg, "x.mp4").items() if k != "path"}}}

    # ---------- группы и аккаунты ----------
    @app.get("/api/groups")
    def groups(d: DB = Depends(get_db)):
        out = []
        for g in d.groups():
            accs = [{"id": a["id"], "platform": a["platform"], "label": a["label"],
                     "supported": a["platform"] in REAL_PLATFORMS,
                     "authorized": a["platform"] in REAL_PLATFORMS and token_path(cfg, a["platform"], a["id"]).exists(),
                     "has_keys": bool(d.account_credentials(a["id"]).get("client_id")),
                     "keys_id": (d.account_credentials(a["id"]).get("client_id") or "")[:14]}
                    for a in d.accounts(g["id"])]
            out.append({"id": g["id"], "name": g["name"], "accounts": accs, "pending": len(d.pending_clips(g["id"]))})
        return out

    @app.post("/api/groups")
    def add_group(body: GroupIn, d: DB = Depends(get_db)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Название группы не может быть пустым")
        return {"id": d.add_group(name), "name": name}

    @app.post("/api/groups/{group_id}/accounts")
    def add_account(group_id: int, body: AccountIn, d: DB = Depends(get_db)):
        need_group(d, group_id)
        if body.platform not in PLATFORMS:
            raise HTTPException(400, f"Платформа должна быть одной из: {', '.join(PLATFORMS)}")
        d.set_account(group_id, body.platform, body.label.strip() or body.platform)
        return {"ok": True}

    @app.delete("/api/accounts/{account_id}")
    def del_account(account_id: int, d: DB = Depends(get_db)):
        if not d.account(account_id):
            raise HTTPException(404, "Аккаунт не найден")
        cancel_login(account_id)  # если шёл вход в этот аккаунт — прекращаем
        d.delete_account(account_id)
        for plat in REAL_PLATFORMS:  # чужой токен не должен достаться новому аккаунту
            try:
                token_path(cfg, plat, account_id).unlink(missing_ok=True)
            except Exception:
                pass
        return {"ok": True}

    @app.put("/api/accounts/{account_id}/keys")
    def account_keys(account_id: int, body: KeysIn, d: DB = Depends(get_db)):
        """Свои Client ID и Client secret Google-приложения для аккаунта YouTube (пусто = использовать общие)."""
        a = d.account(account_id)
        if not a:
            raise HTTPException(404, "Аккаунт не найден")
        if a["platform"] != "youtube":
            raise HTTPException(400, "Свои ключи пока поддерживаются только для YouTube")
        cid, sec = body.client_id.strip(), body.client_secret.strip()
        if cid.startswith("{"):  # вставили целиком содержимое client_secret.json
            try:
                j = json.loads(cid)
            except ValueError:
                raise HTTPException(400, "Не получилось прочитать JSON — вставь Client ID и Client secret в отдельные поля") from None
            if "web" in j and "installed" not in j:
                raise HTTPException(400, "Это ключи типа «Web application» — с ними вход не работает (redirect_uri_mismatch). "
                                         "Создай в Google Cloud новый OAuth client ID типа «Desktop app»")
            part = j.get("installed") or {}
            cid, sec = str(part.get("client_id", "")).strip(), str(part.get("client_secret", "")).strip()
        if bool(cid) != bool(sec):
            raise HTTPException(400, "Нужно заполнить оба поля: Client ID и Client secret")
        if cid and not cid.endswith(".apps.googleusercontent.com"):
            raise HTTPException(400, "Client ID должен заканчиваться на .apps.googleusercontent.com — проверь, что скопировал именно его")
        creds = d.account_credentials(account_id)
        creds.pop("client_id", None); creds.pop("client_secret", None)
        if cid:
            creds.update(client_id=cid, client_secret=sec)
        d.set_account_credentials(account_id, creds)
        return {"ok": True, "has_keys": bool(cid)}

    @app.post("/api/accounts/{account_id}/login")
    def login(account_id: int, d: DB = Depends(get_db)):
        a = d.account(account_id)
        if not a:
            raise HTTPException(404, "Аккаунт не найден")
        if a["platform"] not in REAL_PLATFORMS:
            raise HTTPException(400, "Для этой платформы вход пока не поддерживается")

        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        plat, title = a["platform"], PLATFORM_TITLES.get(a["platform"], a["platform"])
        pub = get_publisher(plat, cfg)
        if plat != "youtube":
            port = pub.redirect_port()  # у Meta/TikTok адрес возврата зарегистрирован в приложении, порт фиксированный
        state = logins["current"] = {"account_id": account_id, "port": port, "cancelled": False}
        label = a["label"]
        ck = d.account_credentials(account_id)
        client = {"client_id": ck.get("client_id", ""), "client_secret": ck.get("client_secret", "")}

        def fn(log):
            log(f"Открываю браузер для входа в {title}… (Можно отменить кнопкой «Отменить вход».)")
            if plat == "youtube":
                log("Если Google пишет redirect_uri_mismatch — ключи созданы как «Web application»; нужен тип «Desktop app». "
                    "access_denied — добавь почту в Test users этого проекта.")
            try:
                if plat == "youtube":
                    pub.authorize(account_id, port=port, timeout=300, client=client)
                else:
                    who = pub.authorize(account_id, timeout=300, label=label)
                    if who:
                        log(f"Вошёл как {who}.")
            except Exception as e:
                if state["cancelled"]:
                    raise RuntimeError("Вход отменён") from None
                if "Timed out" in str(e):
                    raise RuntimeError("Время на вход вышло (5 минут). Нажми «Войти» ещё раз.") from None
                raise
            finally:
                if logins.get("current") is state:
                    logins.pop("current", None)
            db2 = DB(cfg["paths"]["db"], init=False)
            if not db2.account(account_id):  # аккаунт убрали, пока шёл вход: токен не оставляем
                pub.token_path(account_id).unlink(missing_ok=True)
                raise RuntimeError("Аккаунт был удалён во время входа")
            log("Вход выполнен.")
        return started("login", f"Вход в {title}: {a['label']}", fn, exclusive=True)

    def cancel_login(account_id: int | None = None) -> bool:
        """Прерывает ожидание входа: достаточно «постучаться» на локальный адрес, куда Google вернёт ответ."""
        st = logins.get("current")
        if not st or (account_id is not None and st["account_id"] != account_id):
            return False
        st["cancelled"] = True
        try:
            requests.get(f"http://127.0.0.1:{st['port']}/?error=cancelled", timeout=3)
        except Exception:
            pass
        return True

    @app.post("/api/login/cancel")
    def login_cancel():
        return {"cancelled": cancel_login()}

    # ---------- нарезка ----------
    @app.post("/api/process")
    def process(body: ProcessIn, d: DB = Depends(get_db)):
        gid, plan = resolve_target(d, body.group_id, body.template_id)
        try:
            url = normalize_source(body.url)
        except SourceError as e:
            raise HTTPException(400, str(e)) from None

        cut = cut_args(body)

        def fn(log):
            db2 = DB(cfg["paths"]["db"], init=False)
            job_id, clips = run_pipeline(url, eff_cfg(db2, log), db2, gid, log, test=body.test, **cut)
            fan_out(db2, job_id, plan, log)
            return {"job_id": job_id, "clips": len(clips) * max(1, len(plan))}
        return started("process", f"{'Тест: ' if body.test else 'Нарезка целиком: ' if body.mode == 'full' else 'Нарезка: '}{Path(url).name if not url.startswith('http') else url}", fn, exclusive=True)

    @app.get("/api/tasks")
    def task_list():
        return [t.public() for t in tasks.recent(100)]

    @app.get("/api/tasks/{task_id}")
    def task_get(task_id: str, after: int = 0):
        t = tasks.get(task_id)
        if not t:
            raise HTTPException(404, "Задача не найдена")
        return t.public(after=max(after, 0))

    # ---------- клипы и редактор ----------
    @app.get("/api/clips")
    def all_clips(d: DB = Depends(get_db)):
        return [clip_json(c, d) for c in d.pending_all()]

    @app.get("/api/groups/{group_id}/clips")
    def clips(group_id: int, d: DB = Depends(get_db)):
        need_group(d, group_id)
        return [clip_json(c, d) for c in d.pending_clips(group_id)]

    @app.get("/api/clips/{clip_id}")
    def clip(clip_id: int, d: DB = Depends(get_db)):
        return clip_json(need_clip(d, clip_id), d)

    @app.patch("/api/clips/{clip_id}")
    def patch_clip(clip_id: int, body: ClipPatch, d: DB = Depends(get_db)):
        need_clip(d, clip_id)
        sent, upd = body.model_fields_set, {}
        if "title" in sent and body.title is not None:
            upd["title"] = body.title.strip()[:200]
        if "description" in sent and body.description is not None:
            upd["reason"] = body.description.strip()
        if "tags" in sent and body.tags is not None:
            upd["tags"] = normalize_tags(body.tags)
        if "banners" in sent or "banner" in sent:
            raw = body.banners if "banners" in sent else ([body.banner] if body.banner else [])
            upd["banner"] = bn.specs_to_json(clean_specs(raw))
        if "filler" in sent:
            upd["filler"] = filler_json(body.filler)
        if "group_id" in sent and body.group_id is not None:
            need_group(d, body.group_id)
            upd["group_id"] = body.group_id
        if "publish_at" in sent:
            v = (body.publish_at or "").strip()
            if v:
                at = schedule.from_iso(v)
                if not at:
                    raise HTTPException(400, "Неверный формат времени")
                from datetime import datetime, timezone
                if at < datetime.now(timezone.utc) + schedule.MIN_AHEAD:
                    raise HTTPException(400, f"Время должно быть минимум на {int(schedule.MIN_AHEAD.total_seconds() // 60)} минут позже текущего")
                v = schedule.to_iso(at)
            upd["publish_at"] = v
        d.update_clip(clip_id, **upd)
        return clip_json(d.clip(clip_id), d)

    @app.delete("/api/clips/{clip_id}")
    def delete_clip(clip_id: int, d: DB = Depends(get_db)):
        need_clip(d, clip_id)
        discard_clip(d, cfg, clip_id)
        return {"ok": True}

    @app.get("/api/clips/{clip_id}/video")
    def clip_video(clip_id: int, variant: str = "base", d: DB = Depends(get_db)):
        c = need_clip(d, clip_id)
        path = c["path"]
        if variant == "final":
            path = str(bn.cached_final(c, cfg) or path)
        if not path or not Path(path).exists():
            raise HTTPException(404, "Файл клипа не найден")
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})

    @app.get("/api/clips/{clip_id}/thumb")
    def clip_thumb(clip_id: int, d: DB = Depends(get_db)):
        c = need_clip(d, clip_id)
        if not c["thumb"] or not Path(c["thumb"]).exists():
            # превью нет (старый клип или сбой) — делаем кадр из самого клипа и запоминаем
            if not c["path"] or not Path(c["path"]).exists():
                raise HTTPException(404, "Нет превью")
            t = make_thumb(c["path"], wd / "thumbs" / f"clip{clip_id}.png", height=240)
            if not t:
                raise HTTPException(404, "Не удалось сделать превью")
            d.update_clip(clip_id, thumb=str(t))
            return FileResponse(t, media_type="image/png")
        return FileResponse(c["thumb"], media_type="image/png")

    @app.post("/api/clips/{clip_id}/preview")
    def clip_preview(clip_id: int, d: DB = Depends(get_db)):
        c = need_clip(d, clip_id)
        if not bn.specs_from_json(c["banner"]) and not filler_mod.normalize(c["filler"]):
            raise HTTPException(400, "У клипа нет ни баннера, ни видео-вставки")

        def fn(log):
            db2 = DB(cfg["paths"]["db"], init=False)
            bn.ensure_final(db2, db2.clip(clip_id), cfg, log)
            log("Готово.")
        return started("preview", "Предпросмотр с баннером и вставкой", fn)

    # ---------- прокси ----------
    def proxy_json(r) -> dict:
        u = urlsplit(r["url"])
        return {"id": r["id"], "url": px.mask(r["url"]), "scheme": u.scheme, "host": u.hostname, "port": u.port,
                "status": r["status"], "ip": r["ip"], "country": r["country"], "code": r["code"], "city": r["city"],
                "isp": r["isp"], "ms": r["ms"], "error": r["error"], "checked_at": r["checked_at"],
                "active": bool(r["active"])}

    def check_task(ids: list[int]):
        def fn(log):
            db2 = DB(cfg["paths"]["db"], init=False)
            from concurrent.futures import ThreadPoolExecutor
            rows = [r for r in (db2.proxy(i) for i in ids) if r]
            ok = 0
            with ThreadPoolExecutor(max_workers=8) as ex:
                for r, res in zip(rows, ex.map(lambda r: px.check_proxy(r["url"]), rows)):
                    db2.set_proxy_result(r["id"], res)
                    ok += res["ok"]
                    log(f"{px.mask(r['url'])}: " + (f"работает, IP {res['ip']} {res['country']} {res['city']}, {res['ms']} мс" if res["ok"] else f"не работает — {res['error']}"))
            return {"checked": len(rows), "ok": ok}
        return started("proxycheck", f"Проверка прокси ({len(ids)})", fn, exclusive=True)

    @app.get("/api/proxies")
    def proxies(d: DB = Depends(get_db)):
        return [proxy_json(r) for r in d.proxies()]

    @app.post("/api/proxies")
    def add_proxies(body: ProxyAddIn, d: DB = Depends(get_db)):
        scheme = body.scheme if body.scheme in px.SCHEMES else "http"
        good, bad = px.parse_proxies(body.text, scheme)
        if not good:
            raise HTTPException(400, "Не нашёл ни одного прокси. Примеры: 1.2.3.4:8080, user:pass@1.2.3.4:8080, "
                                     "1.2.3.4:8080:user:pass, socks5://1.2.3.4:1080")
        new = [i for i in (d.add_proxy(p["url"]) for p in good) if i]
        task = check_task(new) if new and not tasks.running("proxycheck") else None
        return {"added": len(new), "duplicates": len(good) - len(new), "bad": bad, "task": task}

    @app.post("/api/proxies/check")
    def check_proxies(ids: Optional[list[int]] = None, d: DB = Depends(get_db)):
        ids = ids or [r["id"] for r in d.proxies()]
        if not ids:
            raise HTTPException(400, "Список прокси пуст")
        return check_task(ids)

    @app.post("/api/proxies/{proxy_id}/use")
    def use_proxy(proxy_id: int, body: ProxyUseIn, d: DB = Depends(get_db)):
        r = d.proxy(proxy_id)
        if not r:
            raise HTTPException(404, "Прокси не найден")
        if body.on and r["status"] != "ok":
            raise HTTPException(400, "Сначала проверь прокси — выбрать можно только рабочий")
        d.set_active_proxy(proxy_id if body.on else None)
        return {"ok": True}

    @app.delete("/api/proxies/{proxy_id}")
    def del_proxy(proxy_id: int, d: DB = Depends(get_db)):
        if not d.proxy(proxy_id):
            raise HTTPException(404, "Прокси не найден")
        d.delete_proxies([proxy_id])
        return {"ok": True}

    @app.delete("/api/proxies")
    def del_bad_proxies(bad: bool = True, d: DB = Depends(get_db)):
        ids = [r["id"] for r in d.proxies() if (r["status"] == "bad" or not bad)]
        d.delete_proxies(ids)
        return {"deleted": len(ids)}

    # ---------- пресеты ----------
    def preset_json(r) -> dict:
        data = json.loads(r["data"] or "{}")
        banners = data.get("banners") or ([data["banner"]] if data.get("banner") else [])
        return {"id": r["id"], "name": r["name"], "banner": banners[0] if banners else None, "banners": banners,
                "tags": data.get("tags", ""), "footer": data.get("footer", "")}

    def clean_banners(raw: list[dict] | None) -> list[dict]:
        return [{**{k: v for k, v in sp.items() if k != "path"}, "name": Path(sp["path"]).name} for sp in clean_specs(raw)]

    @app.get("/api/presets")
    def presets(d: DB = Depends(get_db)):
        return [preset_json(r) for r in d.presets()]

    @app.post("/api/presets")
    def save_preset(body: PresetIn, d: DB = Depends(get_db)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Дай пресету название")
        banners = clean_banners(body.banners if body.banners is not None else ([body.banner] if body.banner else []))
        data = {"banners": banners, "banner": banners[0] if banners else None, "tags": normalize_tags(body.tags),
                "footer": body.footer.strip()}
        if not (data["banners"] or data["tags"] or data["footer"]):
            raise HTTPException(400, "В пресете пусто: выбери баннер, теги или текст в конце описания")
        if body.id:
            if not d.preset(body.id):
                raise HTTPException(404, "Пресет не найден")
            if any(r["id"] != body.id and r["name"].lower() == name.lower() for r in d.presets()):
                raise HTTPException(400, f"Пресет «{name}» уже есть")
            d.update_preset(body.id, name, data)
            return preset_json(d.preset(body.id))
        return preset_json(d.preset(d.save_preset(name, data)))

    @app.delete("/api/presets/{preset_id}")
    def del_preset(preset_id: int, d: DB = Depends(get_db)):
        if not d.preset(preset_id):
            raise HTTPException(404, "Пресет не найден")
        d.delete_preset(preset_id)
        return {"ok": True}

    def preset_banner_spec(pr: dict) -> str | None:
        """JSON баннеров пресета для клипа; ValueError — если файла баннера уже нет в библиотеке."""
        if not pr["banners"]:
            return None
        lib = library()
        for sp in pr["banners"]:
            if sp["name"] not in lib:
                raise ValueError(f"Файл баннера «{sp['name']}» из пресета «{pr['name']}» удалён — сохрани пресет заново")
        return bn.specs_to_json([bn.normalize_spec({**sp, "path": str(lib[sp["name"]])}, cfg) for sp in pr["banners"]])

    def apply_preset_to(d: DB, pr: dict, banner_spec: str | None, clip_ids: list[int]) -> list[int]:
        done = []
        for cid in clip_ids:
            c = d.clip(cid)
            if not c or c["status"] != "ready":
                continue
            upd = {}
            if banner_spec:
                upd["banner"] = banner_spec
            if pr["tags"]:
                upd["tags"] = normalize_tags((c["tags"] or "") + " " + pr["tags"])
            if pr["footer"] and pr["footer"] not in (c["reason"] or ""):
                upd["reason"] = ((c["reason"] or "").rstrip() + "\n\n" + pr["footer"]).strip()
            if upd:
                d.update_clip(cid, **upd)
            done.append(cid)
        return done

    @app.post("/api/presets/{preset_id}/apply")
    def apply_preset(preset_id: int, body: ApplyIn, d: DB = Depends(get_db)):
        """Баннер заменяется, теги добавляются к имеющимся, текст дописывается в конец описания (один раз)."""
        r = d.preset(preset_id)
        if not r:
            raise HTTPException(404, "Пресет не найден")
        pr = preset_json(r)
        try:
            spec = preset_banner_spec(pr)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return [clip_json(d.clip(cid), d) for cid in apply_preset_to(d, pr, spec, body.clip_ids)]

    # ---------- шаблоны публикации ----------
    def template_json(r, d: DB) -> dict:
        items = []
        for it in json.loads(r["data"] or "{}").get("items", []):
            g = d.group(it["group_id"])
            pr = d.preset(it["preset_id"]) if it.get("preset_id") else None
            items.append({"group_id": it["group_id"], "group": g["name"] if g else "", "preset_id": it.get("preset_id"), "tags": it.get("tags", ""),
                          "preset": pr["name"] if pr else "", "broken": not g or (bool(it.get("preset_id")) and not pr)})
        return {"id": r["id"], "name": r["name"], "tags": json.loads(r["data"] or "{}").get("tags", ""), "items": items,
                "broken": any(i["broken"] for i in items)}

    @app.get("/api/templates")
    def templates(d: DB = Depends(get_db)):
        return [template_json(r, d) for r in d.templates()]

    @app.post("/api/templates")
    def save_template(body: TemplateIn, d: DB = Depends(get_db)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Дай шаблону название")
        if not body.items:
            raise HTTPException(400, "Добавь в шаблон хотя бы одну группу")
        seen = set()
        for it in body.items:
            need_group(d, it.group_id)
            if it.group_id in seen:
                raise HTTPException(400, "Одна группа указана в шаблоне дважды")
            seen.add(it.group_id)
            if it.preset_id and not d.preset(it.preset_id):
                raise HTTPException(400, "Пресет баннеров не найден — выбери другой")
        if body.id and not d.template(body.id):
            raise HTTPException(404, "Шаблон не найден")
        other = next((t for t in d.templates() if t["name"].lower() == name.lower() and t["id"] != body.id), None)
        if other:
            raise HTTPException(400, f"Шаблон «{name}» уже есть")
        tid = d.save_template(name, {"tags": normalize_tags(body.tags),
                                     "items": [{"group_id": i.group_id, "preset_id": i.preset_id or None, "tags": normalize_tags(i.tags)} for i in body.items]}, body.id)
        return template_json(d.template(tid), d)

    @app.delete("/api/templates/{tpl_id}")
    def del_template(tpl_id: int, d: DB = Depends(get_db)):
        if not d.template(tpl_id):
            raise HTTPException(404, "Шаблон не найден")
        d.delete_template(tpl_id)
        return {"ok": True}

    def resolve_target(d: DB, group_id: int, template_id: int | None):
        """(группа задания, план по группам [(group_id, пресет|None, banner_spec)]). Без шаблона план пуст."""
        if not template_id:
            need_group(d, group_id)
            return group_id, []
        r = d.template(template_id)
        if not r:
            raise HTTPException(404, "Шаблон не найден")
        plan = []
        tdata = json.loads(r["data"] or "{}")
        for it in tdata.get("items", []):
            if not d.group(it["group_id"]):
                raise HTTPException(400, "В шаблоне есть удалённая группа — отредактируй шаблон")
            pr = None
            if it.get("preset_id"):
                pre = d.preset(it["preset_id"])
                if not pre:
                    raise HTTPException(400, "В шаблоне есть удалённый пресет баннеров — отредактируй шаблон")
                pr = preset_json(pre)
                try:
                    preset_banner_spec(pr)
                except ValueError as e:
                    raise HTTPException(400, str(e)) from None
            plan.append((it["group_id"], pr, normalize_tags((tdata.get("tags", "") + " " + it.get("tags", "")).strip())))
        if not plan:
            raise HTTPException(400, "Шаблон пуст")
        return plan[0][0], plan

    def fan_out(d: DB, job_id: int, plan: list, log=print):
        """Клипы задания копируются в остальные группы шаблона; каждой группе — её пресет баннеров (баннер, теги, подпись)."""
        if not plan:
            return
        base = [c["id"] for c in d.clips(job_id)]
        by_group = {plan[0][0]: list(base)}
        for gid, _, _t in plan[1:]:
            ids = []
            for cid in base:
                c = d.clip(cid)
                src, th = Path(c["path"]), Path(c["thumb"]) if c["thumb"] else None
                dst = src.with_name(f"{src.stem}_g{gid}{src.suffix}")
                shutil.copy2(src, dst)
                filler_mod.copy_sidecar(src, dst)
                tdst = ""
                if th and th.exists():
                    t2 = th.with_name(f"{th.stem}_g{gid}{th.suffix}")
                    shutil.copy2(th, t2)
                    tdst = str(t2)
                ids.append(d.copy_clip(cid, str(dst), tdst, gid))
            by_group[gid] = ids
        for gid, pr, extra in plan:
            ids = by_group[gid]
            for cid in ids:
                d.update_clip(cid, group_id=gid)
            g = d.group(gid)
            if pr:
                apply_preset_to(d, pr, preset_banner_spec(pr), ids)
            if extra:  # хэштеги шаблона ставим первыми: при лимите в 15 тегов обрезаются остальные, а не они
                for cid in ids:
                    d.update_clip(cid, tags=normalize_tags(extra + " " + (d.clip(cid)["tags"] or "")))
            log(f"Группа «{g['name']}»: {len(ids)} клип(ов)" + (f", пресет «{pr['name']}»" if pr else "") + (f", теги {extra}" if extra else ""))

    # ---------- папки и файлы ----------
    VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".flv", ".ts", ".mpg", ".mpeg", ".wmv"}

    def need_folder(key: str) -> Path:
        if key not in FOLDER_TITLES:
            raise HTTPException(400, "Неизвестная папка")
        return folder(cfg, key)

    def safe_file(key: str, name: str) -> Path:
        root = need_folder(key).resolve()
        p = (root / name).resolve()
        if root not in p.parents or p.suffix.lower() not in VIDEO_EXT or not p.is_file():
            raise HTTPException(404, "Файл не найден")
        return p

    @app.get("/api/folders")
    def folders():
        return [{"key": k, "title": t, "path": str(need_folder(k))} for k, t in FOLDER_TITLES.items()]

    @app.get("/api/files")
    def files(folder_key: str, d: DB = Depends(get_db)):
        root = need_folder(folder_key).resolve()
        used = {str(Path(r["path"]).resolve()) for r in d.c.execute("SELECT path FROM clips WHERE path!=''")}
        out = []
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in VIDEO_EXT and len(p.relative_to(root).parts) <= 3:
                st = p.stat()
                out.append({"name": p.relative_to(root).as_posix(), "size": st.st_size, "mtime": st.st_mtime,
                            "in_editor": str(p.resolve()) in used})
        out.sort(key=lambda x: -x["mtime"])
        return out[:300]

    @app.delete("/api/files")
    def delete_file(folder_key: str, name: str, d: DB = Depends(get_db)):
        """Удалить видео из папки. Если оно в очереди редактора — клип убирается из очереди; кэш транскрипта удаляется тоже."""
        p = safe_file(folder_key, name)
        if tasks.running("process", "publish"):
            raise HTTPException(409, "Идёт нарезка или публикация — удалять файлы сейчас нельзя, дождись завершения")
        rp = str(p.resolve())
        for r in d.c.execute("SELECT id, path, final_path, filler_path FROM clips WHERE status='ready'").fetchall():
            if r["path"] and str(Path(r["path"]).resolve()) == rp:
                for extra in (r["final_path"], r["filler_path"]):
                    if extra:
                        Path(extra).unlink(missing_ok=True)
                d.update_clip(r["id"], status="discarded", path="", final_path="", filler_path="")
        for j in d.c.execute("SELECT id FROM jobs WHERE source_path!=''").fetchall():
            if str(Path(d.job(j["id"])["source_path"]).resolve()) == rp:
                d.clear_source(j["id"])
        freed = p.stat().st_size
        p.unlink()
        for extra in p.parent.glob(glob.escape(p.stem) + ".*"):   # кэш транскрипта и отметка источника
            if extra.suffix in (".json", ".src"):
                extra.unlink(missing_ok=True)
        return {"ok": True, "freed": freed}

    @app.post("/api/files/rename")
    def rename_file(body: RenameIn, d: DB = Depends(get_db)):
        """Переименовать видео в папке. Расширение сохраняется; кэш, отметка источника и записи в базе обновляются."""
        p = safe_file(body.folder, body.name)
        if tasks.running("process", "publish"):
            raise HTTPException(409, "Идёт нарезка или публикация — переименовывать файлы сейчас нельзя")
        typed = body.new_name.strip()
        if typed.lower().endswith(p.suffix.lower()):
            typed = typed[:-len(p.suffix)]
        stem = safe_name(typed)
        if not stem:
            raise HTTPException(400, "Название не может быть пустым")
        new = p.with_name(stem + p.suffix)
        if new == p:
            return {"name": new.relative_to(need_folder(body.folder)).as_posix()}
        if new.exists():
            raise HTTPException(409, "Файл с таким названием уже есть")
        old_stem, moves = p.stem, [(p, new)]
        for extra in p.parent.glob(glob.escape(old_stem) + ".*"):
            rest = extra.name[len(old_stem):]
            if extra != p and (rest == ".src" or rest.startswith(".transcript.") or rest == ".final" + p.suffix):
                moves.append((extra, p.with_name(stem + rest)))
        if any(dst.exists() for _, dst in moves[1:]):
            raise HTTPException(409, "Рядом уже лежат файлы с таким названием")
        for src, dst in moves:
            src.rename(dst)
        mp = {str(src.resolve()): str(dst) for src, dst in moves}
        for r in d.c.execute("SELECT id, path, final_path, filler_path FROM clips").fetchall():
            upd = {}
            for key in ("path", "final_path", "filler_path"):
                if r[key] and str(Path(r[key]).resolve()) in mp:
                    upd[key] = mp[str(Path(r[key]).resolve())]
            if upd:
                d.update_clip(r["id"], **upd)
        for j in d.c.execute("SELECT id, source_path FROM jobs WHERE source_path!=''").fetchall():
            if str(Path(j["source_path"]).resolve()) in mp:
                d.c.execute("UPDATE jobs SET source_path=? WHERE id=?", (mp[str(Path(j["source_path"]).resolve())], j["id"]))
        d.c.commit()
        return {"name": new.relative_to(need_folder(body.folder)).as_posix()}

    @app.post("/api/folders/{key}/open")
    def open_folder(key: str):
        import os, subprocess, sys
        p = need_folder(key)
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])
        except Exception as e:
            raise HTTPException(500, f"Не удалось открыть папку: {e}") from None
        return {"ok": True}

    @app.post("/api/files/slice")
    def file_slice(body: FileIn, d: DB = Depends(get_db)):
        """Нарезать клипы из видеофайла, лежащего в папке исходников или тестов."""
        gid, plan = resolve_target(d, body.group_id, body.template_id)
        src = safe_file(body.folder, body.name)
        cut = cut_args(body)

        def fn(log):
            db2 = DB(cfg["paths"]["db"], init=False)
            job_id, clips_ = run_pipeline(str(src), eff_cfg(db2, log), db2, gid, log, test=body.test, **cut)
            fan_out(db2, job_id, plan, log)
            return {"job_id": job_id, "clips": len(clips_) * max(1, len(plan))}
        return started("process", f"{'Тест: ' if body.test else 'Нарезка: '}{src.name}", fn, exclusive=True)

    @app.post("/api/files/to-editor")
    def file_to_editor(body: FileIn, d: DB = Depends(get_db)):
        """Готовый вертикальный клип из папки добавить в редактор (название, баннер, публикация)."""
        need_group(d, body.group_id)
        src = safe_file(body.folder, body.name)
        try:
            dur = bn.probe(src)["duration"]
        except bn.BannerError as e:
            raise HTTPException(400, f"Файл не распознан как видео: {e}") from None
        from ..editor import make_thumb
        from ..models import Moment
        job_id = d.add_job(body.group_id, "", src.stem, "", test=body.test or body.folder == "test")
        thumb = make_thumb(src, wd / "thumbs" / f"file{job_id}.png", height=240)
        spec = bn.config_spec(cfg)
        cid = d.add_clip(job_id, str(src), Moment(0, dur, src.stem[:100], "", 0),
                         banner=json.dumps(spec, ensure_ascii=False) if spec else "", thumb=str(thumb or ""))
        return clip_json(d.clip(cid), d)

    # ---------- баннеры ----------
    @app.get("/api/banners")
    def banners():
        out = []
        for name, p in library().items():
            try:
                info = bn.probe(p)
            except bn.BannerError:
                continue
            key, box = None, None
            if info["kind"] == "video":
                try:
                    key = bn.detect_key(p)
                    box = bn.content_box(p, key) if key else None
                except Exception:   # noqa: BLE001 — хромакей необязателен, список баннеров не должен из-за него падать
                    key, box = None, None
            out.append({"name": name, "kind": info["kind"], "has_audio": info["has_audio"],
                        "duration": round(info["duration"], 1), "width": info["w"], "height": info["h"],
                        "size": p.stat().st_size, "key": key, "box": box})
        return out

    # ---------- видео-вставки (библиотека) ----------
    @app.get("/api/fillers")
    def fillers_list():
        out = []
        for p in filler_mod.library(cfg):
            try:
                info = bn.probe(p)
            except bn.BannerError:
                continue
            out.append({"name": p.name, "duration": round(info["duration"], 1), "width": info["w"], "height": info["h"], "size": p.stat().st_size})
        return {"dir": str(filler_mod.library_dir(cfg)), "files": out}

    @app.get("/api/fillers/file")
    def fillers_file(name: str):
        p = filler_mod.library_dir(cfg) / Path(name).name
        if p.suffix.lower() not in filler_mod.VIDEO_EXT or not p.is_file():
            raise HTTPException(404, "Файл не найден")
        return FileResponse(p, media_type="video/mp4")

    @app.put("/api/fillers")
    async def fillers_upload(request: Request, name: str):
        base = re.sub(r"[^\w.\- ]", "_", Path(name).name).strip()
        if Path(base).suffix.lower() not in filler_mod.VIDEO_EXT:
            raise HTTPException(400, "Допустимы видео: mp4, mov, mkv, webm, m4v")
        folder = filler_mod.library_dir(cfg)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / base
        tmp = dest.with_suffix(dest.suffix + ".part")
        size = 0
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BANNER_BYTES:
                    f.close(); tmp.unlink(missing_ok=True)
                    raise HTTPException(413, "Файл больше 500 МБ")
                f.write(chunk)
        tmp.replace(dest)
        try:
            info = bn.probe(dest)
        except bn.BannerError as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"Файл не распознан как видео: {e}") from None
        return {"name": dest.name, "duration": round(info["duration"], 1)}

    @app.delete("/api/fillers")
    def fillers_delete(name: str):
        p = filler_mod.library_dir(cfg) / Path(name).name
        if not p.is_file() or p.suffix.lower() not in filler_mod.VIDEO_EXT:
            raise HTTPException(404, "Файл не найден")
        p.unlink()
        return {"ok": True}

    @app.put("/api/banners")
    async def upload_banner(request: Request, name: str):
        base = re.sub(r"[^\w.\- ]", "_", Path(name).name).strip()
        if Path(base).suffix.lower() not in BANNER_EXT:
            raise HTTPException(400, "Допустимы картинки (png, jpg, webp) и видео (mp4, mov, mkv, webm)")
        dest = wd / "banners" / base
        tmp = dest.with_suffix(dest.suffix + ".part")
        size = 0
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BANNER_BYTES:
                    f.close(); tmp.unlink(missing_ok=True)
                    raise HTTPException(413, "Файл больше 500 МБ")
                f.write(chunk)
        tmp.replace(dest)
        try:
            info = bn.probe(dest)
        except bn.BannerError as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"Файл не распознан как картинка или видео: {e}") from None
        return {"name": dest.name, "kind": info["kind"], "has_audio": info["has_audio"],
                "duration": round(info["duration"], 1), "width": info["w"], "height": info["h"]}

    @app.get("/api/banners/describe")
    def banner_describe(name: str, start: float = 3.0, duration: float = 0.0, width_percent: float = 100.0,
                        position: str = "center", duck_percent: int = 15, margin: int = 160,
                        chroma: bool | None = None, chroma_color: str = ""):
        p = library().get(name)
        if not p:
            raise HTTPException(404, "Баннер не найден")
        try:
            spec = bn.normalize_spec({"path": str(p), "start": start, "duration": duration, "width_percent": width_percent,
                                      "position": position, "duck_percent": duck_percent, "margin": margin,
                                      "chroma": chroma, "chroma_color": chroma_color}, cfg)
            return bn.describe_info(spec, cfg)
        except bn.BannerError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/banners/file")
    def banner_file(name: str):
        p = library().get(name)
        if not p:
            raise HTTPException(404, "Баннер не найден")
        return FileResponse(p)

    @app.delete("/api/banners")
    def banner_delete(name: str):
        p = (wd / "banners" / Path(name).name)
        if not p.is_file():
            raise HTTPException(404, "Баннер не найден")
        p.unlink()
        return {"ok": True}

    # ---------- публикация ----------
    @app.post("/api/publish")
    def publish(body: PublishIn, d: DB = Depends(get_db)):
        need_group(d, body.group_id)
        targets = []
        if body.account_ids:
            for i in dict.fromkeys(body.account_ids):
                a = d.account(i)
                if not a:
                    raise HTTPException(404, "Аккаунт не найден")
                if a["platform"] not in REAL_PLATFORMS:
                    raise HTTPException(400, f"Публикация на {a['platform']} пока не поддерживается")
                targets.append(i)
        elif not d.accounts(body.group_id):
            raise HTTPException(400, "В группе нет привязанных аккаунтов")
        pending = {c["id"] for c in d.pending_clips(body.group_id)}
        test = {i for i in pending if (d.job(d.clip(i)["job_id"]) or {"test": 0})["test"]}
        asked = body.clip_ids if body.clip_ids else sorted(pending)
        ids = [i for i in asked if i in pending and i not in test]
        if not ids:
            raise HTTPException(400, "Тестовые клипы не публикуются" if any(i in test for i in asked) else "Нет клипов для публикации")
        if body.mode == "now":
            for i in ids:
                d.update_clip(i, publish_at="")
        keep_sources = tasks.running("process")

        def fn(log):
            return service.publish_clips(DB(cfg["paths"]["db"], init=False), cfg, body.group_id, ids, log,
                                         delete_sources=not keep_sources, account_ids=targets or None)
        return started("publish", f"Публикация: {len(ids)} клип(ов)", fn, exclusive=True)

    @app.post("/api/clips/move")
    def move_clips(body: MoveClipsIn, d: DB = Depends(get_db)):
        """Переносит несколько клипов в другую группу (вопрос «применить ко всем клипам группы» в редакторе)."""
        need_group(d, body.group_id)
        ids = [i for i in dict.fromkeys(body.clip_ids) if d.clip(i) and d.clip(i)["status"] == "ready"]
        for i in ids:
            d.update_clip(i, group_id=body.group_id)
        return {"moved": len(ids)}

    @app.post("/api/publish/batch")
    def publish_batch(body: BatchPublishIn, d: DB = Depends(get_db)):
        """Публикация выбранных клипов в заданном порядке с промежутком между выходами.
        Время i-го клипа = start + i·interval (start пусто → первый сразу, остальные от текущего момента)."""
        from datetime import datetime, timedelta, timezone
        order = list(dict.fromkeys(body.clip_ids))
        if not order:
            raise HTTPException(400, "Не выбрано ни одного клипа")
        interval = float(body.interval_minutes or 0)
        min_gap = schedule.MIN_AHEAD.total_seconds() / 60
        if interval < 0 or (0 < interval < min_gap):
            raise HTTPException(400, f"Промежуток между выходами: 0 (все сразу) или не меньше {int(min_gap)} минут")
        now = datetime.now(timezone.utc)
        start = None
        if (body.start or "").strip():
            start = schedule.from_iso(body.start.strip())
            if not start:
                raise HTTPException(400, "Неверный формат времени начала")
            if start < now + schedule.MIN_AHEAD:
                raise HTTPException(400, f"Время начала должно быть минимум на {int(min_gap)} минут позже текущего")
        targets = []
        for i in dict.fromkeys(body.account_ids or []):
            a = d.account(i)
            if not a:
                raise HTTPException(404, "Аккаунт не найден")
            if a["platform"] not in REAL_PLATFORMS:
                raise HTTPException(400, f"Публикация на {a['platform']} пока не поддерживается")
            targets.append(i)
        if body.filler is not None:
            fj = filler_json(body.filler)
            for cid in order:
                if d.clip(cid):
                    d.update_clip(cid, filler=fj)
        groups: dict[int, list[int]] = {}
        for pos, cid in enumerate(order):
            c = d.clip(cid)
            if not c or c["status"] != "ready":
                raise HTTPException(404, f"Клип {cid} не найден или уже опубликован")
            if (d.job(c["job_id"]) or {"test": 0})["test"]:
                raise HTTPException(400, "Тестовые клипы не публикуются")
            gid = d.clip_group(c)
            if not gid or (not targets and not d.accounts(gid)):
                raise HTTPException(400, f"У клипа «{c['title']}» группа без аккаунтов")
            groups.setdefault(gid, []).append(cid)
        for pos, cid in enumerate(order):
            if start is not None:
                when = start + timedelta(minutes=interval * pos)
            elif interval and pos > 0:
                when = now + timedelta(minutes=interval * pos)
            else:
                when = None
            d.update_clip(cid, publish_at=schedule.to_iso(when) if when else "")
        keep_sources = tasks.running("process")
        if body.privacy and body.privacy not in ("public", "unlisted", "private"):
            raise HTTPException(400, "Видимость: public, unlisted или private")
        # время каждого клипа уже задано выше, поэтому общая «задержка между клипами» из config.toml здесь не нужна:
        # иначе при «все сразу» клипы со второго уходили бы с задержкой из конфига
        run_cfg = {**cfg, "youtube": {**cfg["youtube"], "delay_minutes": 0, **({"privacy": body.privacy} if body.privacy else {})}}

        def fn(log):
            total = {"published": 0, "errors": 0, "scheduled": 0, "left": 0, "limit": False, "total": len(order)}
            db2 = DB(cfg["paths"]["db"], init=False)
            for gid, ids in groups.items():
                r = service.publish_clips(db2, run_cfg, gid, ids, log, delete_sources=not keep_sources, account_ids=targets or None)
                total["published"] += r.get("published", 0)
                total["errors"] += r.get("errors", 0)
                total["scheduled"] += r.get("scheduled", 0)
                total["left"] += r.get("left", 0)
                if r.get("limit"):
                    total["limit"] = True
                    break
            total["left"] = sum(1 for i in order if (d_ := db2.clip(i)) and d_["status"] == "ready")
            return total
        return started("publish", f"Публикация: {len(order)} клип(ов)", fn, exclusive=True)

    # ---------- очередь отложенных публикаций (Instagram/TikTok) ----------
    @app.get("/api/scheduled")
    def scheduled(d: DB = Depends(get_db)):
        return [{"id": r["id"], "clip": r["clip_title"] or "", "platform": r["platform"] or "", "account": r["label"] or "",
                 "group": r["group_name"] or "", "run_at": r["run_at"], "status": r["status"], "error": r["error"]}
                for r in d.deferred_list()]

    @app.delete("/api/scheduled/{did}")
    def scheduled_cancel(did: int, d: DB = Depends(get_db)):
        d.delete_deferred(did)
        return {"ok": True}

    @app.post("/api/scheduled/{did}/retry")
    def scheduled_retry(did: int, d: DB = Depends(get_db)):
        d.set_deferred(did, "wait")
        return {"ok": True}

    sched_lock = threading.Lock()

    stats_last = {"t": 0.0}

    def scheduler_loop():
        while True:
            time.sleep(30)
            # раз в 20 минут тихо обновляем статистику YouTube (1 ед. квоты на 50 видео), чтобы история не была пустой
            # а если у запланированного видео время выхода уже прошло — не раньше чем через 3 минуты, чтобы статус сменился на «Опубликовано»
            if not tasks.running("publish", "stats") and (time.time() - stats_last["t"] > 20 * 60 or (
                    time.time() - stats_last["t"] > 3 * 60 and service.has_stale_scheduled(DB(cfg["paths"]["db"], init=False)))):
                stats_last["t"] = time.time()
                try:
                    db_s = DB(cfg["paths"]["db"], init=False)
                    if db_s.publications("youtube"):
                        service.refresh_stats(db_s, cfg, lambda m: None)
                except Exception as e:  # нет входа/сети — не страшно, обновится вручную
                    print("[статистика] пропущено:", str(e)[:200])
            if not sched_lock.acquire(blocking=False):
                continue
            try:
                if not tasks.running("publish"):
                    service.run_due(DB(cfg["paths"]["db"], init=False), cfg, lambda m: print("[очередь]", m))
            except Exception as e:  # планировщик не должен падать
                print("[очередь] ошибка:", e)
            finally:
                sched_lock.release()

    @app.on_event("startup")
    def _start_scheduler():
        threading.Thread(target=scheduler_loop, daemon=True, name="narez-scheduler").start()

    # ---------- история ----------
    @app.get("/api/history")
    def history(d: DB = Depends(get_db)):
        out = []
        for p in d.publications():
            # дата на канале: время выхода (у отложенных — назначенное), а не момент отправки из приложения
            up = (p["publish_at"] or p["uploaded_at"] or "").replace(" ", "T")
            out.append({"id": p["id"], "title": p["title"], "group": p["group_name"] or "", "group_id": p["group_id"],
                        "account": p["account_label"] or p["platform"], "platform": p["platform"], "url": p["url"],
                        "status": service.pub_status(p), "uploaded_at": up + "Z" if up and not re.search(r"(Z|[+-]\d\d:?\d\d)$", up) else up,
                        "publish_at": p["publish_at"] or "", "views": p["views"], "likes": p["likes"],
                        "comments": p["comments"], "avg_pct": p["avg_pct"], "avg_sec": p["avg_sec"], "has_thumb": bool(p["thumb"]) and Path(p["thumb"]).exists()})
        return out

    @app.get("/api/history/{pub_id}/thumb")
    def history_thumb(pub_id: int, d: DB = Depends(get_db)):
        p = d.publication(pub_id)
        if not p or not p["thumb"] or not Path(p["thumb"]).exists():
            raise HTTPException(404, "Нет превью")
        return FileResponse(p["thumb"], media_type="image/png")

    @app.post("/api/history/refresh")
    def history_refresh():
        def fn(log):
            from ..publishers.youtube import ScopeError
            try:
                return {"updated": service.refresh_stats(DB(cfg["paths"]["db"], init=False), cfg, log)}
            except ScopeError as e:
                raise RuntimeError(str(e)) from None
        return started("stats", "Обновление статистики", fn, exclusive=True)

    @app.post("/api/history/import")
    def history_import(body: ImportIn, d: DB = Depends(get_db)):
        need_group(d, body.group_id)
        accs = [a for a in d.accounts(body.group_id) if a["platform"] == "youtube"]
        if not accs:
            raise HTTPException(400, "В группе нет аккаунта YouTube")
        ids = parse_video_ids(body.urls)
        if not ids:
            raise HTTPException(400, "Не нашёл ни одной ссылки на видео YouTube")
        acc = accs[0]["id"]

        def fn(log):
            from ..publishers.youtube import ScopeError
            try:
                n = import_videos(DB(cfg["paths"]["db"], init=False), cfg, get_publisher("youtube", cfg), acc,
                                  body.group_id, ids, log)
            except ScopeError as e:
                raise RuntimeError(str(e)) from None
            log(f"Добавлено видео: {n}.")
            return {"added": n}
        return started("import", "Импорт видео в историю", fn, exclusive=True)

    # ---------- страница ----------
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", NoCacheStatic(directory=STATIC), name="static")
    return app
