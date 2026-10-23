"""Веб-интерфейс narez: FastAPI на localhost. Запуск: python run.py"""
from __future__ import annotations

import glob
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlsplit

import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import banner as bn
from .. import filler as filler_mod
from .. import stickers as stk
from .. import schedule, service
from ..cleanup import discard_clip, sweep_sources
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
    reddit: bool = False               # режим «Реддит» (только moments): клипы из 2–3 историй с комментариями, 60–180 с
    trim_end: float = 15.0             # сколько секунд отрезать от конца исходника перед нарезкой (0 = не отрезать)
    subs_fallback: str = "youtube"     # если ручных субтитров YouTube нет: youtube = взять автоматические, whisper = распознать речь


def cut_args(b: CutIn) -> dict:
    if b.audio not in ("ru", "original"):
        raise HTTPException(400, "Звук: ru или original")
    if b.subs_fallback not in ("youtube", "whisper"):
        raise HTTPException(400, "Субтитры: youtube или whisper")
    if not 0 <= b.trim_end <= 120:
        raise HTTPException(400, "Сколько отрезать от конца исходника — от 0 до 120 секунд")
    if b.mode not in ("moments", "full"):
        raise HTTPException(400, "Режим нарезки: moments или full")
    if b.mode == "full":
        if not 5 <= b.length <= 180:
            raise HTTPException(400, "Длина клипа — от 5 до 180 секунд")
        if not 0 <= b.overlap <= min(30, b.length - 5):
            raise HTTPException(400, "Нахлёст — от 0 секунд и не больше длины клипа минус 5 секунд")
    return {"mode": b.mode, "length": b.length, "overlap": b.overlap, "audio": b.audio,
            "adcut_sb": b.adcut_sb, "adcut_text": b.adcut_text, "subs_fallback": b.subs_fallback,
            "reddit": bool(b.reddit and b.mode == "moments"), "trim_end": b.trim_end}


class ProcessIn(CutIn):
    url: str
    group_id: int = 0
    test: bool = False
    template_id: Optional[int] = None   # шаблон: клипы уходят во все его группы, каждая со своим пресетом баннеров
    tiktok: bool = False                # клипы для ручной выкладки в TikTok: без баннера, группа и шаблон не нужны


class QueueIn(CutIn):
    urls: list[str]
    group_id: int = 0
    test: bool = False
    template_id: Optional[int] = None
    tiktok: bool = False


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


class TikTokAccountIn(BaseModel):
    label: str
    login: str = ""
    password: Optional[str] = None      # None/пусто при правке — оставить прежний пароль
    note: str = ""


class StickerPresetIn(BaseModel):
    id: Optional[int] = None
    name: str
    files: list[str] = []          # пусто — случайные из всей папки
    count: int = 3
    y_min: float = 50
    y_max: float = 95
    size_mm: float = 4
    rotate: bool = True


class TikTokPresetIn(BaseModel):
    preset_id: Optional[int] = None            # пресет баннеров
    sticker_preset_id: Optional[int] = None    # пресет стикеров
    clip_ids: Optional[list[int]] = None       # к каким клипам применить (None — ко всем)


class TikTokFillerIn(BaseModel):
    clip_ids: Optional[list[int]] = None
    position: str = ""        # top / bottom — поставить вставку; "" — убрать
    file: str = ""            # "" — случайное видео из папки вставок


class TikTokIdsIn(BaseModel):
    clip_ids: Optional[list[int]] = None


