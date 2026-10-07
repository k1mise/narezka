"""Скачивание: разбор ввода, локальный файл, прямая ссылка на файл."""
from __future__ import annotations

import functools
import http.server
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from narez.downloader import SourceError, download, friendly_error, normalize_source


def make_video(p: Path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=160x90:d=1:r=10",
                    "-pix_fmt", "yuv420p", str(p)], check=True)


def test_normalize_links():
    assert normalize_source("https://youtu.be/x") == "https://youtu.be/x"
    assert normalize_source(" youtube.com/watch?v=1 ") == "https://youtube.com/watch?v=1"
    assert normalize_source("www.tiktok.com/@a/video/1").startswith("https://www.")
    for bad in ["", "ftp://x/y", "привет мир", "file.txt.exe more"]:
        with pytest.raises(SourceError):
            normalize_source(bad)


def test_local_file(tmp_path):
    v = tmp_path / "my video.mp4"
    make_video(v)
    assert normalize_source(f'"{v}"') == str(v.resolve())
    path, title = download(str(v), tmp_path / "wd")
    assert path == v.resolve() and title == "my video"
    with pytest.raises(SourceError):
        normalize_source(str(tmp_path / "нет.mp4"))
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(SourceError):
        normalize_source(str(tmp_path / "a.txt"))


def test_direct_url(tmp_path):
    srv = tmp_path / "srv"; srv.mkdir()
    make_video(srv / "clip.mp4")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(srv))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        path, title = download(f"http://127.0.0.1:{httpd.server_address[1]}/clip.mp4", tmp_path / "wd")
    finally:
        httpd.shutdown()
    assert path.exists() and path.stat().st_size > 1000 and path.parent == tmp_path / "wd" / "downloads"


def test_friendly_errors():
    assert "прямую ссылку" in friendly_error(RuntimeError("ERROR: Unsupported URL: https://x"))
    assert "cookies" in friendly_error(RuntimeError("Sign in to confirm you're not a bot"))


def test_clean_url_and_safe_name():
    from narez.downloader import clean_url, safe_name
    u = "https://rick-i-morty.club/episodes/1sez/?utm_referrer=https%3A%2F%2Fx.club&utm_source=a&keep=1"
    assert clean_url(u) == "https://rick-i-morty.club/episodes/1sez/?keep=1"
    assert clean_url("https://youtu.be/abc?si=XYZ&t=5") == "https://youtu.be/abc?t=5"
    assert clean_url("https://x.y/a?v=1") == "https://x.y/a?v=1"
    assert normalize_source("https://x.y/v?utm_source=a") == "https://x.y/v"
    assert safe_name('Как дрессировать щенка: 7 дней? "Топ" / часть 2 ') == "Как дрессировать щенка 7 дней Топ часть 2"
    assert safe_name("???") == ""


def test_friendly_dns_and_ssl_errors():
    e1 = RuntimeError("Unable to download JSON metadata: HTTPSConnection(host='vk.com', port=443): Failed to resolve 'vk.com' ([Errno 11001] getaddrinfo failed)")
    assert "VPN" in friendly_error(e1) and "Прокси" in friendly_error(e1)
    e2 = RuntimeError("Unable to download webpage: EOF occurred in violation of protocol (_ssl.c:997)")
    assert "обрывает защищённое соединение" in friendly_error(e2)


def test_download_named_by_title_and_cached(tmp_path):
    srv = tmp_path / "srv"; srv.mkdir()
    make_video(srv / "Мой ролик про собак.mp4")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(srv))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/%D0%9C%D0%BE%D0%B9%20%D1%80%D0%BE%D0%BB%D0%B8%D0%BA%20%D0%BF%D1%80%D0%BE%20%D1%81%D0%BE%D0%B1%D0%B0%D0%BA.mp4"
    try:
        path, title = download(url, tmp_path / "wd")
        assert path.name == "Мой ролик про собак.mp4" and path.exists()
        assert (tmp_path / "wd" / "downloads" / "Мой ролик про собак.src").exists()
        path2, _ = download(url, tmp_path / "wd")           # повторно — тот же файл, без дублей
        assert path2 == path and len(list((tmp_path / "wd" / "downloads").glob("*.mp4"))) == 1
    finally:
        httpd.shutdown()
