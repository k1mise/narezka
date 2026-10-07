"""Запуск narez: python run.py  — поднимает веб-интерфейс на localhost и открывает браузер."""
from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser


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
    args = ap.parse_args()

    import uvicorn
    from narez.env import load_env
    from narez.config import load_config

    if not load_env(".env"):
        print("Файл .env не найден — он нужен только для Telegram (Этап 3), пока можно не создавать.")
    from narez.web.server import create_app

    port = free_port(args.port)
    url = f"http://localhost:{port}/"
    app = create_app(load_config())
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    print(f"narez работает: {url}   (остановить: Ctrl+C)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
