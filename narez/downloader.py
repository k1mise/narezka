from __future__ import annotations

from . import captions
import re
from pathlib import Path

from .config import folder

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".flv", ".ts", ".mpg", ".mpeg", ".wmv"}
_DOMAIN = re.compile(r"^(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:[/?#:].*)?$", re.I)


class SourceError(RuntimeError):
    pass


_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|yclid|_openstat|igshid|mc_cid|mc_eid|ref_src|feature)$", re.I)


def clean_url(url: str) -> str:
    """Убирает из ссылки рекламные метки (utm_referrer и т. п.): они только мешают, а на сайтах бывают причиной ошибок."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    u = urlsplit(url)
    if not u.query:
        return url
    yt = (u.hostname or "").lower().endswith(("youtube.com", "youtu.be"))
    keep = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not _TRACKING.match(k) and not (yt and k == "si")]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(keep), u.fragment))


def safe_name(title: str, limit: int = 90) -> str:
    """Название видео -> имя файла: читаемое (кириллица остаётся), без запрещённых в Windows символов."""
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", title or "")
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:limit].strip(" .") or ""


def normalize_source(text: str) -> str:
    """Приводит ввод пользователя к ссылке или пути к файлу.
    Принимает: http(s)-ссылку, ссылку без схемы (youtube.com/watch?v=..), путь к видеофайлу на этом компьютере."""
    s = (text or "").strip().strip('"').strip("'")
    if not s:
        raise SourceError("Вставь ссылку на видео или путь к видеофайлу")
    if re.match(r"^https?://\S+$", s, re.I):
        return clean_url(s)
    if re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I):
        raise SourceError("Поддерживаются ссылки http:// и https://")
    p = Path(s).expanduser()
    if p.suffix.lower() in VIDEO_EXT or p.exists():
        if not p.is_file():
            raise SourceError(f"Файл не найден: {s}")
        if p.suffix.lower() not in VIDEO_EXT:
            raise SourceError("Это не видеофайл (mp4, mkv, mov, webm…)")
        return str(p.resolve())
    if _DOMAIN.match(s) and " " not in s:
        return clean_url("https://" + s)
    raise SourceError("Не похоже на ссылку или путь к видеофайлу")


def is_local(src: str) -> bool:
    return not re.match(r"^https?://", src, re.I)


def friendly_error(err: Exception, url: str = "") -> str:
    """Короткое объяснение частых ошибок yt-dlp."""
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(err)).strip()
    low = msg.lower()
    host = ""
    m = re.search(r"https?://([^/\s]+)", url or "")
    if m:
        host = m.group(1)
    hints = [
        ("failed to resolve", "Сайт не открывается с этого компьютера (не находится адрес в интернете). Проверь, открывается ли он в браузере. "
                              "Если включён VPN — он может блокировать этот сайт: отключи VPN для него, либо выбери прокси из нужной страны (вкладка «Прокси»)."),
        ("getaddrinfo failed", "Сайт не открывается с этого компьютера (не находится адрес в интернете). Проверь, открывается ли он в браузере. "
                               "Если включён VPN — он может блокировать этот сайт: отключи VPN для него, либо выбери прокси из нужной страны (вкладка «Прокси»)."),
        ("eof occurred in violation of protocol", "Сайт обрывает защищённое соединение — обычно это блокировка провайдером или VPN, либо защита сайта от скачивания. "
                                                  "Попробуй другой прокси или VPN-страну, либо скачай файл вручную и укажи путь к нему."),
        ("certificate verify failed", "Не удалось проверить сертификат сайта (часто мешает антивирус/прокси с подменой сертификата)."),
        ("connection reset", "Соединение сброшено сайтом или сетью (блокировка/VPN). Попробуй другой прокси."),
        ("timed out", "Сайт слишком долго не отвечает. Проверь интернет/VPN или попробуй позже."),
        ("unsupported url", "Эта страница не похожа на видео, и найти на ней видеофайл не удалось. Дай прямую ссылку на видео или скачай файл и укажи путь к нему."),
        ("sign in to confirm", "YouTube просит подтвердить, что ты не бот. Укажи cookies браузера: в config.toml [download] cookies_from_browser = \"chrome\"."),
        ("login required", "Сайт требует вход. Укажи cookies браузера: в config.toml [download] cookies_from_browser = \"chrome\"."),
        ("private video", "Видео закрытое — скачать нельзя."),
        ("video unavailable", "Видео недоступно (удалено или закрыто для твоей страны)."),
        ("geo", "Видео недоступно в твоей стране. Нужен прокси (настройка [download] proxy)."),
        ("http error 403", "Сайт отказал в доступе (403). Попробуй cookies браузера или обнови yt-dlp: pip install -U yt-dlp"),
        ("http error 429", "Сайт временно ограничил запросы (429). Подожди несколько минут."),
    ]
    for key, text in hints:
        if key in low:
            return f"{text}\n(подробности: {msg[-300:]})"
    return msg


def _pick_path(out_dir: Path, title: str, ident: str) -> str:
    """Имя файла без расширения: «Название видео». Если такое имя уже занято другим видео — добавляем [id]."""
    base = safe_name(title) or ident
    marker = out_dir / f"{base}.src"
    if marker.exists() and marker.read_text(encoding="utf-8", errors="ignore").strip() != ident:
        base = f"{base} [{ident}]"
    elif not marker.exists() and any(out_dir.glob(glob_escape(base) + ".*")):
        base = f"{base} [{ident}]"  # файл с таким именем появился не от нас — не трогаем
    return base


def glob_escape(s: str) -> str:
    import glob
    return glob.escape(s)


def has_russian_audio(info: dict) -> bool:
    """Есть ли у видео отдельная русская звуковая дорожка (дубляж / автодубляж YouTube)."""
    return any(str(f.get("language") or "").lower().startswith("ru") and f.get("vcodec") in (None, "none")
               for f in info.get("formats") or [])


def format_for(audio: str, height: int, has_ru: bool) -> str:
    """Строка формата yt-dlp. audio="ru": русская дорожка, если она есть; иначе — оригинал."""
    base = f"bv*[height<={height}]+ba/b[height<={height}]/b"
    if audio == "ru" and has_ru:
        return f"bv*[height<={height}]+ba[language^=ru]/{base}"
    return base


def download(url: str, workdir: Path, log=print, cfg: dict | None = None, audio: str = "ru", subs: str = "youtube") -> tuple[Path, str]:
    """Возвращает (путь к видео, название). Источник: ссылка (yt-dlp: YouTube и ~1000 сайтов, прямые ссылки на файлы,
    страницы с видеоплеером) или путь к локальному видеофайлу (скачивание пропускается).
    Файл называется по названию видео («Как дрессировать щенка.mp4»). Рядом сохраняются субтитры YouTube (см. captions.py):
    ручные — всегда, автоматические — если subs="youtube"."""
    src = normalize_source(url)
    if is_local(src):
        p = Path(src)
        log(f"Беру локальный файл: {p.name}")
        return p, p.stem

    import yt_dlp

    dl = (cfg or {}).get("download", {})
    out_dir = folder(cfg, "sources") if cfg else workdir / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    h = int(dl.get("max_height", 1080))
    opts = {
        "format": f"bv*[height<={h}]+ba/b[height<={h}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "playlist_items": "1",      # ссылка на плейлист — берём только первое видео
        "retries": 10,              # обрывы соединения (RemoteDisconnected) часто проходят со 2–3 попытки
        "fragment_retries": 10,
        "extractor_retries": 5,
        "socket_timeout": 30,
        "no_color": True,           # без ANSI-кодов в тексте ошибок
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [
            lambda d: log(f"Загрузка: {d.get('_percent_str', '').strip()}")
            if d["status"] == "downloading" and d.get("_percent_str", "").strip().endswith("0.0%")
            else None
        ],
    }
    if dl.get("proxy"):
        opts["proxy"] = dl["proxy"]
    if dl.get("cookies_file"):
        opts["cookiefile"] = str(dl["cookies_file"])
    elif dl.get("cookies_from_browser"):
        opts["cookiesfrombrowser"] = (str(dl["cookies_from_browser"]),)

    def attempt(extra: dict):
        with yt_dlp.YoutubeDL({**opts, **extra}) as ydl:
            info = ydl.extract_info(src, download=False)
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    raise SourceError("В плейлисте нет доступных видео")
                info = entries[0]
            ru = has_russian_audio(info)
            fmt = format_for(audio, h, ru)
            ydl.params["format"] = fmt
            ydl.format_selector = ydl.build_format_selector(fmt)  # селектор строится при создании YoutubeDL — пересобираем
            if audio == "ru":
                log("Нашёл русскую звуковую дорожку — качаю с ней." if ru else
                    "Русской дорожки у этого видео нет — качаю оригинал.")
            base = _pick_path(out_dir, info.get("title") or "", str(info.get("id") or "video"))
            ydl.params["outtmpl"] = {"default": str(out_dir / (base + ".%(ext)s"))}
            info = ydl.process_ie_result(info, download=True)
            path = None
            for r in info.get("requested_downloads") or []:
                if r.get("filepath"):
                    path = Path(r["filepath"])
            if path is None:
                path = Path(ydl.prepare_filename(info))
                if not path.exists() and path.with_suffix(".mp4").exists():
                    path = path.with_suffix(".mp4")
            (out_dir / f"{base}.src").write_text(str(info.get("id") or ""), encoding="utf-8")
            try:
                kind = captions.fetch(ydl, info, path, captions.wanted_lang(info, ru and audio == "ru", cfg), subs == "youtube", log)
                if kind:
                    log("Нашёл ручные субтитры YouTube — речь распознавать не нужно." if kind == "manual"
                        else "Нашёл автоматические субтитры YouTube — Whisper не понадобится." if subs == "youtube" else "")
            except Exception as e:  # noqa: BLE001 — субтитры необязательны
                log(f"Субтитры YouTube пропущены: {str(e)[:100]}")
            return path, info.get("title") or path.stem

    try:
        try:
            path, title = attempt({})
        except SourceError:
            raise
        except Exception as e:
            if "violation of protocol" in str(e) or "SSL" in str(e):
                log("Сайт не принял обычное защищённое соединение — пробую совместимый режим…")
                path, title = attempt({"legacyserverconnect": True})
            else:
                raise
    except SourceError:
        raise
    except Exception as e:
        raise RuntimeError(friendly_error(e, src)) from None
    if not path.exists():
        raise RuntimeError("Скачивание завершилось, но файл не найден")
    return path, title
