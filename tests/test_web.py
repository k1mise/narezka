"""Тесты веб-интерфейса: API, защита, фоновые задачи."""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

from narez.config import load_config
from narez.db import DB
from narez.models import Moment
from narez.web.server import create_app


@pytest.fixture()
def env(tmp_path):
    cfg = load_config()
    cfg["paths"]["workdir"] = tmp_path / "wd"
    cfg["paths"]["workdir"].mkdir()
    cfg["paths"]["db"] = tmp_path / "t.db"
    cfg["_root"] = str(tmp_path)
    app = create_app(cfg)
    return TestClient(app), cfg, DB(cfg["paths"]["db"]), app


def wait_task(c, t, timeout=20):
    for _ in range(int(timeout / 0.1)):
        r = c.get(f"/api/tasks/{t['id']}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.1)
    raise AssertionError("задача не завершилась")


def add_clip(db, cfg, g, title="Клип", n=0, with_file=True):
    j = db.add_job(g, "u", "Исходник")
    p = cfg["paths"]["workdir"] / "clips" / f"{n}.mp4"
    p.parent.mkdir(exist_ok=True)
    if with_file:
        p.write_bytes(b"clip")
    return db.add_clip(j, str(p), Moment(0, 40, title, "Описание", 5), tags="#a")


def test_guard_blocks_foreign_host_and_origin(env):
    c, *_ = env
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/groups", headers={"host": "evil.com"}).status_code == 403
    assert c.post("/api/groups", json={"name": "x"}, headers={"origin": "https://evil.com"}).status_code == 403
    assert c.post("/api/groups", json={"name": "x"}, headers={"origin": "http://localhost:8000"}).status_code == 200


def test_index_and_health(env):
    c, *_ = env
    assert c.get("/").status_code == 200 and "narez" in c.get("/").text
    h = c.get("/api/health").json()
    assert {"ffmpeg", "ollama", "yt_dlp", "process_running"} <= set(h)
    cfg = c.get("/api/config").json()
    assert cfg["min_ahead_minutes"] == 5 and "image" in cfg["banner_defaults"]


def test_groups_and_accounts(env):
    c, cfg, db, _ = env
    assert c.post("/api/groups", json={"name": "  "}).status_code == 400
    gid = c.post("/api/groups", json={"name": "Собаки"}).json()["id"]
    assert c.post(f"/api/groups/{gid}/accounts", json={"platform": "myspace"}).status_code == 400
    assert c.post(f"/api/groups/{gid}/accounts", json={"platform": "youtube", "label": "main"}).status_code == 200
    assert c.post(f"/api/groups/{gid}/accounts", json={"platform": "tiktok", "label": "tt"}).status_code == 200
    g = c.get("/api/groups").json()[0]
    assert g["name"] == "Собаки" and {a["platform"] for a in g["accounts"]} == {"youtube", "tiktok"}
    yt = next(a for a in g["accounts"] if a["platform"] == "youtube")
    assert yt["supported"] and not yt["authorized"]
    tt = next(a for a in g["accounts"] if a["platform"] == "tiktok")
    assert tt["supported"] and not tt["authorized"]
    assert c.delete(f"/api/accounts/{tt['id']}").status_code == 200
    assert len(c.get("/api/groups").json()[0]["accounts"]) == 1
    assert c.post(f"/api/groups/999/accounts", json={"platform": "youtube"}).status_code == 404


def test_clip_edit_roundtrip_and_validation(env):
    c, cfg, db, _ = env
    g = db.add_group("g")
    cid = add_clip(db, cfg, g)
    lst = c.get(f"/api/groups/{g}/clips").json()
    assert lst[0]["title"] == "Клип" and lst[0]["has_file"] and lst[0]["banner"] is None
    r = c.patch(f"/api/clips/{cid}", json={"title": "Новое", "description": "Опис", "tags": "gta5, лайфхаки"})
    assert r.status_code == 200 and r.json()["tags"] == "#gta5 #лайфхаки" and r.json()["title"] == "Новое"
    # время публикации: только в будущем и не ближе 5 минут
    soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert c.patch(f"/api/clips/{cid}", json={"publish_at": soon}).status_code == 400
    assert c.patch(f"/api/clips/{cid}", json={"publish_at": "завтра"}).status_code == 400
    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert c.patch(f"/api/clips/{cid}", json={"publish_at": later}).json()["publish_at"] == later
    assert c.patch(f"/api/clips/{cid}", json={"publish_at": ""}).json()["publish_at"] == ""
    # неизвестный баннер
    assert c.patch(f"/api/clips/{cid}", json={"banner": {"name": "нет.png"}}).status_code == 400
    assert c.get("/api/clips/999").status_code == 404
    assert c.get(f"/api/clips/{cid}/video").content == b"clip"
    assert c.delete(f"/api/clips/{cid}").status_code == 200
    assert c.get(f"/api/groups/{g}/clips").json() == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_banner_upload_describe_and_preview(env, tmp_path):
    c, cfg, db, _ = env
    cfg["clips"]["preset"] = "ultrafast"
    g = db.add_group("g")
    base = cfg["paths"]["workdir"] / "clips" / "b.mp4"
    base.parent.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "8", "-c:v", "libx264", "-c:a", "aac",
                    "-pix_fmt", "yuv420p", str(base)], check=True)
    j = db.add_job(g, "u", "src")
    cid = db.add_clip(j, str(base), Moment(0, 8, "T", "D", 5))
    png = tmp_path / "b.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=600x200",
                    "-frames:v", "1", str(png)], check=True)
    bad = c.put("/api/banners?name=evil.exe", content=b"x")
    assert bad.status_code == 400
    assert c.put("/api/banners?name=../../x.png", content="не картинка".encode()).status_code == 400   # мусор отвергается
    up = c.put("/api/banners?name=ad one.png", content=png.read_bytes())
    assert up.status_code == 200 and up.json()["kind"] == "image"
    assert [b["name"] for b in c.get("/api/banners").json()] == ["ad one.png"]
    d = c.get("/api/banners/describe", params={"name": "ad one.png", "width_percent": 50}).json()
    assert d["kind"] == "image" and "Картинка" in d["text"]
    r = c.patch(f"/api/clips/{cid}", json={"banner": {"name": "ad one.png", "start": 2, "duration": 3,
                                                       "width_percent": 60, "position": "bogus", "duck_percent": 500}})
    b = r.json()["banner"]
    assert r.status_code == 200 and b["position"] == "center" and b["duck_percent"] == 100 and b["start"] == 2
    t = c.post(f"/api/clips/{cid}/preview").json()
    assert wait_task(c, t, 60)["status"] == "done"
    assert len(c.get(f"/api/clips/{cid}/video?variant=final").content) > 1000
    assert c.get(f"/api/clips/{cid}/video?variant=final").content != c.get(f"/api/clips/{cid}/video").content
    assert c.delete("/api/banners?name=ad one.png").status_code == 200


