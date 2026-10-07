"""Прокси: разбор списка в любом формате, проверка (IP, страна, город, пинг).
Используются для скачивания видео (yt-dlp), когда сайт недоступен из твоей страны или режет запросы."""
from __future__ import annotations

import re
import time
from urllib.parse import quote, unquote, urlsplit

SCHEMES = {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}
_HOST = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$|^(?:\d{1,3}\.){3}\d{1,3}$|^\[[0-9A-Fa-f:]+\]$|^localhost$")


def _is_port(x: str) -> bool:
    return x.isdigit() and 0 < int(x) < 65536


def _is_host(x: str) -> bool:
    return bool(_HOST.match(x)) and (not re.match(r"^\d+(\.\d+){3}$", x) or all(int(p) < 256 for p in x.split(".")))


def _build(scheme: str, host: str, port: str, user: str = "", pwd: str = "") -> dict:
    auth = f"{quote(user, safe='')}:{quote(pwd, safe='')}@" if user or pwd else ""
    return {"url": f"{scheme}://{auth}{host}:{port}", "scheme": scheme, "host": host, "port": int(port),
            "user": user, "has_password": bool(pwd)}


def parse_line(line: str, default_scheme: str = "http") -> dict | None:
    """Понимает: scheme://user:pass@host:port, user:pass@host:port, host:port, host:port:user:pass,
    user:pass:host:port, 'host port [user pass]' (пробел/таб/;/|/запятая как разделитель)."""
    s = line.strip().strip(",;")
    if not s or s.startswith("#"):
        return None
    scheme = default_scheme
    m = re.match(r"^([A-Za-z0-9]+)://(.*)$", s)
    if m:
        scheme, s = m.group(1).lower(), m.group(2)
        if scheme not in SCHEMES:
            return None
    s = s.rstrip("/")
    if "@" in s:                                                       # user:pass@host:port (или host:port@user:pass)
        left, right = s.rsplit("@", 1)
        for cred, addr in ((left, right), (right, left)):
            hp = addr.rsplit(":", 1)
            if len(hp) == 2 and _is_host(hp[0]) and _is_port(hp[1]):
                user, _, pwd = cred.partition(":")
                return _build(scheme, hp[0], hp[1], unquote(user), unquote(pwd))
        return None
    parts = [p for p in re.split(r"[\s;|,]+|:", s) if p] if re.search(r"[\s;|,]", s) else s.split(":")
    if len(parts) == 2 and _is_host(parts[0]) and _is_port(parts[1]):
        return _build(scheme, parts[0], parts[1])
    if len(parts) == 4:
        if _is_host(parts[0]) and _is_port(parts[1]):                  # host:port:user:pass
            return _build(scheme, parts[0], parts[1], parts[2], parts[3])
        if _is_host(parts[2]) and _is_port(parts[3]):                  # user:pass:host:port
            return _build(scheme, parts[2], parts[3], parts[0], parts[1])
    return None


def parse_proxies(text: str, default_scheme: str = "http") -> tuple[list[dict], list[str]]:
    """Возвращает (прокси без повторов, непонятые строки)."""
    good, bad, seen = [], [], set()
    for line in (text or "").replace("\r", "\n").split("\n"):
        if not line.strip() or line.strip().startswith("#"):
            continue
        p = parse_line(line, default_scheme)
        if p is None:
            bad.append(line.strip()[:80])
        elif p["url"] not in seen:
            seen.add(p["url"])
            good.append(p)
    return good, bad


def mask(url: str) -> str:
    """Для показа в интерфейсе: пароль скрыт."""
    u = urlsplit(url)
    auth = f"{unquote(u.username or '')}:***@" if u.username else ""
    return f"{u.scheme}://{auth}{u.hostname}:{u.port}"


def check_proxy(url: str, timeout: float = 12.0, get=None) -> dict:
    """Ходит через прокси на сервис геолокации: показывает внешний IP, страну, город, провайдера и время ответа."""
    import requests
    get = get or requests.get
    u = url.replace("socks5://", "socks5h://").replace("socks4://", "socks4a://")  # DNS тоже через прокси
    proxies = {"http": u, "https": u}
    t0 = time.time()
    try:
        r = get("https://ipwho.is/", proxies=proxies, timeout=timeout)
        ms = int((time.time() - t0) * 1000)
        d = r.json()
        if d.get("success") is False:
            raise ValueError(d.get("message") or "сервис геолокации отказал")
        return {"ok": True, "ip": d.get("ip", ""), "country": d.get("country", ""), "code": d.get("country_code", ""),
                "city": d.get("city", ""), "isp": (d.get("connection") or {}).get("isp", ""), "ms": ms, "error": ""}
    except Exception as e:
        first = e
    try:  # запасной вариант: хотя бы внешний IP
        t0 = time.time()
        r = get("https://api.ipify.org?format=json", proxies=proxies, timeout=timeout)
        return {"ok": True, "ip": r.json().get("ip", ""), "country": "", "code": "", "city": "", "isp": "",
                "ms": int((time.time() - t0) * 1000), "error": ""}
    except Exception:
        pass
    msg = re.sub(r"\s+", " ", str(first))
    low = msg.lower()
    if "407" in msg or "authentication" in low:
        msg = "Прокси требует логин/пароль или они неверные"
    elif "timed out" in low or "timeout" in low:
        msg = "Прокси не ответил за отведённое время"
    elif "refused" in low:
        msg = "Прокси отклонил соединение (неверный порт или выключен)"
    elif "socks" in low and "missing" in low:
        msg = "Для SOCKS нужен пакет PySocks: pip install pysocks"
    return {"ok": False, "ip": "", "country": "", "code": "", "city": "", "isp": "", "ms": 0, "error": msg[:200]}
