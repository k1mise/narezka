"""Каналы: нормализация ссылок, сбор свежих видео, API ленты."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

from narez import watch as wt
from narez.config import load_config
from narez.db import DB
from narez.web.server import create_app


def test_channel_url_normalizes_youtube_and_rejects_videos():
    assert wt.channel_url("https://www.youtube.com/@Name") == "https://www.youtube.com/@Name/videos"
    assert wt.channel_url("youtube.com/@Name/featured") == "https://www.youtube.com/@Name/videos"
    assert wt.channel_url("@Name") == "https://www.youtube.com/@Name/videos"
    assert wt.channel_url("https://m.youtube.com/channel/UC123/") == "https://www.youtube.com/channel/UC123/videos"
    assert wt.channel_url("https://www.tiktok.com/@user") == "https://www.tiktok.com/@user"
    for bad in ["", "просто текст", "https://www.youtube.com/watch?v=abc", "https://youtu.be", "ftp://x.y/z", "https://www.youtube.com/"]:
        with pytest.raises(wt.WatchError):
            wt.channel_url(bad)


NOW = time.time()
DAY = 86400


class FakeYDL:
    """Подставка вместо yt_dlp: канал с пятью видео (одно чужое «закреплённое» старое, два свежих, два старых)."""
    calls: list = []

    def __init__(self, extra=None):
        self.extra = extra or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        FakeYDL.calls.append(url)
        if url.endswith("/videos"):
            return {"channel": "Канал - Videos", "entries": [{"id": i, "title": i, "url": f"https://www.youtube.com/watch?v={i}"} for i in ("new1", "new2", "old1", "old2")]}
        vid = url.split("=")[-1]
        ts = {"new1": NOW - 1 * DAY, "new2": NOW - 5 * DAY, "old1": NOW - 12 * DAY, "old2": NOW - 20 * DAY}[vid]
        if vid == "bad":
            raise RuntimeError("Private video")
        return {"id": vid, "title": "Видео " + vid, "channel": "Канал", "timestamp": ts, "view_count": 1000, "like_count": 50, "duration": 600,
                "webpage_url": url, "thumbnail": f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg", "extractor_key": "Youtube"}


def make_db(tmp_path):
    return DB(tmp_path / "t.db")


def test_refresh_channel_collects_only_recent_and_updates_stats(tmp_path):
    db = make_db(tmp_path)
    cid = db.add_watch_channel("https://www.youtube.com/@x/videos")
    assert db.add_watch_channel("https://www.youtube.com/@x/videos") is None           # дубль не добавляется
    FakeYDL.calls = []
    n = wt.refresh_channel(db, db.watch_channel(cid), 10, lambda *_: None, ydl_factory=FakeYDL)
    assert n == 2
    feed = db.watch_feed(NOW - 10 * DAY)
    assert [v["id"] for v in feed] == ["new1", "new2"]                                  # новые сверху, старые не попали
    assert feed[0]["views"] == 1000 and feed[0]["likes"] == 50 and feed[0]["channel_name"] == "Канал"
    assert feed[0]["thumb"].endswith("mqdefault.jpg")                                    # лёгкое превью, а не огромное
    ch = db.watch_channel(cid)
    assert ch["name"] == "Канал" and ch["error"] == "" and ch["checked_at"] > 0
    # второй проход: лайки выросли — обновляются; для видео старше окна запросов больше нет
    FakeYDL.calls = []
    wt.refresh_channel(db, db.watch_channel(cid), 10, lambda *_: None, ydl_factory=FakeYDL)
    assert not [u for u in FakeYDL.calls if "old2" in u]


def test_refresh_channel_records_error(tmp_path):
    db = make_db(tmp_path)
    cid = db.add_watch_channel("https://www.youtube.com/@gone/videos")

    class Boom(FakeYDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("ERROR: [youtube:tab] Does not exist")
    assert wt.refresh_channel(db, db.watch_channel(cid), 10, lambda *_: None, ydl_factory=Boom) == 0
    assert "Does not exist" in db.watch_channel(cid)["error"]


@pytest.fixture()
def env(tmp_path):
    cfg = load_config()
    cfg["paths"]["workdir"] = tmp_path / "wd"
    cfg["paths"]["workdir"].mkdir()
    cfg["paths"]["db"] = tmp_path / "t.db"
    cfg["_root"] = str(tmp_path)
    cfg["watch"] = {"auto_hours": 0, "days": 10}
    return TestClient(create_app(cfg)), cfg


def wait_task(c, t, timeout=20):
    for _ in range(int(timeout / 0.1)):
        r = c.get(f"/api/tasks/{t['id']}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.1)
    raise AssertionError("задача не завершилась")


def test_watch_api_add_refresh_feed_remove(env, monkeypatch):
    c, cfg = env
    real = wt.refresh_channel
    monkeypatch.setattr("narez.web.server.wt.refresh_channel", lambda db, ch, days, log, **kw: real(db, ch, days, log, ydl_factory=FakeYDL, **kw))
    r = c.post("/api/watch/channels", json={"urls": ["https://www.youtube.com/@a", "youtube.com/@b", "https://www.youtube.com/@a", "мусор", "https://www.youtube.com/watch?v=1"]}).json()
    assert r["added"] == 2 and len(r["rejected"]) == 3 and r["task"]
    res = wait_task(c, r["task"])
    assert res["status"] == "done" and res["result"]["channels"] == 2 and res["result"]["videos"] == 4
    w = c.get("/api/watch").json()
    assert w["days"] == 10 and len(w["channels"]) == 2 and len(w["videos"]) == 4 and w["updated"] > 0
    v = w["videos"][0]
    assert {"id", "title", "channel", "thumb", "views", "likes", "uploaded", "url"} <= set(v)
    # ручное обновление и удаление канала вместе с его видео
    assert wait_task(c, c.post("/api/watch/refresh").json())["status"] == "done"
    cid = w["channels"][0]["id"]
    w2 = c.delete(f"/api/watch/channels/{cid}").json()
    assert len(w2["channels"]) == 1 and len(w2["videos"]) == 2
    assert c.delete(f"/api/watch/channels/{cid}").status_code == 404


def test_watch_refresh_without_channels_is_400(env):
    c, _ = env
    assert c.post("/api/watch/refresh").status_code == 400
    assert c.post("/api/watch/channels", json={"urls": []}).status_code == 400
