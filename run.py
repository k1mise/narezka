"""Запуск narez: python run.py  — поднимает веб-интерфейс на localhost и открывает браузер."""
from __future__ import annotations

import argparse
import socket
import threading
import time


def free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("Не нашёл свободный порт рядом с " + str(start))


def main() -> None:
    ap = argparse.ArgumentParser(description="narez — веб-интерфейс")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--browser", default=None, help="chrome, edge, firefox, brave, opera, yandex или путь к exe (по умолчанию — [ui] browser из config.toml, иначе браузер системы)")
    ap.add_argument("--profile", default=None, help="профиль браузера: почта Google-аккаунта, имя профиля или папка («Profile 2»); по умолчанию — [ui] profile")
    ap.add_argument("--list-profiles", action="store_true", help="показать профили браузера и выйти")
    args = ap.parse_args()
    if args.list_profiles:
        from narez import browser
        ps = browser.profiles(args.browser or "chrome")
        print("\n".join(f"{p['dir']:12} {p['email'] or '—':35} {p['name']}" for p in ps) or "Профили не найдены")
        return

    import uvicorn
    from narez.env import load_env
    from narez.config import load_config

    if not load_env(".env"):
        print("Файл .env не найден — он нужен только для Telegram (Этап 3), пока можно не создавать.")
    from narez.web.server import create_app

    port = free_port(args.port)
    url = f"http://localhost:{port}/"
    cfg = load_config()
    app = create_app(cfg)
    if not args.no_browser:
        from narez import browser
        name = args.browser if args.browser is not None else str((cfg.get("ui") or {}).get("browser", "") or "")
        profile = args.profile if args.profile is not None else str((cfg.get("ui") or {}).get("profile", "") or "")
        threading.Thread(target=lambda: (time.sleep(1.2), browser.open_url(url, name, print, profile)), daemon=True).start()
    print(f"narez работает: {url}   (остановить: Ctrl+C)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