def test_publish_flow_with_fake_publisher(env, monkeypatch):
    c, cfg, db, app = env
    from narez import service
    from narez.publishers.base import Publisher
    g = db.add_group("g"); db.set_account(g, "youtube", "main")
    ids = [add_clip(db, cfg, g, f"Клип {i}", i) for i in range(3)]
    sent = []

    class Fake(Publisher):
        platform = "youtube"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            sent.append((title, when))
            if len(sent) == 3:
                raise RuntimeError("HttpError 400 uploadLimitExceeded")
            return f"https://youtube.com/shorts/V{len(sent):011d}"
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Fake())
    assert c.post("/api/publish", json={"group_id": 999}).status_code == 404
    # только выбранный клип
    t = c.post("/api/publish", json={"group_id": g, "clip_ids": [ids[1]]}).json()
    assert wait_task(c, t)["result"]["published"] == 1 and [s[0] for s in sent] == ["Клип 1"]
    # остальные: второй упирается в лимит, клип остаётся в очереди
    t = c.post("/api/publish", json={"group_id": g}).json()
    r = wait_task(c, t)
    assert r["result"]["published"] == 1 and r["result"]["limit"] is True
    assert [x["title"] for x in c.get(f"/api/groups/{g}/clips").json()] == ["Клип 2"]
    h = c.get("/api/history").json()
    assert len(h) == 2 and all(x["url"].startswith("https://youtube.com/shorts/") for x in h)
    # mode=now очищает своё время клипа
    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c.patch(f"/api/clips/{ids[2]}", json={"publish_at": later})
    sent.clear(); sent.append(("x", None)); sent.append(("y", None))  # следующий вызов не упадёт
    sent.pop()
    monkeypatch.setattr(Fake, "publish", lambda self, *a, **k: "https://youtube.com/shorts/ZZZZZZZZZZZ")
    t = c.post("/api/publish", json={"group_id": g, "clip_ids": [ids[2]], "mode": "now"}).json()
    assert wait_task(c, t)["result"]["published"] == 1
    assert c.get(f"/api/groups/{g}/clips").json() == []


