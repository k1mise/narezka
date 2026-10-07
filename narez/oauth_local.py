"""Локальный приём ответа OAuth: браузер возвращается на http://localhost:PORT/callback?code=...

Работает для Instagram (Facebook Login) и TikTok (Login Kit). Отмена входа — запрос на тот же адрес с ?error=cancelled."""
from __future__ import annotations

import http.server
import threading
import time
import urllib.parse

PAGE_OK = "<html><meta charset=utf-8><body style='font-family:sans-serif;padding:40px'><h2>Готово</h2>Можно закрыть эту вкладку и вернуться в narez.</body></html>"
PAGE_ERR = "<html><meta charset=utf-8><body style='font-family:sans-serif;padding:40px'><h2>Вход не выполнен</h2>Вернись в narez и попробуй ещё раз.</body></html>"


class OAuthError(RuntimeError):
    pass


def wait_for_callback(port: int, timeout: int = 300) -> dict:
    """Поднимает сервер на 127.0.0.1:port и ждёт ответа провайдера. Возвращает параметры запроса (code, state, ...)."""
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
            if "code" in q or "error" in q:
                got.update(q)
            body = (PAGE_OK if "code" in q else PAGE_ERR).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # тишина в консоли
            pass

    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    except OSError as e:
        raise OAuthError(f"Порт {port} занят ({e}). Закрой программу, которая его использует, или поменяй redirect_port в config.toml.") from None
    srv.timeout = 1
    stop = time.time() + timeout
    try:
        while not got and time.time() < stop:
            srv.handle_request()
    finally:
        srv.server_close()
    if not got:
        raise OAuthError("Время на вход вышло. Нажми «Войти» ещё раз.")
    if "error" in got:
        raise OAuthError("Вход отменён" if got["error"] == "cancelled" else
                         f"Вход отклонён: {got.get('error_description') or got['error']}")
    return got
