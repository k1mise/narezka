"""Прокси: разбор форматов, проверка, выбор для скачивания."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

from narez import proxies as px
from narez.config import load_config
from narez.db import DB
from narez.web.server import create_app


def test_parse_formats():
    text = """1.2.3.4:8080
http://u:p@5.6.7.8:3128
socks5://9.9.9.9:1080
user:pa ss@10.0.0.1:80
11.11.11.11:8000:bob:secret
bob:secret:12.12.12.12:9000
13.13.13.13 8888
14.14.14.14;7777;u2;p2
proxy.example.com:3128
мусор
999.1.1.1:80
1.2.3.4:8080
# комментарий
https://u%40x:p%3Aq@host.io:443/"""
    good, bad = px.parse_proxies(text)
    urls = [g["url"] for g in good]
    assert urls == ["http://1.2.3.4:8080", "http://u:p@5.6.7.8:3128", "socks5://9.9.9.9:1080",
                    "http://user:pa%20ss@10.0.0.1:80", "http://bob:secret@11.11.11.11:8000",
                    "http://bob:secret@12.12.12.12:9000", "http://13.13.13.13:8888", "http://u2:p2@14.14.14.14:7777",
                    "http://proxy.example.com:3128", "https://u%40x:p%3Aq@host.io:443"]
    assert bad == ["мусор", "999.1.1.1:80"]
    assert px.parse_proxies("1.2.3.4:80", "socks5")[0][0]["url"] == "socks5://1.2.3.4:80"
    assert px.mask("http://bob:secret@1.1.1.1:80") == "http://bob:***@1.1.1.1:80"


def test_check_proxy_ok_and_errors():
    class R:
        def __init__(self, d): self.d = d
        def json(self): return self.d

    seen = {}
    def ok_get(url, proxies=None, timeout=0):
        seen["p"] = proxies["https"]
        return R({"success": True, "ip": "8.8.8.8", "country": "Germany", "country_code": "DE", "city": "Berlin",
                  "connection": {"isp": "Hetzner"}})
    r = px.check_proxy("socks5://1.1.1.1:1080", get=ok_get)
    assert r["ok"] and r["ip"] == "8.8.8.8" and r["city"] == "Berlin" and r["isp"] == "Hetzner"
    assert seen["p"] == "socks5h://1.1.1.1:1080"                       # DNS через прокси

    def bad_get(url, proxies=None, timeout=0):
        raise OSError("Tunnel connection failed: 407 Proxy Authentication Required")
    r = px.check_proxy("http://1.1.1.1:80", get=bad_get)
    assert not r["ok"] and "логин" in r["error"]

    def fallback_get(url, proxies=None, timeout=0):
        if "ipwho" in url:
            raise OSError("boom")
        return R({"ip": "7.7.7.7"})
    r = px.check_proxy("http://1.1.1.1:80", get=fallback_get)
    assert r["ok"] and r["ip"] == "7.7.7.7" and r["country"] == ""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["paths"]["workdir"] = tmp_path / "wd"; cfg["paths"]["workdir"].mkdir()
    cfg["paths"]["db"] = tmp_path / "t.db"
    app = create_app(cfg)
    def fake_check(url, timeout=12.0, get=None):
        if "bad" in url:
            return {"ok": False, "ip": "", "country": "", "code": "", "city": "", "isp": "", "ms": 0, "error": "Прокси не ответил"}
        return {"ok": True, "ip": "5.5.5.5", "country": "Poland", "code": "PL", "city": "Warsaw", "isp": "X", "ms": 120, "error": ""}
    monkeypatch.setattr("narez.web.server.px.check_proxy", fake_check)
    return TestClient(app), cfg


def wait(c, t):
    for _ in range(100):
        r = c.get(f"/api/tasks/{t['id']}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.1)
    raise AssertionError


def test_proxy_api_flow(env, monkeypatch):
    c, cfg = env
    r = c.post("/api/proxies", json={"text": "1.1.1.1:80:bob:pw\nbad.host.example:99999\nhttp://2.2.2.2:8080\n1.1.1.1:80:bob:pw"})
    assert r.status_code == 200
    j = r.json()
    assert j["added"] == 2 and j["bad"] and j["task"]
    assert wait(c, j["task"])["status"] == "done"
    lst = c.get("/api/proxies").json()
    assert len(lst) == 2 and all(p["status"] == "ok" and p["country"] == "Poland" for p in lst)
    assert "pw" not in str(lst) and "***" in lst[0]["url"]                # пароль наружу не отдаётся
    assert c.post("/api/proxies", json={"text": "ерунда"}).status_code == 400
    # плохой прокси: выбрать нельзя, удаляется кнопкой «убрать нерабочие»
    c.post("/api/proxies", json={"text": "3.3.3.3:80"})
    db = DB(cfg["paths"]["db"])
    badid = db.add_proxy("http://bad.example.com:1")
    wait(c, c.post("/api/proxies/check").json())
    assert c.post(f"/api/proxies/{badid}/use", json={"on": True}).status_code == 400
    assert c.delete("/api/proxies").json()["deleted"] == 1
    # выбираем рабочий: скачивание идёт через него
    pid = c.get("/api/proxies").json()[0]["id"]
    assert c.post(f"/api/proxies/{pid}/use", json={"on": True}).status_code == 200
    g = db.add_group("g")
    got = {}
    monkeypatch.setattr("narez.web.server.run_pipeline", lambda src, cfg_, *a, **k: got.update(proxy=cfg_.get("download", {}).get("proxy")) or (1, []))
    wait(c, c.post("/api/process", json={"url": "https://a.b/c", "group_id": g}).json())
    assert got["proxy"] and got["proxy"].startswith("http://") and "1.1.1.1" in got["proxy"] + db.active_proxy()["url"]
    c.post(f"/api/proxies/{pid}/use", json={"on": False})
    wait(c, c.post("/api/process", json={"url": "https://a.b/c", "group_id": g}).json())
    assert not got["proxy"]
    assert c.delete(f"/api/proxies/{pid}").status_code == 200 and c.delete(f"/api/proxies/{pid}").status_code == 404