def test_history_refresh_and_import(env, monkeypatch):
    c, cfg, db, _ = env
    from narez import history, service
    from narez.publishers.youtube import ScopeError
    g = db.add_group("g"); db.set_account(g, "youtube", "main"); acc = db.accounts(g)[0]["id"]

    class Pub:
        def fetch_stats(self, a, ids):
            ok = lambda t: {"title": t, "published": "2026-10-05T10:00:00Z", "views": 7, "likes": 1, "comments": 0,
                            "privacy": "public", "publish_at": ""}
            return {i: ok(f"Видео {i}") for i in ids}
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Pub())
    monkeypatch.setattr("narez.web.server.get_publisher", lambda *a, **k: Pub())
    monkeypatch.setattr(history, "make_thumb", lambda *a, **k: None)
    monkeypatch.setattr("narez.web.server.import_videos",
                        lambda *a, **k: history.import_videos(*a, **{**k, "thumb_fn": lambda *x, **y: None}))
    assert c.post("/api/history/import", json={"urls": "мусор", "group_id": g}).status_code == 400
    t = c.post("/api/history/import", json={"urls": "https://youtu.be/AAAAAAAAAAA https://youtube.com/shorts/BBBBBBBBBBB",
                                            "group_id": g}).json()
    assert wait_task(c, t)["result"]["added"] == 2
    rows = c.get("/api/history").json()
    assert len(rows) == 2 and rows[0]["views"] == 7 and rows[0]["status"] == "Опубликовано"
    t = c.post("/api/history/refresh").json()
    assert wait_task(c, t)["result"]["updated"] == 2
    monkeypatch.setattr(Pub, "fetch_stats", lambda self, a, ids: (_ for _ in ()).throw(ScopeError("нужен повторный вход")))
    t = c.post("/api/history/refresh").json()
    r = wait_task(c, t)
    assert r["status"] == "error" and "повторный вход" in r["error"]


def test_process_validation_and_exclusive(env, monkeypatch):
    c, cfg, db, _ = env
    g = db.add_group("g")
    assert c.post("/api/process", json={"url": "не ссылка", "group_id": g}).status_code == 400
    assert c.post("/api/process", json={"url": "https://x.y/v", "group_id": 999}).status_code == 404
    import threading
    gate = threading.Event()
    monkeypatch.setattr("narez.web.server.run_pipeline", lambda *a, **k: (gate.wait(5), (1, []))[1])
    t = c.post("/api/process", json={"url": "https://x.y/v", "group_id": g}).json()
    assert c.post("/api/process", json={"url": "https://x.y/v", "group_id": g}).status_code == 409   # одна нарезка за раз
    assert c.get("/api/health").json()["process_running"] is True
    gate.set()
    assert wait_task(c, t)["status"] == "done"


def _mk_video(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=180x320:d=2:r=10",
                    "-pix_fmt", "yuv420p", str(p)], check=True)