class ClipboardIn(BaseModel):
    items: list[str]
    delay: float = 1.5


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
    sticker_preset_id: Optional[int] = None
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
    stickers: Optional[dict] = None           # пусто/{} — убрать стикеры (задать раскладку можно только пресетом)
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
                "stickers": stk.normalize(c["stickers"]),
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
    def make_process_job(body: ProcessIn, d: DB):
        """(название, fn(log)) для задачи нарезки; ошибки настроек — HTTPException. Общее для «Нарезать» и очереди ссылок."""
        if body.tiktok:
            gid, plan = None, []
        else:
            gid, plan = resolve_target(d, body.group_id, body.template_id)
        try:
            url = normalize_source(body.url)
        except SourceError as e:
            raise HTTPException(400, str(e)) from None

        cut = cut_args(body)
        if body.tiktok:
            cut["tiktok"] = True

        def fn(log):
            db2 = DB(cfg["paths"]["db"], init=False)
            job_id, clips = run_pipeline(url, eff_cfg(db2, log), db2, gid, log, test=body.test and not body.tiktok, **cut)
            fan_out(db2, job_id, plan, log)
            return {"job_id": job_id, "clips": len(clips) * max(1, len(plan))}
        title = f"{'TikTok: ' if body.tiktok else 'Тест: ' if body.test else 'Нарезка целиком: ' if body.mode == 'full' else 'Нарезка: '}{Path(url).name if not url.startswith('http') else url}"
        return title, fn

    @app.post("/api/process")
    def process(body: ProcessIn, d: DB = Depends(get_db)):
        title, fn = make_process_job(body, d)
        return started("process", title, fn, exclusive=True)

    # ---------- очередь ссылок ----------
    # Ссылки обрабатываются строго по одной (скачивание и нарезка занимают процессор и видеокарту). Как только очередной ролик
    # нарезан, его клипы сразу доступны в интерфейсе, пока скачивается и режется следующий. Очередь лежит в workspace/queue.json.
    queue_file = wd / "queue.json"
    qlock = threading.RLock()
    qitems: list[dict] = []
    qstate = {"paused": False, "thread": None}

    def q_save() -> None:
        try:
            queue_file.write_text(json.dumps({"paused": qstate["paused"], "items": qitems}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def q_load() -> None:
        try:
            data = json.loads(queue_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for it in data.get("items", []):
            if it.get("status") == "running":      # программу закрыли посреди нарезки — отметим, повторить можно кнопкой
                it["status"], it["error"] = "error", "Прервано: программу закрыли или перезапустили до завершения"
            qitems.append(it)
        qstate["paused"] = bool(qitems and any(i["status"] == "pending" for i in qitems))   # после перезапуска сами не стартуем — ждём «Продолжить»

    def q_public() -> dict:
        with qlock:
            alive = bool(qstate["thread"] and qstate["thread"].is_alive())
            return {"items": [{k: i.get(k, "") for k in ("id", "url", "status", "error", "task_id", "title", "added", "tiktok", "mode")} for i in qitems],
                    "paused": qstate["paused"], "active": alive}

    def q_worker() -> None:
        while True:
            with qlock:
                item = next((i for i in qitems if i["status"] == "pending"), None)
                if item is None or qstate["paused"]:
                    qstate["thread"] = None
                    return
                item["status"], item["error"] = "running", ""
                q_save()
            try:
                body = ProcessIn(**item["body"])
                d = DB(cfg["paths"]["db"], init=False)
                title, fn = make_process_job(body, d)
                item["title"] = title
                while True:
                    try:
                        t = tasks.start("process", title, fn, exclusive=True)
                        break
                    except Busy:        # идёт ручная нарезка — ждём её конца
                        time.sleep(2)
                item["task_id"] = t.id
                while t.status == "running":
                    time.sleep(1)
                item["status"] = "done" if t.status == "done" else "error"
                item["error"] = "" if t.status == "done" else (t.error or "Ошибка нарезки")
            except HTTPException as e:
                item["status"], item["error"] = "error", str(e.detail)
            except Exception as e:      # одна ссылка не должна останавливать всю очередь
                item["status"], item["error"] = "error", f"{type(e).__name__}: {e}"
            with qlock:
                q_save()

    def q_kick() -> None:
        with qlock:
            if qstate["thread"] and qstate["thread"].is_alive():
                return
            if qstate["paused"] or not any(i["status"] == "pending" for i in qitems):
                return
            th = qstate["thread"] = threading.Thread(target=q_worker, daemon=True, name="narez-queue")
            th.start()

    q_load()

    @app.get("/api/queue")
    def queue_get():
        return q_public()

    @app.post("/api/queue")
    def queue_add(body: QueueIn, d: DB = Depends(get_db)):
        urls = [u.strip() for u in body.urls if u and u.strip()]
        if not urls:
            raise HTTPException(400, "Нет ссылок")
        if len(urls) > 100:
            raise HTTPException(400, "За раз можно добавить до 100 ссылок")
        proto = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        proto.pop("urls", None)
        if not body.tiktok:                                   # настройки проверяем сразу, а не когда дойдёт очередь
            resolve_target(d, body.group_id, body.template_id)
        cut_args(body)
        added, rejected = 0, []
        with qlock:
            pending_urls = {i["url"] for i in qitems if i["status"] in ("pending", "running")}
            for u in urls:
                try:
                    norm = normalize_source(u)
                except SourceError as e:
                    rejected.append({"url": u, "error": str(e)})
                    continue
                if norm in pending_urls:
                    rejected.append({"url": u, "error": "Уже в очереди"})
                    continue
                pending_urls.add(norm)
                qitems.append({"id": uuid.uuid4().hex[:10], "url": norm, "status": "pending", "error": "", "task_id": "", "title": "", "added": time.time(),
                               "tiktok": bool(body.tiktok), "mode": body.mode, "body": {**proto, "url": norm}})
                added += 1
            if added:
                qstate["paused"] = False
                q_save()
        q_kick()
        return {**q_public(), "added": added, "rejected": rejected}

    @app.post("/api/queue/start")
    def queue_start():
        with qlock:
            qstate["paused"] = False
            q_save()
        q_kick()
        return q_public()

    @app.post("/api/queue/pause")
    def queue_pause():
        """Текущая нарезка дойдёт до конца, следующие ссылки ждут."""
        with qlock:
            qstate["paused"] = True
            q_save()
        return q_public()

    @app.post("/api/queue/clear")
    def queue_clear():
        with qlock:
            qitems[:] = [i for i in qitems if i["status"] in ("pending", "running")]
            q_save()
        return q_public()

    @app.delete("/api/queue/{item_id}")
    def queue_remove(item_id: str):
        with qlock:
            it = next((i for i in qitems if i["id"] == item_id), None)
            if not it:
                raise HTTPException(404, "Нет такой ссылки в очереди")
            if it["status"] == "running":
                raise HTTPException(409, "Эта ссылка сейчас обрабатывается — дождись конца нарезки")
            qitems.remove(it)
            q_save()
        return q_public()

    @app.post("/api/queue/{item_id}/retry")
    def queue_retry(item_id: str):
        with qlock:
            it = next((i for i in qitems if i["id"] == item_id), None)
            if not it or it["status"] != "error":
                raise HTTPException(404, "Повторить можно только ссылку с ошибкой")
            it["status"], it["error"] = "pending", ""
            qstate["paused"] = False
            q_save()
        q_kick()
        return q_public()

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
        if "stickers" in sent and not body.stickers:
            upd.update(stickers="", sticker_path="", sticker_applied="")
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
        c0 = need_clip(d, clip_id)
        was_tt = c0["status"] == "tiktok"
        discard_clip(d, cfg, clip_id)
        if was_tt:
            ttlog(f"Клип #{clip_id} удалён: {c0['title'] or ''}".rstrip(": "))
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

    # ---------- стикеры: библиотека и пресеты ----------
    def sticker_json(r) -> dict:
        data = stk.clean_preset(json.loads(r["data"] or "{}"), cfg)
        return {"id": r["id"], "name": r["name"], **data}

    def apply_stickers_to(d: DB, sp: dict, clip_ids: list[int]) -> list[int]:
        """Раскладка стикеров пресета записывается в клип (у каждого клипа своя случайная); баннеры и вставка не меняются."""
        done = []
        for cid in clip_ids:
            c = d.clip(cid)
            if not c or c["status"] not in ("ready", "tiktok"):
                continue
            lay = stk.layout(sp, cfg, f"{cid}:{sp['id']}:{time.time_ns()}")
            lay["preset"] = sp["name"]
            d.update_clip(cid, stickers=stk.to_json(lay), sticker_path="", sticker_applied="")
            done.append(cid)
        return done

    @app.get("/api/stickers")
    def stickers_list():
        d = stk.library_dir(cfg)
        return {"dir": str(d), "screen_mm": stk.SCREEN_MM, "defaults": {k: v for k, v in stk.scfg(cfg).items() if k != "dir"},
                "files": [{"name": p.name, "size": p.stat().st_size} for p in stk.library(cfg)]}

    @app.get("/api/stickers/file")
    def stickers_file(name: str):
        p = stk.library_dir(cfg) / Path(name).name
        if not p.is_file() or p.suffix.lower() not in stk.IMG_EXT:
            raise HTTPException(404, "Файл не найден")
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    @app.put("/api/stickers")
    async def stickers_upload(request: Request, name: str):
        base = re.sub(r"[^\w.\- ]", "_", Path(name).name).strip()
        if Path(base).suffix.lower() not in stk.IMG_EXT:
            raise HTTPException(400, "Допустимы картинки PNG или WebP (лучше с прозрачным фоном)")
        folder = stk.library_dir(cfg)
        folder.mkdir(parents=True, exist_ok=True)
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > 8 * 1024 * 1024:
                raise HTTPException(413, "Файл больше 8 МБ")
        if not data:
            raise HTTPException(400, "Пустой файл")
        dest = folder / base
        dest.write_bytes(bytes(data))
        try:
            info = bn.probe(dest)
        except Exception as e:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"Файл не распознан как картинка: {e}") from None
        return {"name": dest.name, "width": info.get("w"), "height": info.get("h")}

    @app.delete("/api/stickers")
    def stickers_delete(name: str):
        p = stk.library_dir(cfg) / Path(name).name
        if not p.is_file() or p.suffix.lower() not in stk.IMG_EXT:
            raise HTTPException(404, "Файл не найден")
        p.unlink()
        return {"ok": True}

    @app.get("/api/sticker-presets")
    def sticker_presets(d: DB = Depends(get_db)):
        return [sticker_json(r) for r in d.sticker_presets()]

    @app.post("/api/sticker-presets")
    def sticker_preset_save(body: StickerPresetIn, d: DB = Depends(get_db)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Дай пресету название")
        lib = {p.name for p in stk.library(cfg)}
        bad = [f for f in body.files if Path(f).name not in lib]
        if bad:
            raise HTTPException(400, f"Нет такого стикера в папке: {bad[0]}")
        data = stk.clean_preset(body.model_dump(), cfg)
        if body.id and not d.sticker_preset(body.id):
            raise HTTPException(404, "Пресет не найден")
        if any(r["id"] != body.id and r["name"].lower() == name.lower() for r in d.sticker_presets()):
            if body.id:
                raise HTTPException(400, f"Пресет «{name}» уже есть")
            body.id = next(r["id"] for r in d.sticker_presets() if r["name"].lower() == name.lower())    # создание с тем же именем = перезапись
        return sticker_json(d.sticker_preset(d.save_sticker_preset(name, data, body.id)))

    @app.delete("/api/sticker-presets/{preset_id}")
    def sticker_preset_del(preset_id: int, d: DB = Depends(get_db)):
        if not d.sticker_preset(preset_id):
            raise HTTPException(404, "Пресет не найден")
        d.delete_sticker_preset(preset_id)
        return {"ok": True}

    @app.post("/api/sticker-presets/{preset_id}/apply")
    def sticker_preset_apply(preset_id: int, body: ApplyIn, d: DB = Depends(get_db)):
        r = d.sticker_preset(preset_id)
        if not r:
            raise HTTPException(404, "Пресет не найден")
        try:
            ids = apply_stickers_to(d, sticker_json(r), body.clip_ids)
        except stk.StickerError as e:
            raise HTTPException(400, str(e)) from None
        return [clip_json(d.clip(cid), d) for cid in ids]

    # ---------- шаблоны публикации ----------
    def template_json(r, d: DB) -> dict:
        items = []
        for it in json.loads(r["data"] or "{}").get("items", []):
            g = d.group(it["group_id"])
            pr = d.preset(it["preset_id"]) if it.get("preset_id") else None
            sp = d.sticker_preset(it["sticker_preset_id"]) if it.get("sticker_preset_id") else None
            items.append({"group_id": it["group_id"], "group": g["name"] if g else "", "preset_id": it.get("preset_id"), "tags": it.get("tags", ""),
                          "preset": pr["name"] if pr else "", "sticker_preset_id": it.get("sticker_preset_id"), "sticker_preset": sp["name"] if sp else "",
                          "broken": not g or (bool(it.get("preset_id")) and not pr) or (bool(it.get("sticker_preset_id")) and not sp)})
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
            if it.sticker_preset_id and not d.sticker_preset(it.sticker_preset_id):
                raise HTTPException(400, "Пресет стикеров не найден — выбери другой")
        if body.id and not d.template(body.id):
            raise HTTPException(404, "Шаблон не найден")
        other = next((t for t in d.templates() if t["name"].lower() == name.lower() and t["id"] != body.id), None)
        if other:
            raise HTTPException(400, f"Шаблон «{name}» уже есть")
        tid = d.save_template(name, {"tags": normalize_tags(body.tags),
                                     "items": [{"group_id": i.group_id, "preset_id": i.preset_id or None, "sticker_preset_id": i.sticker_preset_id or None,
                                                  "tags": normalize_tags(i.tags)} for i in body.items]}, body.id)
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
            sp = None
            if it.get("sticker_preset_id"):
                spr = d.sticker_preset(it["sticker_preset_id"])
                if not spr:
                    raise HTTPException(400, "В шаблоне есть удалённый пресет стикеров — отредактируй шаблон")
                sp = sticker_json(spr)
            plan.append((it["group_id"], pr, normalize_tags((tdata.get("tags", "") + " " + it.get("tags", "")).strip()), sp))
        if not plan:
            raise HTTPException(400, "Шаблон пуст")
        return plan[0][0], plan

    def fan_out(d: DB, job_id: int, plan: list, log=print):
        """Клипы задания копируются в остальные группы шаблона; каждой группе — её пресет баннеров (баннер, теги, подпись)."""
        if not plan:
            return
        base = [c["id"] for c in d.clips(job_id)]
        by_group = {plan[0][0]: list(base)}
        for gid, *_ in plan[1:]:
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
        for gid, pr, extra, sp in plan:
            ids = by_group[gid]
            for cid in ids:
                d.update_clip(cid, group_id=gid)
            g = d.group(gid)
            if pr:
                apply_preset_to(d, pr, preset_banner_spec(pr), ids)
            if sp:
                apply_stickers_to(d, sp, ids)
            if extra:  # хэштеги шаблона ставим первыми: при лимите в 15 тегов обрезаются остальные, а не они
                for cid in ids:
                    d.update_clip(cid, tags=normalize_tags(extra + " " + (d.clip(cid)["tags"] or "")))
            log(f"Группа «{g['name']}»: {len(ids)} клип(ов)" + (f", пресет «{pr['name']}»" if pr else "") + (f", теги {extra}" if extra else "") + (f", стикеры «{sp['name']}»" if sp else ""))

    # ---------- TikTok: ручная выкладка ----------
    tt_log_lines: list = []     # журнал вкладки TikTok: наложение пресетов, сброс, удаление, ошибки
    tt_log_lock = threading.Lock()

    def ttlog(msg: str):
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        with tt_log_lock:
            tt_log_lines.append(line)
            del tt_log_lines[:-500]
        try:
            lp = Path(cfg["paths"]["workdir"]) / "logs"
            lp.mkdir(parents=True, exist_ok=True)
            with open(lp / "tiktok.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d')} {line}\n")
        except OSError:
            pass

    @app.get("/api/tiktok/log")
    def tt_log():
        with tt_log_lock:
            return {"lines": list(tt_log_lines)}

    @app.delete("/api/tiktok/log")
    def tt_log_clear():
        with tt_log_lock:
            tt_log_lines.clear()
        return {"ok": True}

    @app.post("/api/clipboard")
    def clipboard_seq(body: ClipboardIn):
        """Кладёт в буфер обмена Windows по очереди несколько текстов с паузой (чтобы в истории Win+V остался каждый).
        Делается из программы, а не из браузера: так надёжнее. Вне Windows — 501, и страница копирует сама."""
        import base64
        import subprocess
        import sys
        if not sys.platform.startswith("win"):
            raise HTTPException(501, "Только Windows")
        items = [x for x in body.items if x is not None][:10]
        for i, t in enumerate(items):
            b64 = base64.b64encode(t.encode("utf-8")).decode()
            ps = f"Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')))"
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, timeout=20)
            if r.returncode != 0:
                raise HTTPException(500, "Не удалось записать в буфер: " + r.stderr.decode("utf-8", "replace")[:200])
            if i < len(items) - 1:
                time.sleep(max(0.3, min(body.delay, 5.0)))
        return {"ok": True, "n": len(items)}

    tt_armed: dict = {}      # account_id -> время, до которого расширение может забрать данные для входа (один раз)

    len_cache: dict = {}

    def file_len(path) -> float | None:
        """Реальная длина файла клипа в секундах (с учётом паузы баннера); кэш по пути и времени изменения."""
        try:
            p = Path(path)
            key = (str(p), p.stat().st_mtime_ns)
        except (OSError, TypeError):
            return None
        if key not in len_cache:
            try:
                len_cache[key] = round(bn.probe(p)["duration"], 1)
            except Exception:  # noqa: BLE001
                len_cache[key] = None
        return len_cache[key]

    def tt_clip_json(c, d: DB) -> dict:
        j = clip_json(c, d)
        try:
            pre = json.loads(c["tt_preset"] or "{}")
        except ValueError:
            pre = {}
        fin = bn.cached_final(c, cfg)
        orig = str(Path(c["path"]).resolve()) if c["path"] else ""
        j.update(length_original=file_len(orig), length_preset=file_len(fin) if fin else None)
        j.update(preset=pre.get("name", ""), preset_id=pre.get("id"), has_preset_file=bool(fin),
                 path_original=orig, path_preset=str(Path(fin).resolve()) if fin else "",
                 text=(j["description"] + ("\n\n" + j["tags"] if j["tags"] else "")).strip())
        return j

    @app.get("/api/tiktok/clips")
    def tt_clips(d: DB = Depends(get_db)):
        return [tt_clip_json(c, d) for c in d.tiktok_clips()]

    def rm_in_workdir(fin, keep) -> None:
        """Удаляет собранный файл варианта клипа (только внутри рабочей папки и не сам клип)."""
        if fin and fin != keep:
            try:
                if Path(fin).is_file() and Path(fin).resolve().is_relative_to(wd.resolve()):
                    Path(fin).unlink()
            except OSError:
                pass

    def tt_strip_preset(c) -> dict:
        """Поля клипа без текста прежнего пресета (его теги и подпись), баннер и готовый файл убираются."""
        try:
            old = json.loads(c["tt_preset"] or "{}")
        except ValueError:
            old = {}
        upd = {}
        if old.get("footer"):
            upd["reason"] = (c["reason"] or "").replace("\n\n" + old["footer"], "").replace(old["footer"], "").strip()
        if old.get("tags"):
            drop = {t.lower() for t in old["tags"].split()}
            upd["tags"] = " ".join(t for t in (c["tags"] or "").split() if t.lower() not in drop)
        for fin in (c["final_path"], c["sticker_path"]):
            rm_in_workdir(fin, c["path"])
        upd.update(banner="", final_path="", banner_applied="", tt_preset="", stickers="", sticker_path="", sticker_applied="")
        return upd

    @app.post("/api/tiktok/preset")
    def tt_apply_preset(body: TikTokPresetIn, d: DB = Depends(get_db)):
        """Пресет баннеров и/или стикеров применяется ко всем клипам вкладки; прежний вариант и его файлы удаляются
        (остаются оригинал и последний вариант)."""
        if not body.preset_id and not body.sticker_preset_id:
            raise HTTPException(400, "Выбери пресет баннеров или пресет стикеров")
        pr = spec = sp = None
        if body.preset_id:
            r = d.preset(body.preset_id)
            if not r:
                raise HTTPException(404, "Пресет не найден")
            pr = preset_json(r)
            try:
                spec = preset_banner_spec(pr)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
        if body.sticker_preset_id:
            r = d.sticker_preset(body.sticker_preset_id)
            if not r:
                raise HTTPException(404, "Пресет стикеров не найден")
            sp = sticker_json(r)
            if not stk.library(cfg):
                raise HTTPException(400, "В папке стикеров нет картинок")
        ids = [c["id"] for c in d.tiktok_clips() if body.clip_ids is None or c["id"] in set(body.clip_ids)]
        if not ids:
            raise HTTPException(400, "Не выбрано ни одного клипа")
        label = " + ".join(x["name"] for x in (pr, sp) if x)

        def fn(log0):
            def log(m):
                log0(m)
                ttlog(m)
            db2 = DB(cfg["paths"]["db"], init=False)
            log(f"Применяю «{label}» к клипам: {len(ids)}")
            ok = bad = 0
            for n, cid in enumerate(ids, 1):
                c = db2.clip(cid)
                if not c or c["status"] != "tiktok":
                    continue
                try:
                    _tt_apply_one(db2, c, cid, n, log)
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    bad += 1
                    log(f"ОШИБКА, клип #{cid}: {e}")
            log(f"Готово: {ok} клип(ов)" + (f", с ошибкой: {bad}" if bad else ""))
            return {"clips": len(ids), "preset": label}

        def _tt_apply_one(db2, c, cid, n, log):
            if True:
                upd = tt_strip_preset(c)
                if pr and pr["tags"]:
                    upd["tags"] = normalize_tags((upd.get("tags", c["tags"]) or "") + " " + pr["tags"])
                if pr and pr["footer"]:
                    base = upd.get("reason", c["reason"]) or ""
                    upd["reason"] = (base.rstrip() + "\n\n" + pr["footer"]).strip()
                if spec:
                    upd["banner"] = spec
                if sp:
                    lay = stk.layout(sp, cfg, f"{cid}:{sp['id']}:{time.time_ns()}")
                    lay["preset"] = sp["name"]
                    upd["stickers"] = stk.to_json(lay)
                upd["tt_preset"] = json.dumps({"id": pr["id"] if pr else None, "name": label, "tags": pr["tags"] if pr else "",
                                               "footer": pr["footer"] if pr else ""}, ensure_ascii=False)
                db2.update_clip(cid, **upd)
                if spec or sp:
                    log(f"Клип {n}/{len(ids)}: накладываю «{label}»...")
                    fin = bn.ensure_final(db2, db2.clip(cid), cfg, log)
                    log(f"Клип #{cid}: готово — {Path(fin).name if fin else 'файл не создан'}")
        return started("tiktok-preset", f"TikTok: {label}", fn)

    @app.post("/api/tiktok/filler")
    def tt_apply_filler(body: TikTokFillerIn, d: DB = Depends(get_db)):
        """Видео-вставка для выбранных клипов вкладки. Пресет баннеров и стикеров клипа не трогается: вставка применяется
        к клипу вместе с ним (баннеры и стикеры ложатся поверх кадра со вставкой). position="" — вставка убирается."""
        pos = (body.position or "").strip()
        if pos and pos not in filler_mod.POSITIONS:
            raise HTTPException(400, "Расположение вставки: сверху или снизу")
        spec = None
        if pos:
            if not filler_mod.library(cfg):
                raise HTTPException(400, f"В папке вставок нет видео: {filler_mod.library_dir(cfg)}")
            if body.file and body.file not in {p.name for p in filler_mod.library(cfg)}:
                raise HTTPException(404, f"Видео «{body.file}» нет в папке вставок")
            spec = {"position": pos, "file": body.file}
        ids = [c["id"] for c in d.tiktok_clips() if body.clip_ids is None or c["id"] in set(body.clip_ids)]
        if not ids:
            raise HTTPException(400, "Не выбрано ни одного клипа")
        label = ("сверху" if pos == "top" else "снизу") + (f", {body.file}" if body.file else ", случайное видео") if pos else "убрать"

        def fn(log0):
            def log(m):
                log0(m)
                ttlog(m)
            db2 = DB(cfg["paths"]["db"], init=False)
            log(f"Вставка ({label}) для клипов: {len(ids)}")
            ok = bad = 0
            for n, cid in enumerate(ids, 1):
                c = db2.clip(cid)
                if not c or c["status"] != "tiktok":
                    continue
                old_filler = c["filler"] or ""
                try:
                    for key in ("filler_path", "final_path", "sticker_path"):     # собранные поверх старой вставки файлы устарели
                        rm_in_workdir(c[key], c["path"])
                    db2.update_clip(cid, filler=json.dumps(spec, ensure_ascii=False) if spec else "", filler_path="", filler_applied="",
                                    final_path="", banner_applied="", sticker_path="", sticker_applied="")
                    c = db2.clip(cid)
                    if filler_mod.normalize(c["filler"]) or bn.specs_from_json(c["banner"]) or c["stickers"]:
                        log(f"Клип {n}/{len(ids)} (#{cid}): собираю" + (" со вставкой" if spec else " без вставки, остальное сохраняю") + "...")
                        fin = bn.ensure_final(db2, c, cfg, log)
                        log(f"Клип #{cid}: готово — {Path(fin).name}")
                    else:
                        log(f"Клип #{cid}: вставка убрана, остался оригинал")
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    bad += 1
                    log(f"ОШИБКА, клип #{cid}: {e}")
                    try:      # возвращаем клип как был: прежняя вставка (или её отсутствие) и всё, что на нём собрано
                        db2.update_clip(cid, filler=old_filler, filler_path="", filler_applied="", final_path="", banner_applied="", sticker_path="", sticker_applied="")
                        c2 = db2.clip(cid)
                        if filler_mod.normalize(c2["filler"]) or bn.specs_from_json(c2["banner"]) or c2["stickers"]:
                            bn.ensure_final(db2, c2, cfg, lambda *_: None)
                        log(f"Клип #{cid}: прежний вариант восстановлен")
                    except Exception as e2:  # noqa: BLE001
                        log(f"Клип #{cid}: не удалось восстановить прежний вариант: {e2}")
            log(f"Готово: {ok} клип(ов)" + (f", с ошибкой: {bad}" if bad else ""))
        return started("tiktok-filler", f"TikTok: вставка ({label})", fn)

    @app.post("/api/tiktok/reset")
    def tt_reset(body: Optional[TikTokIdsIn] = None, d: DB = Depends(get_db)):
        """Убирает последний пресет у выбранных клипов (по умолчанию у всех): остаются только оригиналы."""
        only = set(body.clip_ids) if body and body.clip_ids is not None else None
        n = 0
        for c in d.tiktok_clips():
            if only is not None and c["id"] not in only:
                continue
            d.update_clip(c["id"], **tt_strip_preset(c))
            n += 1
        ttlog(f"Пресет убран у клипов: {n} (остались оригиналы)")
        return {"clips": n}

    @app.delete("/api/tiktok/clips")
    def tt_delete_all(ids: str = "", d: DB = Depends(get_db)):
        """Очистка хранилища: удаляются и оригиналы, и клипы с последним пресетом, а затем исходные видео.
        ids=1,2,3 — только эти клипы, иначе все клипы вкладки."""
        only = {int(x) for x in ids.split(",") if x.strip().isdigit()} if ids else None
        n = 0
        for c in d.tiktok_clips():
            if only is not None and c["id"] not in only:
                continue
            discard_clip(d, cfg, c["id"])
            ttlog(f"Клип #{c['id']} удалён: {c['title'] or ''}".rstrip(": "))
            n += 1
        freed = sweep_sources(d, cfg)
        ttlog(f"Удалено клипов: {n}, освобождено ~{round(freed / 1e6)} МБ")
        return {"deleted": n, "freed_mb": round(freed / 1e6)}

    def tt_account_json(r) -> dict:
        return {"id": r["id"], "label": r["label"], "login": r["login"], "has_password": bool(r["password"]), "note": r["note"]}

    @app.get("/api/tiktok/accounts")
    def tt_accounts(d: DB = Depends(get_db)):
        return [tt_account_json(r) for r in d.tiktok_accounts()]

    @app.post("/api/tiktok/accounts")
    def tt_account_add(body: TikTokAccountIn, d: DB = Depends(get_db)):
        label = body.label.strip()
        if not label:
            raise HTTPException(400, "Дай аккаунту название")
        if not body.login.strip() or not body.password:
            raise HTTPException(400, "Нужны логин и пароль")
        return tt_account_json(d.tiktok_account(d.save_tiktok_account(label, body.login.strip(), body.password, body.note.strip())))

    @app.put("/api/tiktok/accounts/{acc_id}")
    def tt_account_edit(acc_id: int, body: TikTokAccountIn, d: DB = Depends(get_db)):
        r = d.tiktok_account(acc_id)
        if not r:
            raise HTTPException(404, "Аккаунт не найден")
        label = body.label.strip()
        if not label:
            raise HTTPException(400, "Дай аккаунту название")
        d.save_tiktok_account(label, body.login.strip(), body.password or r["password"], body.note.strip(), acc_id)
        return tt_account_json(d.tiktok_account(acc_id))

    @app.delete("/api/tiktok/accounts/{acc_id}")
    def tt_account_del(acc_id: int, d: DB = Depends(get_db)):
        if not d.tiktok_account(acc_id):
            raise HTTPException(404, "Аккаунт не найден")
        d.delete_tiktok_account(acc_id)
        return {"ok": True}

    @app.post("/api/tiktok/accounts/{acc_id}/arm")
    def tt_account_arm(acc_id: int, d: DB = Depends(get_db)):
        """Страница нажала «Войти»: на 60 секунд разрешаем расширению один раз забрать данные этого аккаунта."""
        if not d.tiktok_account(acc_id):
            raise HTTPException(404, "Аккаунт не найден")
        tt_armed.clear()
        tt_armed[acc_id] = time.time() + 60
        return {"ok": True}

    def tt_variant_file(c, variant: str) -> Path:
        """Файл клипа для загрузки в TikTok: final — со вставкой/баннерами/стикерами (если собран), иначе оригинал."""
        path = None
        if variant == "final":
            fin = bn.cached_final(c, cfg)
            path = str(fin) if fin else None
        else:
            path = c["path"]
        if not path or not Path(path).is_file():
            raise HTTPException(404, "Файл клипа не найден (возможно, вариант с пресетом ещё не собран)")
        return Path(path)

    def need_helper(request: Request):
        if request.headers.get("x-narez-helper") != "1":
            raise HTTPException(403, "Только для расширения narez")

    @app.get("/api/tiktok/clips/{clip_id}/fileinfo")
    def tt_fileinfo(clip_id: int, request: Request, variant: str = "original", d: DB = Depends(get_db)):
        """Для расширения: размер и имя файла, который нужно положить в форму загрузки TikTok Studio."""
        need_helper(request)
        c = need_clip(d, clip_id)
        p = tt_variant_file(c, variant)
        return {"size": p.stat().st_size, "name": p.name, "type": "video/mp4"}

    @app.get("/api/tiktok/clips/{clip_id}/chunk")
    def tt_chunk(clip_id: int, request: Request, offset: int = 0, size: int = 4_000_000, variant: str = "original", d: DB = Depends(get_db)):
        """Для расширения: кусок файла (расширение собирает файл из кусков — страница TikTok не может читать localhost напрямую)."""
        need_helper(request)
        c = need_clip(d, clip_id)
        p = tt_variant_file(c, variant)
        size = max(1, min(size, 8_000_000))
        with open(p, "rb") as f:
            f.seek(max(offset, 0))
            data = f.read(size)
        return Response(content=data, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/tiktok/clips/{clip_id}/note")
    def tt_ext_log(clip_id: int, request: Request, msg: str = ""):
        """Для расширения: что оно сделало на странице TikTok Studio (попадает в журнал вкладки). GET — чтобы не упираться в Origin расширения."""
        need_helper(request)
        ttlog(f"Клип #{clip_id}, TikTok Studio: " + msg[:300])
        return {"ok": True}

    @app.get("/api/tiktok/pending")
    def tt_pending(request: Request, d: DB = Depends(get_db)):
        """Только для расширения: заголовок X-Narez-Helper (чужая страница не может его отправить без CORS-разрешения),
        данные отдаются один раз и только после нажатия «Войти» в интерфейсе."""
        if request.headers.get("x-narez-helper") != "1":
            raise HTTPException(403, "Только для расширения narez")
        now = time.time()
        for acc_id, until in list(tt_armed.items()):
            tt_armed.pop(acc_id, None)
            r = d.tiktok_account(acc_id)
            if r and until >= now:
                return {"id": r["id"], "label": r["label"], "login": r["login"], "password": r["password"]}
        raise HTTPException(404, "Нет ожидающего входа")

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
                        chroma: bool | None = None, chroma_color: str = "", speed: float = 1.0):
        p = library().get(name)
        if not p:
            raise HTTPException(404, "Баннер не найден")
        try:
            spec = bn.normalize_spec({"path": str(p), "start": start, "duration": duration, "width_percent": width_percent,
                                      "position": position, "duck_percent": duck_percent, "margin": margin,
                                      "chroma": chroma, "chroma_color": chroma_color, "speed": speed}, cfg)
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
