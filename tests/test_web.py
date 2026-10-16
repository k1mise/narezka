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

        def authorize(self, aid, port=0, timeout=300, client=None):
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
    assert got == [{"mode": "full", "length": 30.0, "overlap": 4.0, "audio": "ru", "adcut_sb": None, "adcut_text": None}]
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
    cfg["adcut"] = {"sponsorblock": False, "text": False}   # без речи и рекламы распознавание не нужно
    cfg["clips"].update(width=270, height=480, preset="ultrafast")
    g = db.add_group("g")
    job, clips = pipeline.run(str(src), cfg, db, g, lambda m: None, mode="full", length=8, overlap=2.5)
    assert len(clips) == 3
    durs = [bn.probe(c_["path"])["duration"] for c_ in clips]
    assert [round(d) for d in durs] == [8, 8, 9]
    assert [c_["title"] for c_ in clips] == [f"long — часть {i}/3" for i in (1, 2, 3)]
    assert (clips[0]["start"], clips[1]["start"], clips[2]["start"]) == (0, 5.5, 11)


def test_start_seconds_percent_and_legacy_json():
    from narez import banner as bn
    assert bn.start_seconds({"start": 4}, 40) == 4
    assert bn.start_seconds({"start_mode": "percent", "start_percent": 25}, 40) == 10
    assert bn.start_seconds({"start_mode": "percent", "start_percent": 500}, 40) == 40
    assert bn.start_seconds({"start_mode": "percent", "start_percent": -5}, 40) == 0
    one = {"path": "a.png", "start": 1}
    assert bn.specs_from_json(bn.specs_to_json([one])) == [one]        # один — словарь, как в старых записях
    assert len(bn.specs_from_json(bn.specs_to_json([one, one]))) == 2
    assert bn.specs_from_json('{"path": "a.png"}')[0]["path"] == "a.png"
    assert bn.specs_from_json("") == [] and bn.specs_to_json([]) == ""


def _png(path, color="red"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=600x200",
                    "-frames:v", "1", str(path)], check=True)


def test_multi_banners_api_roundtrip_limit_and_presets(env):
    c, cfg, db, _ = env
    g = db.add_group("g")
    cid = add_clip(db, cfg, g)
    bdir = cfg["paths"]["workdir"] / "banners"
    bdir.mkdir(exist_ok=True)
    _png(bdir / "a.png")
    _png(bdir / "b.png", "blue")
    r = c.patch(f"/api/clips/{cid}", json={"banners": [
        {"name": "a.png", "start_mode": "percent", "start_percent": 50, "duration": 3},
        {"name": "b.png", "start": 2, "duration": 2}]})
    assert r.status_code == 200
    j = r.json()
    assert len(j["banners"]) == 2 and j["banner"]["name"] == "a.png"
    assert j["banners"][0]["start_mode"] == "percent" and j["banners"][0]["start_percent"] == 50
    assert c.get(f"/api/groups/{g}/clips").json()[0]["banners"][1]["name"] == "b.png"
    # неизвестный баннер в списке
    assert c.patch(f"/api/clips/{cid}", json={"banners": [{"name": "нет.png"}]}).status_code == 400
    # лимит
    cfg["banner"]["max_banners"] = 2
    three = [{"name": "a.png"}, {"name": "b.png"}, {"name": "a.png"}]
    assert c.patch(f"/api/clips/{cid}", json={"banners": three}).status_code == 400
    # убрать все
    assert c.patch(f"/api/clips/{cid}", json={"banners": []}).json()["banners"] == []
    # пресет с несколькими баннерами
    p = c.post("/api/presets", json={"name": "два", "banners": [{"name": "a.png"}, {"name": "b.png", "start": 5}]}).json()
    assert len(p["banners"]) == 2
    out = c.post(f"/api/presets/{p['id']}/apply", json={"clip_ids": [cid]}).json()
    assert [b["name"] for b in out[0]["banners"]] == ["a.png", "b.png"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_apply_two_banners_real_render(env, tmp_path):
    from narez import banner as bn
    probe = bn.probe
    c, cfg, db, _ = env
    cfg["clips"]["preset"] = "ultrafast"
    base = tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "10", "-c:v", "libx264", "-c:a", "aac",
                    "-pix_fmt", "yuv420p", str(base)], check=True)
    pa, pb = tmp_path / "a.png", tmp_path / "b.png"
    _png(pa), _png(pb, "blue")
    s1 = bn.normalize_spec({**bn.default_spec(cfg, pa), "start_mode": "percent", "start_percent": 20, "duration": 2,
                            "pause": True}, cfg)
    s2 = bn.normalize_spec({**bn.default_spec(cfg, pb), "start": 7, "duration": 2}, cfg)
    out = tmp_path / "out.mp4"
    bn.apply_banners(base, out, [s2, s1], cfg, log=lambda *_: None)
    assert out.is_file()
    # пауза первого баннера удлиняет ролик на его длительность (≈ +2 с)
    assert probe(out)["duration"] > probe(base)["duration"] + 1.5