def test_folders_files_slice_and_to_editor(env, monkeypatch):
    c, cfg, db, _ = env
    g = db.add_group("g")
    wd = cfg["paths"]["workdir"]
    fl = {f["key"]: f for f in c.get("/api/folders").json()}
    assert set(fl) == {"sources", "clips", "test"}
    _mk_video(wd / "downloads" / "src.mp4")
    _mk_video(wd / "clips" / "job1" / "a.mp4")
    (wd / "downloads" / "notes.txt").write_text("x")
    names = [f["name"] for f in c.get("/api/files", params={"folder_key": "sources"}).json()]
    assert names == ["src.mp4"]
    assert c.get("/api/files", params={"folder_key": "bad"}).status_code == 400
    # защита от выхода из папки
    r = c.post("/api/files/to-editor", json={"folder": "sources", "name": "../clips/job1/a.mp4", "group_id": g})
    assert r.status_code == 404
    # клип из папки -> в редактор
    r = c.post("/api/files/to-editor", json={"folder": "clips", "name": "job1/a.mp4", "group_id": g})
    assert r.status_code == 200, r.text
    cl = r.json()
    assert cl["title"] == "a" and cl["has_thumb"] and not cl["test"] and 1 < cl["duration"] < 3
    assert c.get("/api/files", params={"folder_key": "clips"}).json()[0]["in_editor"] is True
    # нарезка из файла исходников (тестовый режим)
    seen = {}
    monkeypatch.setattr("narez.web.server.run_pipeline", lambda src, cfg_, db_, gid, log, test=False, **kw: seen.update(src=src, test=test, **kw) or (1, []))
    t = c.post("/api/files/slice", json={"folder": "sources", "name": "src.mp4", "group_id": g, "test": True}).json()
    assert wait_task(c, t)["status"] == "done"
    assert seen["test"] is True and seen["src"].endswith("src.mp4")


def test_test_jobs_not_published_and_kept(env, monkeypatch):
    c, cfg, db, _ = env
    from narez import service
    g = db.add_group("g")
    db.set_account(g, "youtube", "yt")
    wd = cfg["paths"]["workdir"]
    _mk_video(wd / "test" / "job1" / "t.mp4")
    r = c.post("/api/files/to-editor", json={"folder": "test", "name": "job1/t.mp4", "group_id": g}).json()
    assert r["test"] is True
    assert c.post("/api/publish", json={"group_id": g, "clip_ids": [r["id"]]}).status_code == 400
    assert c.post("/api/publish", json={"group_id": g}).status_code == 400   # одни тестовые клипы — публиковать нечего
    assert (wd / "test" / "job1" / "t.mp4").exists()


def test_process_accepts_loose_links_and_files(env, monkeypatch, tmp_path):
    c, cfg, db, _ = env
    g = db.add_group("g")
    got = []
    monkeypatch.setattr("narez.web.server.run_pipeline", lambda src, *a, **k: got.append(src) or (1, []))
    for u in ["youtube.com/watch?v=1", "https://a.b/c.mp4"]:
        t = c.post("/api/process", json={"url": u, "group_id": g}).json()
        wait_task(c, t)
    v = tmp_path / "v.mp4"; _mk_video(v)
    wait_task(c, c.post("/api/process", json={"url": str(v), "group_id": g}).json())
    assert got == ["https://youtube.com/watch?v=1", "https://a.b/c.mp4", str(v.resolve())]


def test_presets_save_apply_delete(env):
    c, cfg, db, _ = env
    g = db.add_group("g")
    c1, c2 = add_clip(db, cfg, g, "A", 1), add_clip(db, cfg, g, "B", 2)
    _mk_banner = cfg["paths"]["workdir"] / "banners" / "ad.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=400x200:d=1", "-frames:v", "1",
                    str(_mk_banner)], check=True)
    assert c.post("/api/presets", json={"name": " "}).status_code == 400
    assert c.post("/api/presets", json={"name": "x"}).status_code == 400          # пустой пресет
    assert c.post("/api/presets", json={"name": "x", "banner": {"name": "нет.png"}}).status_code == 400
    p = c.post("/api/presets", json={"name": "Реклама", "banner": {"name": "ad.png", "start": 5}, "tags": "реклама, #ad",
                                     "footer": "Спонсор: example.com"}).json()
    assert p["banner"]["name"] == "ad.png" and p["banner"]["start"] == 5 and p["tags"] == "#реклама #ad"
    assert [x["name"] for x in c.get("/api/presets").json()] == ["Реклама"]
    r = c.post(f"/api/presets/{p['id']}/apply", json={"clip_ids": [c1, c2]}).json()
    assert len(r) == 2 and r[0]["banner"]["name"] == "ad.png" and "#a" in r[0]["tags"] and "#реклама" in r[0]["tags"]
    assert r[0]["description"].endswith("Спонсор: example.com")
    r2 = c.post(f"/api/presets/{p['id']}/apply", json={"clip_ids": [c1]}).json()   # повторно — без дублей
    assert r2[0]["description"].count("Спонсор") == 1 and r2[0]["tags"].count("#реклама") == 1
    # перезапись по имени
    p2 = c.post("/api/presets", json={"name": "Реклама", "tags": "один"}).json()
    assert p2["id"] == p["id"] and p2["banner"] is None
    assert c.delete(f"/api/presets/{p['id']}").status_code == 200
    assert c.post(f"/api/presets/{p['id']}/apply", json={"clip_ids": [c1]}).status_code == 404


