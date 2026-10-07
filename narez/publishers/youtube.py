"""Публикация в YouTube Shorts через официальный YouTube Data API v3 (OAuth, resumable upload)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .base import Publisher

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"  # для статистики (videos.list, 1 ед. квоты на 50 видео)
SCOPES = [UPLOAD_SCOPE, READONLY_SCOPE]


class ScopeError(RuntimeError):
    """Токен выдан до появления доступа на чтение статистики: нужно один раз войти в YouTube заново."""


class YouTubePublisher(Publisher):
    platform = "youtube"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.yt = cfg["youtube"]

    # --- авторизация: один токен на аккаунт ---
    def token_path(self, account_id: int) -> Path:
        d = self.cfg["paths"]["workdir"] / "tokens"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"youtube_{account_id}.json"

    def secrets_path(self) -> Path:
        p = Path(self.yt["client_secrets"])
        return p if p.is_absolute() else Path(self.cfg["_root"]) / p

    def authorize(self, account_id: int, port: int = 0, timeout: int = 300) -> str:
        """Открывает браузер: войди в нужный Google-аккаунт и выбери канал. Токен сохраняется локально."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        sp = self.secrets_path()
        if not sp.exists():
            raise FileNotFoundError(f"Нет файла {sp}. См. README, раздел «YouTube API».")
        # select_account: Google всегда показывает выбор аккаунта (иначе берёт тот, что открыт в браузере по умолчанию)
        creds = InstalledAppFlow.from_client_secrets_file(str(sp), SCOPES).run_local_server(
            port=port, timeout_seconds=timeout, prompt="select_account consent")
        path = self.token_path(account_id)
        path.write_text(creds.to_json(), encoding="utf-8")
        return str(path)

    def _creds(self, account_id: int):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        path = self.token_path(account_id)
        if not path.exists():
            raise RuntimeError("YouTube-аккаунт не авторизован. Нажми «Войти в YouTube».")
        # права берём из самого файла токена: старые токены (только загрузка) продолжают работать
        creds = Credentials.from_authorized_user_file(str(path))
        if not creds.valid:
            creds.refresh(Request())
            path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def _service(self, account_id: int):
        from googleapiclient.discovery import build

        return build("youtube", "v3", credentials=self._creds(account_id), cache_discovery=False)

    def fetch_stats(self, account_id: int, video_ids: list[str]) -> dict[str, dict]:
        """Просмотры/лайки/комментарии и статус видео. Квота: 1 единица на запрос до 50 видео."""
        creds = self._creds(account_id)
        if not creds.has_scopes([READONLY_SCOPE]):
            raise ScopeError("Для статистики нужно один раз заново войти в YouTube (кнопка «Войти в YouTube»).")
        from googleapiclient.discovery import build

        svc = build("youtube", "v3", credentials=creds, cache_discovery=False)
        out: dict[str, dict] = {}
        for i in range(0, len(video_ids), 50):
            r = svc.videos().list(part="snippet,statistics,status", id=",".join(video_ids[i:i + 50])).execute()
            for it in r.get("items", []):
                st, status, sn = it.get("statistics", {}), it.get("status", {}), it.get("snippet", {})
                out[it["id"]] = {
                    "title": sn.get("title", ""), "published": sn.get("publishedAt", ""),
                    "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                    "comments": int(st.get("commentCount", 0)),
                    "privacy": status.get("privacyStatus", ""), "publish_at": status.get("publishAt", "")}
        for vid in video_ids:
            out.setdefault(vid, {"title": "", "published": "", "views": None, "likes": None, "comments": None, "privacy": "deleted", "publish_at": ""})
        return out

    # --- загрузка ---
    def build_body(self, title: str, description: str, publish_at=None, tags: list[str] | None = None) -> dict:
        title = title.strip()[:90]  # запас под " #Shorts"
        if self.yt["add_shorts_tag"] and "#shorts" not in (title + description).lower():
            description = (description + "\n\n#Shorts").strip()
        status = {"privacyStatus": self.yt["privacy"],
                  "selfDeclaredMadeForKids": bool(self.yt["made_for_kids"])}
        if publish_at:  # отложенная публикация на стороне YouTube: видео должно быть private
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        snippet = {"title": title, "description": description[:4900], "categoryId": str(self.yt["category_id"])}
        if tags:
            snippet["tags"] = list(tags)
        return {"snippet": snippet, "status": status}

    def publish(self, video_path, title, description, credentials, publish_at=None, tags=None) -> str:
        from googleapiclient.http import MediaFileUpload

        svc = self._service(credentials["account_id"])
        media = MediaFileUpload(video_path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        req = svc.videos().insert(part="snippet,status", body=self.build_body(title, description, publish_at, tags),
                                  media_body=media)
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        return f"https://youtube.com/shorts/{resp['id']}"