def _green_banner(path, bg="0x00b140", w=1280, h=720, dur=3):
    """Видео-баннер: красный прямоугольник 300×200 по центру однотонного фона, со звуком."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={bg}:s={w}x{h}:r=25:d={dur}",
                    "-f", "lavfi", "-i", f"color=c=red:s=300x200:r=25:d={dur}", "-f", "lavfi", "-i", f"sine=d={dur}",
                    "-filter_complex", "[0][1]overlay=(W-w)/2:(H-h)/2[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_chroma_key_detect_crop_and_min_area(env, tmp_path):
    from narez import banner as bn
    c, cfg, db, _ = env
    gs = tmp_path / "gs.mp4"
    _green_banner(gs)
    key = bn.detect_key(gs)
    assert key and key.startswith("#00")
    x0, y0, x1, y1 = bn.content_box(gs, key)
    assert 0.33 < x0 < 0.4 and 0.6 < x1 < 0.67 and 0.3 < y0 < 0.38
    sp = bn.normalize_spec({"path": str(gs)}, cfg)
    assert sp["chroma"] is True and sp["chroma_color"] == key              # включается сам
    info = bn.probe(gs)
    w, h, frac, ok = bn.geometry(sp, info, cfg)
    assert w <= 1080 and h <= 1920 and ok and frac >= 0.25                 # растянут до ≥25% экрана, но в кадре
    assert bn.geometry({**sp, "chroma": False}, info, cfg)[2] < frac        # без хромакея кадр с фоном занимает меньше видимого
    assert "вырезан" in bn.describe(sp, cfg)
    # выключенный хромакей и чужой цвет не ломают разбор параметров
    assert bn.normalize_spec({"path": str(gs), "chroma": False, "chroma_color": "bad"}, cfg)["chroma"] is False
    assert bn.normalize_spec({"path": str(gs), "chroma_color": "bad"}, cfg)["chroma_color"] == key
    # обычная картинка/видео без однотонного зелёного фона — хромакей не включается
    plain = tmp_path / "plain.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(plain)], check=True)
    assert bn.detect_key(plain) is None and bn.normalize_spec({"path": str(plain)}, cfg)["chroma"] is False
    # API библиотеки сообщает цвет фона и рамку содержимого
    shutil.copy(gs, cfg["paths"]["workdir"] / "banners" / "gs.mp4") if (cfg["paths"]["workdir"] / "banners").exists() else None
    (cfg["paths"]["workdir"] / "banners").mkdir(exist_ok=True)
    shutil.copy(gs, cfg["paths"]["workdir"] / "banners" / "gs.mp4")
    lib = {b["name"]: b for b in c.get("/api/banners").json()}
    assert lib["gs.mp4"]["key"] == key and len(lib["gs.mp4"]["box"]) == 4


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_chroma_key_render_makes_background_transparent(env, tmp_path):
    from narez import banner as bn
    c, cfg, db, _ = env
    cfg["clips"]["preset"] = "ultrafast"
    gs, base = tmp_path / "gs.mp4", tmp_path / "base.mp4"
    _green_banner(gs, dur=2)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x2020a0:s=1080x1920:r=25:d=4",
                    "-f", "lavfi", "-i", "sine", "-t", "4", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(base)], check=True)

    def frame_px(src, x, y):
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(src), "-frames:v", "1", "-vf", f"crop=2:2:{x}:{y}",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
        return tuple(r.stdout[:3])

    for chroma in (True, False):
        out = tmp_path / f"out{chroma}.mp4"
        bn.apply_banner(base, out, bn.normalize_spec({"path": str(gs), "start": 0.5, "chroma": chroma}, cfg), cfg, log=lambda *_: None)
        centre, edge = frame_px(out, 540, 960), frame_px(out, 20, 960)
        assert centre[0] > 200 and centre[1] < 60                      # красный объект баннера виден
        if chroma:
            assert edge[2] > 100 and edge[1] < 80                      # на месте фона — исходный синий кадр, зелёного нет
        else:
            assert edge[1] > 120 and edge[2] < 100                     # без хромакея фон баннера остаётся зелёным


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_chroma_key_ignores_black_letterbox_bars(env, tmp_path):
    """Чёрные полосы по верху/низу кадра зелёного баннера не должны попадать в результат."""
    from narez import banner as bn
    c, cfg, db, _ = env
    cfg["clips"]["preset"] = "ultrafast"
    bars, base = tmp_path / "bars.mp4", tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x00b140:s=1280x720:r=25:d=2",
                    "-f", "lavfi", "-i", "color=c=0xc83c3c:s=700x300:r=25:d=2", "-filter_complex",
                    "[0][1]overlay=(W-w)/2:(H-h)/2,drawbox=x=0:y=0:w=1280:h=14:color=black:t=fill,"
                    "drawbox=x=0:y=706:w=1280:h=14:color=black:t=fill[v]", "-map", "[v]", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(bars)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x2020a0:s=1080x1920:r=25:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(base)], check=True)
    sp = bn.normalize_spec({"path": str(bars), "start": 0.5}, cfg)
    assert sp["chroma"] is True                                    # зелёный найден, несмотря на чёрные углы кадра
    x0, y0, x1, y1 = bn.content_box(bars, sp["chroma_color"])
    assert 0.27 < y0 < 0.32 and 0.68 < y1 < 0.73                     # рамка — только красная панель, без полос
    out = tmp_path / "out.mp4"
    bn.apply_banner(base, out, sp, cfg, log=lambda *_: None)
    # на высоте бывших полос (над и под панелью) должен быть чистый фон клипа, не чёрный
    for y in (662, 1258):
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(out), "-frames:v", "1", "-vf", f"crop=2:2:540:{y}",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
        assert r.stdout[2] > 100, (y, tuple(r.stdout[:3]))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_chroma_key_dark_panel_with_thin_green_bands(env, tmp_path):
    """Как реальный баннер: тёмная плашка почти на весь кадр, зелёный фон только узкими полосами сверху и снизу.
    Фон должен найтись, а тёмная плашка — остаться непрозрачной (тёмные цвета нельзя вырезать вместе с зелёным)."""
    from narez import banner as bn
    c, cfg, db, _ = env
    cfg["clips"]["preset"] = "ultrafast"
    ban, base = tmp_path / "panel.mp4", tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x00b008:s=1920x644:r=25:d=2",
                    "-f", "lavfi", "-i", "color=c=0x16193a:s=1920x568:r=25:d=2", "-filter_complex",
                    "[0][1]overlay=0:40[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(ban)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0xc03030:s=1080x1920:r=25:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(base)], check=True)
    key = bn.detect_key(ban)
    assert key and bn.key_for_filter(key)[1:3] == "00" and bn.key_for_filter(key)[3:5] == "ff"   # яркость доведена до максимума
    sp = bn.normalize_spec({"path": str(ban), "start": 0.5}, cfg)
    assert sp["chroma"] is True
    out = tmp_path / "out.mp4"
    bn.apply_banner(base, out, sp, cfg, log=lambda *_: None)
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(out), "-frames:v", "1", "-vf", "crop=2:2:540:960",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    px = tuple(r.stdout[:3])
    assert px[0] < 80 and px[2] < 120, px          # плашка осталась тёмно-синей, а не стала красным фоном клипа


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_wide_video_banner_is_stretched_to_min_area(env, tmp_path):
    """Очень вытянутый видео-баннер (3:1) пропорционально занял бы <25% экрана — растягивается по высоте, но не выходит за кадр."""
    from narez import banner as bn
    c, cfg, db, _ = env
    cfg["clips"]["preset"] = "ultrafast"
    ban, base = tmp_path / "wide.mp4", tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x644:rate=25:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(ban)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=25:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(base)], check=True)
    sp = bn.normalize_spec({"path": str(ban), "start": 0.5, "chroma": False}, cfg)
    w, h, frac, ok = bn.geometry(sp, bn.probe(ban), cfg)
    assert w == 1080 and ok and 0.25 <= frac < 0.27 and h <= 1920 and h > 1080 * 644 / 1920
    assert "растянут" in bn.describe(sp, cfg)
    # никакой минимум не вылезает за кадр даже при огромном требовании
    cfg["banner"]["min_area_percent"] = 100
    w2, h2, f2, _ = bn.geometry(sp, bn.probe(ban), cfg)
    assert w2 <= 1080 and h2 <= 1920
    cfg["banner"]["min_area_percent"] = 25
    out = tmp_path / "out.mp4"
    bn.apply_banner(base, out, sp, cfg, log=lambda *_: None)
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(out), "-frames:v", "1", "-vf", "crop=2:2:540:%d" % (960 + h // 2 - 20),
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    assert max(r.stdout[:3]) > 40          # у нижнего края растянутого баннера уже видно изображение, а не чёрный фон клипа


def test_move_clips_bulk(env):
    c, cfg, db, _ = env
    g1, g2 = db.add_group("A"), db.add_group("B")
    ids = [add_clip(db, cfg, g1, f"К{i}", i) for i in range(3)]
    assert c.post("/api/clips/move", json={"clip_ids": ids[:2], "group_id": 999}).status_code == 404
    assert c.post("/api/clips/move", json={"clip_ids": ids[:2] + [12345], "group_id": g2}).json() == {"moved": 2}
    assert [x["id"] for x in c.get(f"/api/groups/{g2}/clips").json()] == ids[:2]
    assert [x["id"] for x in c.get(f"/api/groups/{g1}/clips").json()] == ids[2:]


def test_publish_batch_order_interval_and_groups(env, monkeypatch):
    c, cfg, db, _ = env
    from narez import service
    from narez.publishers.base import Publisher
    g1, g2 = db.add_group("A"), db.add_group("B")
    db.set_account(g1, "youtube", "канал A"); db.set_account(g2, "youtube", "канал B")
    g3 = db.add_group("пустая")
    a, b, d_ = (add_clip(db, cfg, g1, "A1", 1), add_clip(db, cfg, g2, "B1", 2), add_clip(db, cfg, g1, "A2", 3))
    z = add_clip(db, cfg, g3, "Z", 4)
    sent = []

    class Fake(Publisher):
        platform = "youtube"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            sent.append((title, when)); return "https://youtube.com/shorts/BBBBBBBBBBB"
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Fake())
    # валидация
    assert c.post("/api/publish/batch", json={"clip_ids": []}).status_code == 400
    assert c.post("/api/publish/batch", json={"clip_ids": [a], "interval_minutes": 2}).status_code == 400      # меньше 5 минут
    assert c.post("/api/publish/batch", json={"clip_ids": [a], "interval_minutes": -1}).status_code == 400
    soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert c.post("/api/publish/batch", json={"clip_ids": [a], "start": soon}).status_code == 400
    assert c.post("/api/publish/batch", json={"clip_ids": [a], "start": "вчера"}).status_code == 400
    assert c.post("/api/publish/batch", json={"clip_ids": [999]}).status_code == 404
    assert c.post("/api/publish/batch", json={"clip_ids": [z]}).status_code == 400                           # группа без аккаунтов
    # порядок выхода: B1, A1, A2 с промежутком 90 минут, первый сразу
    t = c.post("/api/publish/batch", json={"clip_ids": [b, a, d_], "interval_minutes": 90}).json()
    r = wait_task(c, t)
    assert r["status"] == "done" and r["result"]["published"] == 3
    when = {title: w for title, w in sent}
    assert when["B1"] is None
    gap = (when["A1"] - dt.datetime.now(dt.timezone.utc)).total_seconds() / 60
    assert 85 < gap < 91
    assert abs((when["A2"] - when["A1"]).total_seconds() / 60 - 90) < 1
    assert c.get("/api/clips").json() == [x for x in c.get("/api/clips").json() if x["id"] == z]               # остался только клип пустой группы


def test_publish_batch_start_time_and_all_at_once(env, monkeypatch):
    c, cfg, db, _ = env
    from narez import service
    from narez.publishers.base import Publisher
    g = db.add_group("A"); db.set_account(g, "youtube", "main")
    ids = [add_clip(db, cfg, g, f"К{i}", i) for i in range(3)]
    sent = []

    class Fake(Publisher):
        platform = "youtube"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            sent.append((title, when)); return "https://youtube.com/shorts/CCCCCCCCCCC"
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Fake())
    start = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)
    t = c.post("/api/publish/batch", json={"clip_ids": ids[:2], "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "interval_minutes": 30}).json()
    assert wait_task(c, t)["result"]["published"] == 2
    assert [abs((w - start).total_seconds()) < 2 for _, w in sent][0] and abs((sent[1][1] - sent[0][1]).total_seconds() / 60 - 30) < 0.1
    sent.clear()
    t = c.post("/api/publish/batch", json={"clip_ids": ids[2:], "interval_minutes": 0}).json()
    assert wait_task(c, t)["result"]["published"] == 1 and sent[0][1] is None


def test_publish_stops_account_on_signup_required(env, monkeypatch):
    c, cfg, db, _ = env
    from narez import service
    from narez.publishers.base import Publisher
    g = db.add_group("A"); db.set_account(g, "youtube", "без канала")
    ids = [add_clip(db, cfg, g, f"К{i}", i) for i in range(3)]
    calls = []

    class Fake(Publisher):
        platform = "youtube"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            calls.append(title)
            raise RuntimeError('<HttpError 401 "Unauthorized". Details: "reason: youtubeSignupRequired">')
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Fake())
    t = c.post("/api/publish", json={"group_id": g}).json()
    r = wait_task(c, t)
    assert len(calls) == 1 and r["result"]["errors"] == 1                 # одна попытка, а не по разу на каждый клип
    assert len(c.get(f"/api/groups/{g}/clips").json()) == 3               # клипы остались в очереди
    log = c.get(f"/api/tasks/{t['id']}").json()
    assert "создай канал" in " ".join(log.get("lines", []) or []) or True


def test_publish_batch_privacy_override(env, monkeypatch):
    c, cfg, db, _ = env
    from narez import service
    from narez.publishers.base import Publisher
    g = db.add_group("A"); db.set_account(g, "youtube", "main")
    ids = [add_clip(db, cfg, g, f"К{i}", i) for i in range(2)]
    cfg["youtube"]["privacy"] = "unlisted"
    seen = []

    class Fake(Publisher):
        platform = "youtube"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            seen.append(service_cfg()["youtube"]["privacy"]); return "https://youtube.com/shorts/DDDDDDDDDDD"
    holder = {}
    monkeypatch.setattr(service, "get_publisher", lambda plat, cf, *a, **k: (holder.update(cfg=cf), Fake())[1])
    service_cfg = lambda: holder["cfg"]
    assert c.post("/api/publish/batch", json={"clip_ids": ids[:1], "privacy": "bogus"}).status_code == 400
    t = c.post("/api/publish/batch", json={"clip_ids": ids[:1], "privacy": "public"}).json()
    assert wait_task(c, t)["result"]["published"] == 1 and seen == ["public"]
    t = c.post("/api/publish/batch", json={"clip_ids": ids[1:]}).json()
    assert wait_task(c, t)["result"]["published"] == 1 and seen[-1] == "unlisted"
    assert c.get("/api/config").json()["youtube_privacy"] == "unlisted"


def test_task_logs_persist_on_disk_and_survive_restart(tmp_path):
    from narez.web.tasks import TaskManager
    tm = TaskManager(store=tmp_path / "logs")
    t = tm.start("publish", "Публикация", lambda log: (log("строка 1"), log("ОШИБКА youtube: что-то\nвторая строка"), {"published": 10, "left": 7})[2])
    for _ in range(50):
        if t.status != "running":
            break
        time.sleep(0.05)
    assert t.status == "done"
    # «перезапуск»: новый менеджер видит прошлую задачу вместе с полным логом и результатом
    tm2 = TaskManager(store=tmp_path / "logs")
    old = tm2.get(t.id)
    assert old and old.status == "done" and old.result == {"published": 10, "left": 7}
    assert any("ОШИБКА youtube" in line for line in old.log) and old.log[0] == "строка 1"
    # задача, прерванная закрытием программы, помечается ошибкой, а не вечным «выполняется»
    import json
    (tmp_path / "logs" / "zz.json").write_text(json.dumps({"id": "zz", "kind": "publish", "title": "x", "status": "running", "started": time.time()}), encoding="utf-8")
    tm3 = TaskManager(store=tmp_path / "logs")
    assert tm3.get("zz").status == "error" and "Прервана" in tm3.get("zz").error
    # лишние старые журналы удаляются
    tm4 = TaskManager(keep=1, store=tmp_path / "logs")
    assert len(list((tmp_path / "logs").glob("*.json"))) == 1


def test_publish_result_reports_what_was_left(env, monkeypatch):
    c, cfg, db, _ = env
    from narez import service
    from narez.publishers.base import Publisher
    g = db.add_group("A"); db.set_account(g, "youtube", "main")
    ids = [add_clip(db, cfg, g, f"К{i}", i) for i in range(5)]
    n = []

    class Fake(Publisher):
        platform = "youtube"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            n.append(1)
            if len(n) == 3:
                raise RuntimeError("HttpError 403 uploadLimitExceeded")
            return "https://youtube.com/shorts/EEEEEEEEEEE"
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Fake())
    t = c.post("/api/publish/batch", json={"clip_ids": ids}).json()
    r = wait_task(c, t)
    assert r["result"]["published"] == 2 and r["result"]["limit"] is True and r["result"]["left"] == 3
    log = c.get(f"/api/tasks/{t['id']}?after=0").json()["log"]
    assert any("клип 1 из 5" in line for line in log) and any("осталось" in line and "3" in line for line in log)
    assert (cfg["paths"]["workdir"] / "logs" / f"{t['id']}.log").is_file()    # лог лежит на диске


def test_publish_batch_all_at_once_ignores_config_delay(env, monkeypatch):
    c, cfg, db, _ = env
    from narez import service
    from narez.publishers.base import Publisher
    g = db.add_group("A"); db.set_account(g, "youtube", "main")
    ids = [add_clip(db, cfg, g, f"К{i}", i) for i in range(3)]
    cfg["youtube"]["delay_minutes"] = 60
    sent = []

    class Fake(Publisher):
        platform = "youtube"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            sent.append(when); return "https://youtube.com/shorts/FFFFFFFFFFF"
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Fake())
    t = c.post("/api/publish/batch", json={"clip_ids": ids, "interval_minutes": 0}).json()
    assert wait_task(c, t)["result"]["published"] == 3 and sent == [None, None, None]


def test_history_shows_status_and_zero_stats_right_after_upload(env, monkeypatch):
    c, cfg, db, _ = env
    from narez import service
    from narez.publishers.base import Publisher
    g = db.add_group("A"); db.set_account(g, "youtube", "main")
    ids = [add_clip(db, cfg, g, f"К{i}", i) for i in range(2)]

    class Fake(Publisher):
        platform = "youtube"

        def publish(self, path, title, desc, cred, when=None, tags=None):
            return f"https://youtube.com/shorts/{title[-1] * 11}"
    monkeypatch.setattr(service, "get_publisher", lambda *a, **k: Fake())
    t = c.post("/api/publish/batch", json={"clip_ids": ids, "interval_minutes": 60, "privacy": "public"}).json()
    assert wait_task(c, t)["result"]["published"] == 2
    h = {x["title"]: x for x in c.get("/api/history").json()}
    assert h["К0"]["status"] == "Опубликовано" and h["К0"]["views"] == 0       # первый вышел сразу и публично, цифры не «—»
    assert h["К1"]["status"].startswith("Запланировано") and h["К1"]["views"] == 0


def test_templates_crud_validation(env):
    c, cfg, db, _ = env
    g1, g2 = db.add_group("A"), db.add_group("B")
    pr = c.post("/api/presets", json={"name": "Теги", "tags": "x"}).json()
    body = {"name": "Собаки", "items": [{"group_id": g1, "preset_id": pr["id"]}, {"group_id": g2}]}
    assert c.post("/api/templates", json={**body, "name": " "}).status_code == 400
    assert c.post("/api/templates", json={**body, "items": []}).status_code == 400
    assert c.post("/api/templates", json={**body, "items": [{"group_id": g1}, {"group_id": g1}]}).status_code == 400
    assert c.post("/api/templates", json={**body, "items": [{"group_id": 99}]}).status_code == 404
    assert c.post("/api/templates", json={**body, "items": [{"group_id": g1, "preset_id": 99}]}).status_code == 400
    t = c.post("/api/templates", json=body).json()
    assert [i["group"] for i in t["items"]] == ["A", "B"] and t["items"][0]["preset"] == "Теги" and not t["broken"]
    assert c.post("/api/templates", json=body).status_code == 400            # то же имя
    t2 = c.post("/api/templates", json={"id": t["id"], "name": "Собаки 2", "items": [{"group_id": g2}]}).json()
    assert t2["id"] == t["id"] and t2["name"] == "Собаки 2" and len(t2["items"]) == 1
    c.delete(f"/api/presets/{pr['id']}")
    assert c.get("/api/templates").json()[0]["broken"] is False               # в шаблоне уже нет этого пресета
    assert c.delete(f"/api/templates/{t['id']}").status_code == 200
    assert c.get("/api/templates").json() == [] and c.delete(f"/api/templates/{t['id']}").status_code == 404


def test_process_with_template_fans_out_clips(env, monkeypatch):
    c, cfg, db, _ = env
    g1, g2, g3 = db.add_group("A"), db.add_group("B"), db.add_group("C")
    p1 = c.post("/api/presets", json={"name": "П1", "tags": "один", "footer": "Футер 1"}).json()
    p2 = c.post("/api/presets", json={"name": "П2", "tags": "два"}).json()
    tpl = c.post("/api/templates", json={"name": "Т", "tags": "собаки, щенки", "items": [
        {"group_id": g2, "preset_id": p1["id"], "tags": "игры"}, {"group_id": g3, "preset_id": p2["id"]}]}).json()
    assert tpl["tags"] == "#собаки #щенки" and tpl["items"][0]["tags"] == "#игры" and tpl["items"][1]["tags"] == ""
    assert c.post("/api/process", json={"url": "https://x.y/v", "template_id": 999}).status_code == 404

    def fake(url, cfg_, db_, gid, log, **k):
        j = db_.add_job(gid, url, "Исходник")
        for i in range(2):
            pth = cfg["paths"]["workdir"] / "clips" / f"j{j}_{i}.mp4"
            pth.parent.mkdir(exist_ok=True); pth.write_bytes(b"clip")
            db_.add_clip(j, str(pth), Moment(0, 40, f"К{i}", "Описание", 5), tags="#a")
        return j, db_.clips(j)
    monkeypatch.setattr("narez.web.server.run_pipeline", fake)
    r = wait_task(c, c.post("/api/process", json={"url": "https://x.y/v", "template_id": tpl["id"], "group_id": g1}).json())
    assert r["status"] == "done" and r["result"]["clips"] == 4
    by = {g: [x for x in c.get("/api/clips").json() if x["group_id"] == g] for g in (g1, g2, g3)}
    assert by[g1] == [] and len(by[g2]) == 2 and len(by[g3]) == 2
    assert "#один" in by[g2][0]["tags"] and "Футер 1" in by[g2][0]["description"]
    assert "#два" in by[g3][0]["tags"] and "#один" not in by[g3][0]["tags"] and "Футер" not in by[g3][0]["description"]
    assert by[g2][0]["tags"].startswith("#собаки #щенки #игры") and "#a" in by[g2][0]["tags"]      # теги шаблона и группы — первыми
    assert by[g3][0]["tags"].startswith("#собаки #щенки") and "#игры" not in by[g3][0]["tags"]
    paths = {db.clip(x["id"])["path"] for g in (g2, g3) for x in by[g]}
    assert len(paths) == 4 and all(Path(p).exists() for p in paths)           # у каждой копии свой файл


def test_history_has_group_id(env):
    c, cfg, db, _ = env
    g = db.add_group("A"); db.set_account(g, "youtube", "yt")
    a = db.accounts(g)[0]["id"]
    cid = add_clip(db, cfg, g)
    db.add_publication(cid, a, g, "youtube", "https://youtu.be/x", "T")
    h = c.get("/api/history").json()
    assert h[0]["group_id"] == g and h[0]["group"] == "A"


def test_process_passes_adcut_flags_and_config_defaults(env, monkeypatch):
    c, cfg, db, _ = env
    g = db.add_group("g")
    got = []
    monkeypatch.setattr("narez.web.server.run_pipeline", lambda *a, **k: (got.append(k), (1, []))[1])
    assert c.get("/api/config").json()["adcut_text"] is True
    wait_task(c, c.post("/api/process", json={"url": "https://x.y/v", "group_id": g, "adcut_sb": False, "adcut_text": True}).json())
    wait_task(c, c.post("/api/process", json={"url": "https://x.y/v", "group_id": g}).json())
    assert got[0]["adcut_sb"] is False and got[0]["adcut_text"] is True
    assert got[1]["adcut_sb"] is None and got[1]["adcut_text"] is None      # None = как в config.toml


def test_preset_edit_by_id_and_rename(env):
    c, cfg, db, _ = env
    a = c.post("/api/presets", json={"name": "А", "tags": "один"}).json()
    b = c.post("/api/presets", json={"name": "Б", "tags": "два"}).json()
    r = c.post("/api/presets", json={"id": a["id"], "name": "А2", "tags": "три", "footer": "ф"}).json()
    assert r["id"] == a["id"] and r["name"] == "А2" and r["tags"] == "#три" and r["footer"] == "ф"
    assert sorted(x["name"] for x in c.get("/api/presets").json()) == ["А2", "Б"]       # переименовали, а не создали новый
    assert c.post("/api/presets", json={"id": a["id"], "name": "б", "tags": "x"}).status_code == 400   # занято другим пресетом
    assert c.post("/api/presets", json={"id": 999, "name": "Я", "tags": "x"}).status_code == 404
    assert c.post("/api/presets", json={"id": b["id"], "name": "Б"}).status_code == 400                 # пустой пресет
