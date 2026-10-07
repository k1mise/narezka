"""Публикация Reels в Instagram через Instagram Platform API with Facebook Login (graph.facebook.com).

Нужно: профессиональный аккаунт Instagram (Business/Creator), привязанный к Facebook-странице, и приложение Meta
(App ID / App Secret). Видео заливается напрямую (resumable upload, rupload.facebook.com) — публичный хостинг не нужен.
Отложенной публикации у API нет: время держит narez (см. service.run_due)."""
from __future__ import annotations

import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path

import requests

from ..oauth_local import wait_for_callback
from .base import Publisher, token_path

SCOPES = ["instagram_basic", "instagram_content_publish", "pages_show_list", "pages_read_engagement"]


class InstagramError(RuntimeError):
    pass


def _check(r: requests.Response) -> dict:
    try:
        j = r.json()
    except ValueError:
        raise InstagramError(f"Instagram вернул не-JSON (HTTP {r.status_code}): {r.text[:200]}") from None
    if r.status_code >= 400 or "error" in j:
        e = j.get("error", {}) if isinstance(j, dict) else {}
        msg = e.get("error_user_msg") or e.get("message") or str(j)[:300]
        raise InstagramError(f"Instagram: {msg} (код {e.get('code', r.status_code)})")
    return j


class InstagramPublisher(Publisher):
    platform = "instagram"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ig = cfg.get("instagram", {})
        self.ver = self.ig.get("graph_version", "v23.0")
        self.base = f"https://graph.facebook.com/{self.ver}"

    # --- ключи приложения: .env главнее config.toml ---
    def app_id(self) -> str:
        return os.environ.get("INSTAGRAM_APP_ID") or str(self.ig.get("app_id", ""))

    def app_secret(self) -> str:
        return os.environ.get("INSTAGRAM_APP_SECRET") or str(self.ig.get("app_secret", ""))

    def redirect_port(self) -> int:
        return int(self.ig.get("redirect_port", 8765))

    def redirect_uri(self) -> str:
        return f"http://localhost:{self.redirect_port()}/callback"

    def token_path(self, account_id: int) -> Path:
        return token_path(self.cfg, "instagram", account_id)

    # --- вход ---
    def authorize(self, account_id: int, port: int = 0, timeout: int = 300, label: str = "") -> str:
        if not (self.app_id() and self.app_secret()):
            raise InstagramError("Не заданы INSTAGRAM_APP_ID и INSTAGRAM_APP_SECRET (файл .env). См. README, раздел «Instagram».")
        port = self.redirect_port()  # Meta требует точного совпадения redirect URI, поэтому порт фиксированный
        state = secrets.token_urlsafe(16)
        url = f"https://www.facebook.com/{self.ver}/dialog/oauth?" + urllib.parse.urlencode({
            "client_id": self.app_id(), "redirect_uri": self.redirect_uri(), "state": state,
            "scope": ",".join(SCOPES), "response_type": "code", "auth_type": "reauthenticate"})
        import webbrowser
        webbrowser.open(url)
        got = wait_for_callback(port, timeout)
        if got.get("state") != state:
            raise InstagramError("Ответ входа не совпал с запросом (state). Попробуй ещё раз.")
        short = _check(requests.get(f"{self.base}/oauth/access_token", params={
            "client_id": self.app_id(), "client_secret": self.app_secret(),
            "redirect_uri": self.redirect_uri(), "code": got["code"]}, timeout=30))["access_token"]
        long = _check(requests.get(f"{self.base}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": self.app_id(), "client_secret": self.app_secret(),
            "fb_exchange_token": short}, timeout=30))["access_token"]
        # токен страницы, полученный по долгоживущему токену пользователя, не истекает
        pages = _check(requests.get(f"{self.base}/me/accounts", params={
            "fields": "name,access_token,instagram_business_account{id,username}", "limit": 100,
            "access_token": long}, timeout=30)).get("data", [])
        withig = [p for p in pages if p.get("instagram_business_account")]
        if not withig:
            raise InstagramError("Не нашёл Instagram-аккаунт, привязанный к твоим Facebook-страницам. Аккаунт должен быть "
                                 "Business/Creator и привязан к странице (Instagram → Настройки → Связанные аккаунты).")
        pick = next((p for p in withig if label and label.lower() in p["instagram_business_account"].get("username", "").lower()), withig[0])
        ig = pick["instagram_business_account"]
        data = {"ig_user_id": ig["id"], "username": ig.get("username", ""), "page_id": pick["id"],
                "page_name": pick.get("name", ""), "access_token": pick["access_token"], "saved_at": int(time.time()),
                "other": [p["instagram_business_account"].get("username", "") for p in withig if p is not pick]}
        p = self.token_path(account_id)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data["username"]

    def _tok(self, account_id: int) -> dict:
        p = self.token_path(account_id)
        if not p.exists():
            raise InstagramError("Instagram-аккаунт не авторизован. Нажми «Войти».")
        return json.loads(p.read_text(encoding="utf-8"))

    # --- публикация Reels ---
    def publish(self, video_path, title, description, credentials, publish_at=None, tags=None) -> str:
        t = self._tok(credentials["account_id"])
        token, igid = t["access_token"], t["ig_user_id"]
        caption = (title.strip() + ("\n\n" + description.strip() if description.strip() else ""))[:2200]
        size = os.path.getsize(video_path)
        c = _check(requests.post(f"{self.base}/{igid}/media", data={
            "media_type": "REELS", "upload_type": "resumable", "caption": caption, "share_to_feed": "true",
            "access_token": token}, timeout=60))
        cid = c["id"]
        with open(video_path, "rb") as f:
            r = requests.post(f"https://rupload.facebook.com/ig-api-upload/{self.ver}/{cid}", data=f, timeout=1800,
                              headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
        _check(r)
        for _ in range(60):  # до ~10 минут обработки
            s = _check(requests.get(f"{self.base}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=30))
            code = s.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Instagram не принял видео: {s.get('status') or code}")
            time.sleep(10)
        else:
            raise InstagramError("Instagram слишком долго обрабатывает видео. Позже проверь профиль — оно могло опубликоваться.")
        m = _check(requests.post(f"{self.base}/{igid}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=60))
        try:
            link = _check(requests.get(f"{self.base}/{m['id']}", params={"fields": "permalink", "access_token": token}, timeout=30))["permalink"]
        except Exception:
            link = f"https://www.instagram.com/{t.get('username', '')}/"
        return link