def test_login_cancel_and_account_removal(env, monkeypatch):
    """Вход в YouTube ждёт ответа Google на локальном порту; удаление аккаунта (или кнопка отмены) прерывает ожидание."""
    import http.server
    c, cfg, db, _ = env
    g = db.add_group("g")
    db.set_account(g, "youtube", "yt")
    acc = db.accounts(g)[0]["id"]
    started = {}

    class Pub:
        def token_path(self, aid):
            return cfg["paths"]["workdir"] / f"tok{aid}.json"

        def authorize(self, aid, port=0, timeout=300):
            srv = http.server.HTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
            srv.timeout = 15
            started["ok"] = True
            srv.handle_request()          # как run_local_server: ждём один запрос
            srv.server_close()
            raise RuntimeError("access_denied")

    monkeypatch.setattr("narez.web.server.get_publisher", lambda *a, **k: Pub())
    t = c.post(f"/api/accounts/{acc}/login").json()
    for _ in range(50):
        if started.get("ok"):
            break
        time.sleep(0.1)
    time.sleep(0.3)
    assert c.delete(f"/api/accounts/{acc}").status_code == 200       # убрали аккаунт — ожидание входа прервано
    r = wait_task(c, t, timeout=10)
    assert r["status"] == "error" and "отменён" in r["error"]
    # кнопка «Отменить вход»
    db.set_account(g, "youtube", "yt2")
    acc2 = db.accounts(g)[0]["id"]
    started.clear()
    t2 = c.post(f"/api/accounts/{acc2}/login").json()
    for _ in range(50):
        if started.get("ok"):
            break
        time.sleep(0.1)
    time.sleep(0.3)
    assert c.post("/api/login/cancel").json()["cancelled"] is True
    assert wait_task(c, t2, timeout=10)["status"] == "error"
    assert c.post("/api/login/cancel").json()["cancelled"] is False


def test_delete_file_from_folder(env):
    c, cfg, db, _ = env
    g = db.add_group("g")
    wd = cfg["paths"]["workdir"]
    _mk_video(wd / "downloads" / "Видео [x1].mp4")
    (wd / "downloads" / "Видео [x1].transcript.small.json").write_text("{}")
    (wd / "downloads" / "Видео [x1].src").write_text("x1")
    _mk_video(wd / "clips" / "job1" / "a.mp4")
    keep = wd / "downloads" / "Другое.mp4"; _mk_video(keep)
    j = db.add_job(g, "u", "t", str(wd / "downloads" / "Видео [x1].mp4"))
    cid = c.post("/api/files/to-editor", json={"folder": "clips", "name": "job1/a.mp4", "group_id": g}).json()["id"]
    assert c.delete("/api/files", params={"folder_key": "sources", "name": "../clips/job1/a.mp4"}).status_code == 404
    assert c.delete("/api/files", params={"folder_key": "sources", "name": "Видео [x1].mp4"}).status_code == 200
    assert not list((wd / "downloads").glob("Видео*")) and keep.exists()          # и кэш, и отметка убраны
    assert db.job(j)["source_path"] == ""
    assert c.delete("/api/files", params={"folder_key": "clips", "name": "job1/a.mp4"}).status_code == 200
    assert db.clip(cid)["status"] == "discarded" and not (wd / "clips" / "job1" / "a.mp4").exists()
    assert c.get("/api/groups/%d/clips" % g).json() == []


