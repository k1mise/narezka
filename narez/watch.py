"""Слежение за чужими каналами: свежие видео за последние N дней с просмотрами и лайками.

Каналы добавляются ссылкой (YouTube: /@имя, /channel/…, /c/…, /user/…, а также любая страница-список, которую понимает yt-dlp).
Список видео берётся быстрым запросом (без загрузки), по каждому свежему видео запрашиваются дата, просмотры и лайки.
Прокси и cookies здесь не используются: это только чтение публичных страниц, а не скачивание.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


class WatchError(Exception):
    pass


def channel_url(raw: str) -> str:
    """Приводит ссылку к странице списка видео канала. Для YouTube — вкладка «Видео»."""
    s = (raw or "").strip()
    if not s:
        raise WatchError("Пустая ссылка")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I):
        if s.startswith("@"):
            s = "https://www.youtube.com/" + s
        elif re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", s, re.I):
            s = "https://" + s
        else:
            raise WatchError("Нужна ссылка на канал, например https://www.youtube.com/@name")
    u = urlsplit(s)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise WatchError("Нужна ссылка на канал, например https://www.youtube.com/@name")
    host = u.netloc.lower()
    if host == "youtu.be":
        raise WatchError("Это ссылка на видео, а нужна ссылка на канал (https://www.youtube.com/@name)")
    if host in YT_HOSTS:
        path = u.path.rstrip("/")
        if path.startswith("/watch") or path.startswith("/shorts/") or path.startswith("/live/") or u.path.startswith("/playlist"):
            if not path.startswith("/playlist"):
                raise WatchError("Это ссылка на видео, а нужна ссылка на канал (https://www.youtube.com/@name)")
            return f"https://www.youtube.com{u.path}?{u.query}"
        if not re.match(r"^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)(/(videos|streams|shorts|featured|playlists|live))?$", path):
            raise WatchError("Не похоже на ссылку на канал YouTube (нужна вида /@name, /channel/UC…, /c/name или /user/name)")
        base = re.sub(r"/(videos|streams|shorts|featured|playlists|live)$", "", path)
        return f"https://www.youtube.com{base}/videos"
    return s.split("#")[0]


def _opts() -> dict:
    return {"quiet": True, "no_warnings": True, "no_color": True, "retries": 3, "extractor_retries": 3, "socket_timeout": 30, "skip_download": True}


def _ydl(extra: dict | None = None):
    import yt_dlp
    return yt_dlp.YoutubeDL({**_opts(), **(extra or {})})


def _upload_ts(info: dict) -> float | None:
    ts = info.get("release_timestamp") or info.get("timestamp")
    if ts:
        return float(ts)
    d = str(info.get("upload_date") or "")
    if re.fullmatch(r"\d{8}", d):
        return dt.datetime(int(d[:4]), int(d[4:6]), int(d[6:]), tzinfo=dt.timezone.utc).timestamp()
    return None


def _clean_name(s: str) -> str:
    return re.sub(r"\s+-\s+(Videos|Видео|Shorts|Live|Streams)$", "", (s or "").strip(), flags=re.I)


def list_entries(url: str, limit: int, ydl_factory=_ydl) -> tuple[str, list[dict]]:
    """(название канала, свежие записи [{id, url, title}] — сверху новые). Без загрузки видео."""
    try:
        with ydl_factory({"extract_flat": "in_playlist", "playlistend": limit}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise WatchError(_short_error(e)) from None
    if not info:
        raise WatchError("Канал не найден или недоступен")
    entries = list(info.get("entries") or [])
    # у страницы @имя без /videos внутри лежат вкладки — раскрываем первую, где есть видео
    if entries and all((e or {}).get("_type") == "playlist" for e in entries):
        entries = list((entries[0] or {}).get("entries") or [])
    out = []
    for e in entries:
        if not e or not e.get("id"):
            continue
        out.append({"id": str(e["id"]), "url": e.get("webpage_url") or e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
                    "title": e.get("title") or "", "views": e.get("view_count"), "ts": _upload_ts(e), "duration": e.get("duration")})
    name = _clean_name(info.get("channel") or info.get("uploader") or info.get("title") or "")
    return name, out


def video_info(url: str, ydl_factory=_ydl) -> dict | None:
    """Метаданные одного видео (дата, просмотры, лайки, превью); None, если видео недоступно."""
    try:
        with ydl_factory({"noplaylist": True}) as ydl:
            i = ydl.extract_info(url, download=False)
    except Exception:
        return None
    if not i or i.get("_type") == "playlist":
        return None
    if i.get("live_status") == "is_upcoming":
        return None
    thumb = i.get("thumbnail") or ""
    if not thumb and i.get("thumbnails"):
        thumb = (i["thumbnails"][-1] or {}).get("url", "")
    vid = str(i.get("id") or "")
    if "ytimg.com" in thumb or not thumb:
        if i.get("extractor_key") == "Youtube" and vid:
            thumb = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"       # лёгкое превью 320×180 вместо огромного оригинала
    return {"id": vid, "url": i.get("webpage_url") or url, "title": i.get("title") or "", "channel": _clean_name(i.get("channel") or i.get("uploader") or ""),
            "thumb": thumb, "duration": i.get("duration"), "views": i.get("view_count"), "likes": i.get("like_count"), "ts": _upload_ts(i)}


def _short_error(e: Exception) -> str:
    s = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).strip().replace("ERROR: ", "")
    return (s.splitlines() or ["Не удалось получить данные канала"])[-1][:240]


def refresh_channel(db, ch, days: float, log=print, limit: int = 40, workers: int = 4, ydl_factory=_ydl) -> int:
    """Обновляет список свежих видео одного канала. Возвращает число видео за окно. Ошибка канала пишется в строку канала."""
    now = time.time()
    cutoff = now - days * 86400
    try:
        name, entries = list_entries(ch["url"], limit, ydl_factory)
    except WatchError as e:
        db.update_watch_channel(ch["id"], error=str(e), checked_at=now)
        log(f"✗ {ch['name'] or ch['url']}: {e}")
        return 0
    known = db.watch_known(ch["id"])
    todo = []
    for e in entries:
        k = known.get(e["id"])
        ts = (k["uploaded"] if k else None) or e.get("ts")
        if ts is not None and ts < cutoff:
            continue                                   # старше окна — не трогаем (ни запроса, ни записи)
        todo.append(e)
    count, chunk = 0, 8
    stop = False
    i = 0
    while i < len(todo) and not stop:
        batch = todo[i:i + chunk]
        i += chunk
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            infos = list(ex.map(lambda e: video_info(e["url"], ydl_factory), batch))
        fresh = 0
        for e, inf in zip(batch, infos):
            if not inf or inf["ts"] is None:
                continue
            db.upsert_watch_video(ch["id"], {**inf, "channel": inf["channel"] or name or ch["name"], "updated": time.time()})   # старые тоже помним — чтобы не запрашивать их снова
            if inf["ts"] < cutoff:
                continue
            fresh += 1
            count += 1
        if not fresh and any(inf for inf in infos):
            stop = True                                # весь пакет старше окна: дальше только старее
    db.update_watch_channel(ch["id"], name=name or ch["name"], error="", checked_at=time.time())
    log(f"✓ {name or ch['url']}: видео за {int(days)} дн. — {count}")
    return count
