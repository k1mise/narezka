"""Каким браузером открывать narez при запуске: из config.toml ([ui] browser) или из ключа --browser."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

# имя -> (команда в PATH, типичные пути установки в Windows)
KNOWN = {
    "chrome": ("chrome", [r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe", r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
                          r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"]),
    "edge": ("msedge", [r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe", r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"]),
    "firefox": ("firefox", [r"%PROGRAMFILES%\Mozilla Firefox\firefox.exe", r"%PROGRAMFILES(X86)%\Mozilla Firefox\firefox.exe"]),
    "brave": ("brave", [r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe",
                        r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"]),
    "opera": ("opera", [r"%LOCALAPPDATA%\Programs\Opera\opera.exe", r"%PROGRAMFILES%\Opera\opera.exe"]),
    "yandex": ("browser", [r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe"]),
}
ALIASES = {"google chrome": "chrome", "googlechrome": "chrome", "msedge": "edge", "microsoft edge": "edge", "яндекс": "yandex", "yandexbrowser": "yandex"}


def find(name: str) -> str | None:
    """Путь к exe браузера по имени (chrome/edge/firefox/brave/opera/yandex) или по прямому пути; None — не нашли."""
    name = (name or "").strip().strip('"')
    if not name or name.lower() in ("default", "system", "по умолчанию"):
        return None
    if Path(name).is_file():
        return name
    key = ALIASES.get(name.lower(), name.lower())
    cmd, paths = KNOWN.get(key, (name, []))
    for p in paths:
        p = os.path.expandvars(p)
        if Path(p).is_file():
            return p
    return shutil.which(cmd)


# где у Chromium-браузеров лежат профили (в каждом профиле — свой вход в Google-аккаунт и свои расширения)
USER_DATA = {
    "chrome": r"%LOCALAPPDATA%\Google\Chrome\User Data",
    "edge": r"%LOCALAPPDATA%\Microsoft\Edge\User Data",
    "brave": r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data",
    "yandex": r"%LOCALAPPDATA%\Yandex\YandexBrowser\User Data",
}


def _key(name: str) -> str:
    n = (name or "").strip().strip('"')
    if Path(n).name:
        n = Path(n).stem if Path(n).suffix.lower() == ".exe" else n
    n = ALIASES.get(n.lower(), n.lower())
    return {"msedge": "edge", "browser": "yandex"}.get(n, n)


def profiles(name: str = "chrome", user_data: str | None = None) -> list[dict]:
    """Профили браузера: [{dir: "Profile 3", name: "Рабочий", email: "a@gmail.com"}] — из файла Local State."""
    base = Path(os.path.expandvars(user_data or USER_DATA.get(_key(name), "")))
    try:
        info = json.loads((base / "Local State").read_text(encoding="utf-8")).get("profile", {}).get("info_cache", {})
    except (OSError, ValueError):
        return []
    return [{"dir": d, "name": v.get("name", ""), "email": v.get("user_name", "") or v.get("gaia_name", "")} for d, v in info.items()]


def find_profile(profile: str, name: str = "chrome", user_data: str | None = None) -> str | None:
    """Папка профиля по почте Google-аккаунта, отображаемому имени профиля или имени папки («Default», «Profile 2»)."""
    want = (profile or "").strip().strip('"').lower()
    if not want:
        return None
    items = profiles(name, user_data)
    for key in ("email", "name", "dir"):
        for p in items:
            if p[key] and p[key].lower() == want:
                return p["dir"]
    return None


def open_url(url: str, name: str = "", log=print, profile: str = "") -> bool:
    """Открывает url в выбранном браузере; если он не найден — в браузере по умолчанию. Возвращает True, если открыт именно выбранный."""
    if profile and not name:
        name = "chrome"               # профиль бывает только у Chromium-браузеров; по умолчанию берём Chrome
    exe = find(name)
    if name and not exe and name.strip().lower() not in ("default", "system", "по умолчанию"):
        log(f"Браузер «{name}» не найден — открываю в браузере по умолчанию. Можно указать полный путь к exe в [ui] browser.")
    extra: list[str] = []
    if exe and profile:
        pdir = find_profile(profile, name)
        if pdir:
            extra = [f"--profile-directory={pdir}"]
        else:
            have = ", ".join(f"{p['email'] or p['name']} ({p['dir']})" for p in profiles(name)) or "профили не найдены"
            log(f"Профиль «{profile}» не найден — открываю без него. Есть: {have}.")
    if exe:
        try:
            subprocess.Popen([exe, *extra, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) if sys.platform.startswith("win") else 0)
            return True
        except OSError as e:
            log(f"Не удалось запустить {exe}: {e} — открываю в браузере по умолчанию.")
    webbrowser.open(url)
    return False
