"""Instagram / TikTok: публикация (с подменой сети), очередь по времени, приём OAuth."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.request

import pytest
import requests

from narez.db import DB
from test_web import add_clip, env, wait_task  # noqa: F401


class Resp:
    def __init__(self, j, status=200):
        self._j, self.status_code, self.text = j, status, json.dumps(j)

    def json(self):
        return self._j


def test_instagram_reels_flow(env, monkeypatch, tmp_path):
    c, cfg, db, app = env
    from narez.publishers import instagram as ig
    pub = ig.InstagramPublisher(cfg)
    pub.token_path(7).write_text(json.dumps({"access_token": "PT", "ig_user_id": "123", "username": "me"}))
    v = tmp_path / "v.mp4"; v.write_bytes(b"x" * 1000)
    calls = []

    def post(url, data=None, headers=None, **k):
        calls.append(("POST", url, data if isinstance(data, dict) else "<file>", headers))
        if url.endswith("/123/media"):
            return Resp({"id": "C1"})
        if "rupload" in url:
            return Resp({"success": True})
        return Resp({"id": "M1"})

    def get(url, params=None, **k):
        if url.endswith("/C1"):
            return Resp({"status_code": "FINISHED"})
        return Resp({"permalink": "https://www.instagram.com/reel/XYZ/"})
    monkeypatch.setattr(ig.requests, "post", post)
    monkeypatch.setattr(ig.requests, "get", get)
    url = pub.publish(str(v), "Заголовок", "Описание #тег", {"account_id": 7})
    assert url == "https://www.instagram.com/reel/XYZ/"
    first = calls[0][2]
    assert first["media_type"] == "REELS" and first["upload_type"] == "resumable" and "Заголовок" in first["caption"]
    up = calls[1]
    assert "rupload.facebook.com/ig-api-upload" in up[1] and up[3]["file_size"] == "1000" and up[3]["Authorization"] == "OAuth PT"
    assert calls[2][2]["creation_id"] == "C1"


def test_instagram_error_message(env, monkeypatch, tmp_path):
    c, cfg, db, app = env
    from narez.publishers import instagram as ig
    pub = ig.InstagramPublisher(cfg)
    pub.token_path(1).write_text(json.dumps({"access_token": "T", "ig_user_id": "1"}))
    v = tmp_path / "v.mp4"; v.write_bytes(b"x")
    monkeypatch.setattr(ig.requests, "post", lambda *a, **k: Resp({"error": {"message": "Invalid OAuth", "code": 190}}, 400))
    with pytest.raises(ig.InstagramError, match="Invalid OAuth"):
        pub.publish(str(v), "t", "d", {"account_id": 1})


def test_tiktok_chunks_and_privacy(env, monkeypatch, tmp_path):
    c, cfg, db, app = env
    from narez.publishers import tiktok as tt
    pub = tt.TikTokPublisher(cfg)
    pub.token_path(3).write_text(json.dumps({"access_token": "AT", "refresh_token": "R", "username": "me",
                                             "expires_at": time.time() + 9999}))
    v = tmp_path / "v.mp4"; v.write_bytes(b"a" * (25 * 1024 * 1024))
    init_body, ranges = {}, []

    def post(url, headers=None, data=None, **k):
        if "creator_info" in url:
            return Resp({"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if "video/init" in url:
            init_body.update(json.loads(data))
            return Resp({"data": {"publish_id": "P1", "upload_url": "https://up.example/x"}, "error": {"code": "ok"}})
        return Resp({"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["555"]}, "error": {"code": "ok"}})

    def put(url, data=None, headers=None, **k):
        ranges.append(headers["Content-Range"])
        assert int(headers["Content-Length"]) == len(data)
        return Resp({}, 201)
    monkeypatch.setattr(tt.requests, "post", post)
    monkeypatch.setattr(tt.requests, "put", put)
    url = pub.publish(str(v), "Название", "", {"account_id": 3}, tags=["a", "b"])
    assert url == "https://www.tiktok.com/@me/video/555"
    assert init_body["post_info"]["privacy_level"] == "SELF_ONLY"  # PUBLIC запрошен, но аккаунту доступно только SELF_ONLY
    assert init_body["post_info"]["title"] == "Название #a #b"
    n = 25 * 1024 * 1024
    assert init_body["source_info"]["total_chunk_count"] == 2
    assert ranges == [f"bytes 0-{10 * 1024 * 1024 - 1}/{n}", f"bytes {10 * 1024 * 1024}-{n - 1}/{n}"]


def test_tiktok_error_hint():
    from narez.publishers.tiktok import TikTokError, _check
    with pytest.raises(TikTokError, match="аудит"):
        _check(Resp({"error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "x"}}, 403))


def test_future_clip_goes_to_queue_and_runs_when_due(env, monkeypatch):
    c, cfg, db, app = env
    from narez import service
    from narez.publishers.base import Publisher
    g = db.add_group("g"); db.set_account(g, "tiktok", "tt")
    cid = add_clip(db, cfg, g, "Клип", 0)
    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c.patch(f"/api/clips/{cid}", json={"publish_at": later})
    sent = []

    class Fake(Publisher):
        platform = "tiktok"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            sent.append(when)
            return "https://www.tiktok.com/@me/video/1"
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Fake())
    r = wait_task(c, c.post("/api/publish", json={"group_id": g}).json())
    assert r["result"].get("scheduled") == 1 and r["result"]["published"] == 0 and sent == []
    q = c.get("/api/scheduled").json()
    assert len(q) == 1 and q[0]["platform"] == "tiktok" and q[0]["status"] == "wait"
    # повторное нажатие не плодит дубли
    wait_task(c, c.post("/api/publish", json={"group_id": g}).json())
    assert len(c.get("/api/scheduled").json()) == 1
    # время не пришло — ничего не происходит
    assert service.run_due(DB(cfg["paths"]["db"]), cfg) == 0 and sent == []
    # время пришло
    db.c.execute("UPDATE deferred SET run_at='2000-01-01T00:00:00Z'"); db.c.commit()
    assert service.run_due(DB(cfg["paths"]["db"]), cfg) == 1 and sent == [None]
    assert c.get("/api/scheduled").json() == []
    h = c.get("/api/history").json()
    assert len(h) == 1 and h[0]["platform"] == "tiktok" and h[0]["status"] == "Опубликовано"
    assert c.get(f"/api/groups/{g}/clips").json() == []


def test_cancel_queue_and_discarded_clip(env, monkeypatch):
    c, cfg, db, app = env
    from narez import service
    g = db.add_group("g"); db.set_account(g, "instagram", "ig"); acc = db.accounts(g)[0]["id"]
    cid = add_clip(db, cfg, g, "Клип", 0)
    did = db.add_deferred(cid, acc, "2000-01-01T00:00:00Z")
    c.delete(f"/api/clips/{cid}")
    assert service.run_due(DB(cfg["paths"]["db"]), cfg) == 0
    assert c.get("/api/scheduled").json() == []  # отменённая запись в очереди не показывается
    assert db.c.execute("SELECT status FROM deferred WHERE id=?", (did,)).fetchone()[0] == "cancel"


def test_login_without_keys_explains(env, monkeypatch):
    c, cfg, db, app = env
    for k in ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    g = db.add_group("g"); db.set_account(g, "instagram", "ig"); db.set_account(g, "tiktok", "tt")
    for a in db.accounts(g):
        r = wait_task(c, c.post(f"/api/accounts/{a['id']}/login").json())
        assert r["status"] == "error" and ".env" in r["error"]
    gr = c.get("/api/groups").json()[0]
    assert all(a["supported"] and not a["authorized"] for a in gr["accounts"])


def test_oauth_local_callback_and_cancel():
    from narez.oauth_local import OAuthError, wait_for_callback
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    out = {}

    def run():
        try:
            out["r"] = wait_for_callback(port, 10)
        except Exception as e:
            out["e"] = e
    t = threading.Thread(target=run); t.start(); time.sleep(0.5)
    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc&state=s").read()
    t.join(); assert out["r"]["code"] == "abc" and out["r"]["state"] == "s"
    out.clear()
    t = threading.Thread(target=run); t.start(); time.sleep(0.5)
    urllib.request.urlopen(f"http://127.0.0.1:{port}/?error=cancelled").read()
    t.join(); assert isinstance(out["e"], OAuthError) and "отменён" in str(out["e"])


def test_youtube_own_keys_per_account(env, monkeypatch):
    c, cfg, db, app = env
    from narez.publishers.youtube import YouTubePublisher
    for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    g = db.add_group("g"); db.set_account(g, "youtube", "yt"); db.set_account(g, "tiktok", "tt")
    byp = {a["platform"]: a["id"] for a in db.accounts(g)}
    yt, tt = byp["youtube"], byp["tiktok"]
    ok_id = "123-abc.apps.googleusercontent.com"
    assert c.put(f"/api/accounts/{yt}/keys", json={"client_id": ok_id}).status_code == 400            # нужны оба поля
    assert c.put(f"/api/accounts/{yt}/keys", json={"client_id": "oops", "client_secret": "s"}).status_code == 400
    assert c.put(f"/api/accounts/{tt}/keys", json={"client_id": ok_id, "client_secret": "s"}).status_code == 400
    assert c.put("/api/accounts/999/keys", json={}).status_code == 404
    assert c.put(f"/api/accounts/{yt}/keys", json={"client_id": f" {ok_id} ", "client_secret": " GOCSPX-x "}).status_code == 200
    acc = next(a for a in c.get("/api/groups").json()[0]["accounts"] if a["id"] == yt)
    assert acc["has_keys"] and "GOCSPX" not in json.dumps(c.get("/api/groups").json())              # секрет наружу не отдаётся
    seen = {}

    def fake_auth(self, account_id, port=0, timeout=300, client=None):
        seen.update(client=client, cfg=self.client_config(client))
        self.token_path(account_id).write_text("{}")
        return "x"
    monkeypatch.setattr(YouTubePublisher, "authorize", fake_auth)
    assert wait_task(c, c.post(f"/api/accounts/{yt}/login").json())["status"] == "done"
    assert seen["client"] == {"client_id": ok_id, "client_secret": "GOCSPX-x"}
    assert seen["cfg"]["installed"]["client_id"] == ok_id and seen["cfg"]["installed"]["token_uri"].startswith("https://")
    # сброс: снова общие ключи; без файла и .env понятная ошибка
    assert c.put(f"/api/accounts/{yt}/keys", json={}).status_code == 200
    assert not next(a for a in c.get("/api/groups").json()[0]["accounts"] if a["id"] == yt)["has_keys"]
    import pytest as _p
    with _p.raises(FileNotFoundError, match="Ключи"):
        YouTubePublisher(cfg).client_config(None)
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "e.apps.googleusercontent.com"); monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "es")
    assert YouTubePublisher(cfg).client_config(None)["installed"]["client_secret"] == "es"
    assert YouTubePublisher(cfg).client_config({"client_id": ok_id, "client_secret": "own"})["installed"]["client_secret"] == "own"


def test_keys_accept_pasted_json_and_reject_web_type(env):
    c, cfg, db, app = env
    g = db.add_group("g"); db.set_account(g, "youtube", "yt"); acc = db.accounts(g)[0]["id"]
    web = json.dumps({"web": {"client_id": "1.apps.googleusercontent.com", "client_secret": "s"}})
    r = c.put(f"/api/accounts/{acc}/keys", json={"client_id": web, "client_secret": ""})
    assert r.status_code == 400 and "Desktop app" in r.json()["detail"]
    ok = json.dumps({"installed": {"client_id": "2.apps.googleusercontent.com", "client_secret": "sec"}})
    assert c.put(f"/api/accounts/{acc}/keys", json={"client_id": ok}).status_code == 200
    assert db.account_credentials(acc) == {"client_id": "2.apps.googleusercontent.com", "client_secret": "sec"}
    assert c.put(f"/api/accounts/{acc}/keys", json={"client_id": "{oops"}).status_code == 400
