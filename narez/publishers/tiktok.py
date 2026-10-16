"""Публикация в TikTok через Content Posting API (загрузка файла кусками).

Два режима (`[tiktok] mode`): «inbox» (по умолчанию) — видео уходит в черновики приложения TikTok, а публикует/планирует его
сам пользователь (нужен только scope video.upload); «direct» — Direct Post сразу в ленту (нужен аудит приложения,
до него — только «Только я» и закрытый аккаунт).
Отложенной публикации у API нет: время держит narez (см. service.run_due)."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path

import requests

from ..oauth_local import wait_for_callback
from .base import Publisher, token_path

API = "https://open.tiktokapis.com"
SCOPES = ["user.info.basic", "video.publish", "video.upload"]
CHUNK = 10 * 1024 * 1024  # TikTok: кусок 5–64 МБ; видео меньше 5 МБ — одним куском


class TikTokError(RuntimeError):
    pass


def _check(r: requests.Response) -> dict:
    try:
        j = r.json()
    except ValueError:
        raise TikTokError(f"TikTok вернул не-JSON (HTTP {r.status_code}): {r.text[:200]}") from None
    err = j.get("error")
    bad = (isinstance(err, dict) and err.get("code") not in (None, "", "ok")) or (isinstance(err, str) and err) or r.status_code >= 400
    if bad:
        code = err.get("code") if isinstance(err, dict) else (err or r.status_code)
        msg = (err.get("message") if isinstance(err, dict) else None) or j.get("error_description") or ""
        hint = {"unaudited_client_can_only_post_to_private_accounts":
                "приложение TikTok ещё не прошло аудит: публиковать можно только на закрытый аккаунт (поставь в настройках аккаунта «Приватный»).",
                "spam_risk_too_many_posts": "достигнут дневной лимит публикаций TikTok.",
                "access_token_invalid": "токен недействителен, нажми «Войти» заново."}.get(code, "")
        raise TikTokError(f"TikTok: {hint or msg or j} ({code})")
    return j


class TikTokPublisher(Publisher):
    platform = "tiktok"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tt = cfg.get("tiktok", {})

    def client_key(self) -> str:
        return os.environ.get("TIKTOK_CLIENT_KEY") or str(self.tt.get("client_key", ""))

    def client_secret(self) -> str:
        return os.environ.get("TIKTOK_CLIENT_SECRET") or str(self.tt.get("client_secret", ""))

    def mode(self) -> str:
        return "direct" if str(self.tt.get("mode", "inbox")).lower() == "direct" else "inbox"

    def redirect_port(self) -> int:
        return int(self.tt.get("redirect_port", 8766))

    def redirect_uri(self) -> str:
        return f"http://localhost:{self.redirect_port()}/callback/"

    def token_path(self, account_id: int) -> Path:
        return token_path(self.cfg, "tiktok", account_id)

    # --- вход (PKCE) ---
    def authorize(self, account_id: int, port: int = 0, timeout: int = 300, label: str = "") -> str:
        if not (self.client_key() and self.client_secret()):
            raise TikTokError("Не заданы TIKTOK_CLIENT_KEY и TIKTOK_CLIENT_SECRET (файл .env). См. README, раздел «TikTok».")
        port = self.redirect_port()
        state = secrets.token_urlsafe(16)
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = hashlib.sha256(verifier.encode()).hexdigest()  # TikTok требует hex-вариант SHA256, не base64url
        url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
            "client_key": self.client_key(), "scope": ",".join(SCOPES), "response_type": "code",
            "redirect_uri": self.redirect_uri(), "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        import webbrowser
        webbrowser.open(url)
        got = wait_for_callback(port, timeout)
        if got.get("state") != state:
            raise TikTokError("Ответ входа не совпал с запросом (state). Попробуй ещё раз.")
        tok = _check(requests.post(f"{API}/v2/oauth/token/", data={
            "client_key": self.client_key(), "client_secret": self.client_secret(), "code": got["code"],
            "grant_type": "authorization_code", "redirect_uri": self.redirect_uri(), "code_verifier": verifier},
            headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30))
        data = self._store(account_id, tok)
        try:
            u = _check(requests.get(f"{API}/v2/user/info/", params={"fields": "display_name,username"},
                                    headers={"Authorization": f"Bearer {data['access_token']}"}, timeout=30))
            ud = u.get("data", {}).get("user", {})
            data["username"] = ud.get("username") or ud.get("display_name", "")
            self.token_path(account_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return data.get("username", "")

    def _store(self, account_id: int, tok: dict, old: dict | None = None) -> dict:
        now = int(time.time())
        data = dict(old or {})
        data.update({"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", data.get("refresh_token", "")),
                     "open_id": tok.get("open_id", data.get("open_id", "")),
                     "expires_at": now + int(tok.get("expires_in", 86400)),
                     "refresh_expires_at": now + int(tok.get("refresh_expires_in", 365 * 86400))})
        self.token_path(account_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def _access(self, account_id: int) -> tuple[str, dict]:
        p = self.token_path(account_id)
        if not p.exists():
            raise TikTokError("TikTok-аккаунт не авторизован. Нажми «Войти».")
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("expires_at", 0) - 120 < time.time():  # access-токен живёт сутки: обновляем по refresh-токену
            tok = _check(requests.post(f"{API}/v2/oauth/token/", data={
                "client_key": self.client_key(), "client_secret": self.client_secret(),
                "grant_type": "refresh_token", "refresh_token": d.get("refresh_token", "")},
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30))
            d = self._store(account_id, tok, d)
        return d["access_token"], d

    # --- публикация ---
    def _upload(self, H: dict, init_url: str, body: dict, video_path: str) -> str:
        size = os.path.getsize(video_path)
        chunk = size if size < 5 * 1024 * 1024 else CHUNK
        total = max(1, size // chunk)  # последний кусок забирает остаток
        body["source_info"] = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": total}
        init = _check(requests.post(init_url, headers=H, data=json.dumps(body), timeout=60))["data"]
        pid, up = init["publish_id"], init["upload_url"]
        with open(video_path, "rb") as f:
            for i in range(total):
                start = i * chunk
                end = size - 1 if i == total - 1 else start + chunk - 1
                f.seek(start)
                data = f.read(end - start + 1)
                r = requests.put(up, data=data, timeout=600, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(data)), "Content-Range": f"bytes {start}-{end}/{size}"})
                if r.status_code not in (200, 201, 206):
                    raise TikTokError(f"TikTok не принял кусок {i + 1}/{total}: HTTP {r.status_code} {r.text[:150]}")
        return pid

    def _wait(self, H: dict, pid: str, done: tuple) -> dict:
        for _ in range(60):
            s = _check(requests.post(f"{API}/v2/post/publish/status/fetch/", headers=H, data=json.dumps({"publish_id": pid}), timeout=30)).get("data", {})
            st = s.get("status")
            if st in done:
                return s
            if st == "FAILED":
                raise TikTokError(f"TikTok отклонил видео: {s.get('fail_reason', 'без причины')}")
            time.sleep(5)
        raise TikTokError("TikTok слишком долго обрабатывает видео. Проверь черновики/профиль позже — оно могло дойти.")

    def publish(self, video_path, title, description, credentials, publish_at=None, tags=None) -> str:
        token, d = self._access(credentials["account_id"])
        H = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
        user = d.get("username") or d.get("open_id", "")
        if self.mode() == "inbox":
            # черновики: подпись и параметры API не принимает — их задаёт пользователь в приложении TikTok
            pid = self._upload(H, f"{API}/v2/post/publish/inbox/video/init/", {}, video_path)
            self._wait(H, pid, ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"))
            return f"https://www.tiktok.com/@{user}#draft-{pid}"
        info = _check(requests.post(f"{API}/v2/post/publish/creator_info/query/", headers=H, timeout=30)).get("data", {})
        options = info.get("privacy_level_options") or ["SELF_ONLY"]
        want = str(self.tt.get("privacy", "PUBLIC_TO_EVERYONE"))
        privacy = want if want in options else ("SELF_ONLY" if "SELF_ONLY" in options else options[0])
        caption = (title.strip() + (" " + " ".join("#" + t for t in tags) if tags else ""))[:2200]
        body = {"post_info": {"title": caption, "privacy_level": privacy,
                              "disable_duet": bool(self.tt.get("disable_duet", False)),
                              "disable_comment": bool(self.tt.get("disable_comment", False)),
                              "disable_stitch": bool(self.tt.get("disable_stitch", False))}}
        pid = self._upload(H, f"{API}/v2/post/publish/video/init/", body, video_path)
        s = self._wait(H, pid, ("PUBLISH_COMPLETE",))
        ids = s.get("publicaly_available_post_id") or s.get("publicly_available_post_id") or []
        return f"https://www.tiktok.com/@{user}/video/{ids[0]}" if ids else f"https://www.tiktok.com/@{user}#{pid}"