def test_publish_to_selected_accounts_across_groups(env, monkeypatch):
    c, cfg, db, app = env
    from narez import service
    from narez.publishers.base import Publisher
    g1, g2 = db.add_group("A"), db.add_group("B")
    db.set_account(g1, "youtube", "канал A"); db.set_account(g1, "tiktok", "tt A")
    db.set_account(g2, "youtube", "канал B")
    a1 = next(a["id"] for a in db.accounts(g1) if a["platform"] == "youtube")
    tt = next(a["id"] for a in db.accounts(g1) if a["platform"] == "tiktok")
    b1 = db.accounts(g2)[0]["id"]
    cid = add_clip(db, cfg, g1, "Клип", 1)
    sent = []

    class Fake(Publisher):
        platform = "youtube"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            sent.append(cred["account_id"])
            return f"https://youtube.com/shorts/{cred['account_id']:011d}"
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Fake())
    assert c.post("/api/publish", json={"group_id": g1, "clip_ids": [cid], "account_ids": [999]}).status_code == 404
    # клип группы A уходит на канал группы B — и только на него
    t = c.post("/api/publish", json={"group_id": g1, "clip_ids": [cid], "account_ids": [b1]}).json()
    assert wait_task(c, t)["result"]["published"] == 1 and sent == [b1]
    h = c.get("/api/history").json()
    assert len(h) == 1 and h[0]["account"] == "канал B" and h[0]["platform"] == "youtube"
    assert c.get(f"/api/groups/{g1}/clips").json() == []         # выложен на все выбранные — из очереди ушёл


def test_clip_group_can_be_changed_in_editor(env, monkeypatch):
    c, cfg, db, _ = env
    from narez import service
    from narez.publishers.base import Publisher
    g1, g2 = db.add_group("A"), db.add_group("B")
    db.set_account(g1, "youtube", "канал A"); db.set_account(g2, "youtube", "канал B")
    b_acc = db.accounts(g2)[0]["id"]
    cid = add_clip(db, cfg, g1, "Клип", 1)
    allc = c.get("/api/clips").json()
    assert len(allc) == 1 and allc[0]["group_id"] == g1 and allc[0]["group"] == "A"
    r = c.patch(f"/api/clips/{cid}", json={"group_id": g2}).json()
    assert r["group_id"] == g2 and r["group"] == "B"
    assert c.get(f"/api/groups/{g1}/clips").json() == [] and len(c.get(f"/api/groups/{g2}/clips").json()) == 1
    assert c.patch(f"/api/clips/{cid}", json={"group_id": 999}).status_code == 404
    sent = []

    class Fake(Publisher):
        platform = "youtube"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            sent.append(cred["account_id"]); return "https://youtube.com/shorts/AAAAAAAAAAA"
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Fake())
    t = c.post("/api/publish", json={"group_id": g2, "clip_ids": [cid]}).json()   # «все аккаунты группы» — новой группы B
    assert wait_task(c, t)["result"]["published"] == 1 and sent == [b_acc]
    h = c.get("/api/history").json()
    assert h[0]["group"] == "B" and h[0]["account"] == "канал B"


def test_rename_file_updates_everything(env):
    c, cfg, db, _ = env
    g = db.add_group("g")
    wd = cfg["paths"]["workdir"]
    src = wd / "downloads" / "Старое [x1].mp4"; _mk_video(src)
    (wd / "downloads" / "Старое [x1].src").write_text("x1")
    (wd / "downloads" / "Старое [x1].transcript.small.json").write_text("{}")
    j = db.add_job(g, "u", "Старое", str(src))
    _mk_video(wd / "clips" / "job1" / "01 клип.mp4")
    cid = c.post("/api/files/to-editor", json={"folder": "clips", "name": "job1/01 клип.mp4", "group_id": g}).json()["id"]
    other = wd / "downloads" / "Занято.mp4"; _mk_video(other)
    r = c.post("/api/files/rename", json={"folder": "sources", "name": "Старое [x1].mp4", "new_name": 'Новое: имя? "1".mp4'})
    assert r.status_code == 200 and r.json()["name"] == "Новое имя 1.mp4"
    names = sorted(p.name for p in (wd / "downloads").iterdir())
    assert "Новое имя 1.mp4" in names and "Новое имя 1.src" in names and "Новое имя 1.transcript.small.json" in names
    assert not any(n.startswith("Старое") for n in names)
    assert db.job(j)["source_path"].endswith("Новое имя 1.mp4")
    assert c.post("/api/files/rename", json={"folder": "sources", "name": "Новое имя 1.mp4", "new_name": "Занято"}).status_code == 409
    assert c.post("/api/files/rename", json={"folder": "sources", "name": "Новое имя 1.mp4", "new_name": "  "}).status_code == 400
    assert c.post("/api/files/rename", json={"folder": "sources", "name": "../x.mp4", "new_name": "a"}).status_code == 404
    # клип из очереди: путь в базе обновляется, клип остаётся рабочим
    assert c.post("/api/files/rename", json={"folder": "clips", "name": "job1/01 клип.mp4", "new_name": "Мой клип"}).status_code == 200
    assert db.clip(cid)["path"].endswith("Мой клип.mp4") and c.get(f"/api/clips/{cid}").json()["has_file"]


def test_clip_number_and_source_for_labels(env):
    c, cfg, db, _ = env
    g = db.add_group("g")
    j = db.add_job(g, "u", "Исходник")
    for i in range(3):
        db.add_clip(j, str(cfg["paths"]["workdir"] / f"{i}.mp4"), Moment(0, 40, f"Клип {i}", "", 5))
    got = c.get("/api/clips").json()
    assert [x["number"] for x in got] == [1, 2, 3] and got[0]["source"] == "Исходник"


def test_load_env(tmp_path, monkeypatch):
    from narez.env import load_env
    monkeypatch.delenv("NZ_A", raising=False)
    monkeypatch.delenv("NZ_B", raising=False)
    p = tmp_path / ".env"
    p.write_text('# c\nNZ_A=1\nNZ_B="x y"\n', encoding="utf-8")
    assert load_env(p) is True
    import os
    assert os.environ["NZ_A"] == "1" and os.environ["NZ_B"] == "x y"
    assert load_env(tmp_path / "nope") is False


def test_plan_parts_overlap_and_tail():
    from narez.pipeline import plan_parts
    p = plan_parts(100, 45, 2.5)
    assert p == [(0, 45), (42.5, 87.5), (85, 100)]
    assert all(b[0] == a[1] - 2.5 for a, b in zip(p, p[1:]))   # каждая часть начинается за 2.5 с до конца предыдущей
    assert plan_parts(30, 45, 2.5) == [(0, 30)]                  # короткое видео — одна часть
    assert plan_parts(88, 45, 2.5) == [(0, 45), (42.5, 88)]      # хвост меньше 3 с нового материала присоединяется
    assert plan_parts(60, 20, 0) == [(0, 20), (20, 40), (40, 60)]
    with pytest.raises(ValueError):
        plan_parts(60, 10, 10)


def test_cut_params_validation(env, monkeypatch):
    c, cfg, db, app = env
    g = db.add_group("g")
    got = []
    monkeypatch.setattr("narez.web.server.run_pipeline", lambda src, cfg_, db_, gid, log, test=False, **kw: got.append(kw) or (1, []))
    base = {"url": "https://a.b/c.mp4", "group_id": g}
    assert c.post("/api/process", json={**base, "mode": "x"}).status_code == 400
    assert c.post("/api/process", json={**base, "mode": "full", "length": 2}).status_code == 400
    assert c.post("/api/process", json={**base, "mode": "full", "length": 30, "overlap": 30}).status_code == 400
    r = c.post("/api/process", json={**base, "mode": "full", "length": 30, "overlap": 4})
    assert r.status_code == 200
    wait_task(c, r.json())
    assert got == [{"mode": "full", "length": 30.0, "overlap": 4.0, "audio": "ru"}]
    assert c.post("/api/process", json={**base, "audio": "de"}).status_code == 400
    wait_task(c, c.post("/api/process", json={**base, "audio": "original"}).json())
    assert got[-1]["audio"] == "original"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")
def test_full_mode_pipeline_renders_all_parts(env, tmp_path):
    c, cfg, db, app = env
    from narez import banner as bn, pipeline
    src = tmp_path / "long.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=20",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=20", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(src)], check=True)
    cfg["subtitles"]["enabled"] = False
    cfg["clips"].update(width=270, height=480, preset="ultrafast")
    g = db.add_group("g")
    job, clips = pipeline.run(str(src), cfg, db, g, lambda m: None, mode="full", length=8, overlap=2.5)
    assert len(clips) == 3
    durs = [bn.probe(c_["path"])["duration"] for c_ in clips]
    assert [round(d) for d in durs] == [8, 8, 9]
    assert [c_["title"] for c_ in clips] == [f"long — часть {i}/3" for i in (1, 2, 3)]
    assert (clips[0]["start"], clips[1]["start"], clips[2]["start"]) == (0, 5.5, 11)
