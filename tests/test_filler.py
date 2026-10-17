"""Видео-вставка: раскладка кадра, название в полоске, случайный выбор, сборка ffmpeg, интеграция с ensure_final и API."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from narez import banner as bn
from narez import filler
from narez.config import load_config
from narez.db import DB
from narez.models import Moment

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")


def mk_cfg(tmp_path):
    cfg = load_config()
    cfg["paths"]["workdir"] = tmp_path
    cfg["_root"] = tmp_path
    return cfg


def test_layout_bottom_and_top():
    cfg = load_config()
    b = filler.layout(cfg, "bottom")
    t = filler.layout(cfg, "top")
    h = cfg["clips"]["height"]
    assert b["main_y"] == 0 and b["strip_y"] == b["main_h"] and b["vid_y"] == b["strip_y"] + b["strip_h"]
    assert b["vid_y"] + b["vid_h"] == h                                   # вставка прижата к низу
    assert t["strip_y"] == 0 and t["vid_y"] == t["strip_h"] and t["main_y"] + t["main_h"] == h   # клип прижат к низу
    for lay in (b, t):
        rest = h - lay["main_h"]
        assert lay["strip_h"] + lay["vid_h"] == rest
        assert 0.84 <= lay["vid_h"] / rest <= 0.86                        # вставка ≈ 85% оставшегося места
        assert 0 <= lay["band_y"] and lay["band_y"] + lay["main_h"] <= h


def test_wrap_title_fits_and_truncates():
    size, lines = filler.wrap_title("Короткий заголовок", 1000, 130, 66, 30)
    assert lines == ["Короткий заголовок"] and size >= 50
    long = "Очень длинное название клипа, которое никак не помещается в узкую полоску " * 3
    size, lines = filler.wrap_title(long, 1000, 130, 66, 30)
    assert size == 30 and lines[-1].endswith("…") and len(lines) * size * 1.2 <= 130 + 1


def test_normalize():
    assert filler.normalize("") is None and filler.normalize({"position": "left"}) is None
    assert filler.normalize('{"position":"top","file":"../x.mp4"}') == {"position": "top", "file": "x.mp4"}
    assert filler.normalize({"position": "bottom", "picked": "a.mp4", "offset": 3})["offset"] == 3.0


def test_title_ass_escapes_braces():
    cfg = load_config()
    ass = filler.title_ass("Тест {жирный}", filler.layout(cfg, "bottom"), cfg)
    assert "{жирный}" not in ass and "(жирный)" in ass and "\\an5" in ass


def _video(path, w, h, dur, color="red"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color={color}:s={w}x{h}:r=25:d={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)


@needs_ffmpeg
def test_compose_and_integration(tmp_path):
    cfg = mk_cfg(tmp_path)
    lib = filler.library_dir(cfg)
    lib.mkdir(parents=True)
    _video(lib / "blue.mp4", 640, 360, 3, "blue")
    _video(lib / "green.mp4", 360, 640, 4, "green")
    (lib / "notes.txt").write_text("не видео")
    assert [p.name for p in filler.library(cfg)] == ["blue.mp4", "green.mp4"]

    db = DB(tmp_path / "t.db")
    g = db.add_group("g")
    j = db.add_job(g, "u", "t")
    base = tmp_path / "clip.mp4"
    _video(base, 1080, 1920, 3, "white")
    cid = db.add_clip(j, str(base), Moment(0, 3, "Название клипа для проверки", "описание", 7.5))
    row = db.clip(cid)
    assert bn.ensure_final(db, row, cfg) == base                                  # без вставки — исходный файл

    db.update_clip(cid, filler=json.dumps({"position": "bottom", "file": ""}))
    out = bn.ensure_final(db, db.clip(cid), cfg)
    info = bn.probe(out)
    assert out.name.endswith(".filler.mp4") and (info["w"], info["h"]) == (1080, 1920) and info["has_audio"]
    assert abs(info["duration"] - 3) < 0.3
    spec = json.loads(db.clip(cid)["filler"])
    assert spec["picked"] in {"blue.mp4", "green.mp4"} and "offset" in spec        # случайный выбор запомнен
    mtime = out.stat().st_mtime_ns
    assert bn.ensure_final(db, db.clip(cid), cfg) == out and out.stat().st_mtime_ns == mtime   # повтор — из кеша
    db.update_clip(cid, title="Другое название")
    bn.ensure_final(db, db.clip(cid), cfg)
    assert out.stat().st_mtime_ns != mtime                                         # название изменилось — пересобрано

    # заданный файл и верхнее расположение; проверяем пиксели: сверху полоска (чёрная), под ней вставка, внизу белый клип
    db.update_clip(cid, filler=json.dumps({"position": "top", "file": "green.mp4"}))
    out = bn.ensure_final(db, db.clip(cid), cfg)
    assert json.loads(db.clip(cid)["filler"])["picked"] == "green.mp4"
    lay = filler.layout(cfg, "top")

    def pix(y):
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "1", "-i", str(out), "-vf", f"crop=2:2:540:{y},format=rgb24", "-frames:v", "1",
                            "-f", "rawvideo", "-"], capture_output=True, check=True)
        return tuple(r.stdout[:3])
    assert pix(2) == (0, 0, 0) or max(pix(2)) < 40                                 # полоска названия (на краю текста нет)
    r, g_, b_ = pix(lay["vid_y"] + lay["vid_h"] // 2)
    assert g_ > 100 and r < 80 and b_ < 80                                         # зелёная вставка
    assert min(pix(lay["main_y"] + lay["main_h"] // 2)) > 200                      # белый клип

    db.update_clip(cid, filler=json.dumps({"position": "top", "file": "нет.mp4"}))
    with pytest.raises(filler.FillerError):
        bn.ensure_final(db, db.clip(cid), cfg)
    shutil.rmtree(lib)
    db.update_clip(cid, filler=json.dumps({"position": "top", "file": ""}))
    with pytest.raises(filler.FillerError):
        bn.ensure_final(db, db.clip(cid), cfg)


@needs_ffmpeg
def test_api_filler_endpoints_and_patch(tmp_path):
    from fastapi.testclient import TestClient
    from narez.web.server import create_app
    cfg = mk_cfg(tmp_path)
    cfg["paths"]["db"] = tmp_path / "t.db"
    app = create_app(cfg)
    c = TestClient(app)
    src = tmp_path / "v.mp4"
    _video(src, 360, 640, 2, "green")
    assert c.get("/api/fillers").json()["files"] == []
    assert c.put("/api/fillers?name=a.txt", content=b"x").status_code == 400
    r = c.put("/api/fillers?name=%D0%BF%D0%B5%D1%81%D0%BE%D0%BA.mp4", content=src.read_bytes())
    assert r.status_code == 200
    files = c.get("/api/fillers").json()["files"]
    assert len(files) == 1 and files[0]["width"] == 360
    assert c.get("/api/fillers/file?name=" + files[0]["name"]).status_code == 200

    db = DB(tmp_path / "t.db")
    g = db.add_group("g")
    j = db.add_job(g, "u", "t")
    cid = db.add_clip(j, str(src), Moment(0, 2, "T", "d", 5))
    r = c.patch(f"/api/clips/{cid}", json={"filler": {"position": "top", "file": ""}})
    assert r.status_code == 200 and r.json()["filler"] == {"position": "top", "file": ""}
    assert c.patch(f"/api/clips/{cid}", json={"filler": {"position": "top", "file": "нет.mp4"}}).status_code == 400
    assert c.patch(f"/api/clips/{cid}", json={"filler": {"position": ""}}).json()["filler"] is None
    assert c.delete("/api/fillers?name=" + files[0]["name"]).status_code == 200
    assert c.get("/api/fillers").json()["files"] == []


def test_fit_subs_rewrites_playres_and_margin():
    from narez.subtitles import build_ass
    cfg = load_config()
    ass = build_ass([(0.0, 1.0, "привет мир")], cfg)
    out = filler.fit_subs(ass, 998, 70)
    assert "PlayResY: 998" in out and ",2,60,60,70,1" in out and "привет мир" in out
    assert filler.fit_subs(ass, 998, 300).count(",2,60,60,300,1") == 1


@needs_ffmpeg
def test_direct_mode_uses_source_and_subs(tmp_path):
    from narez.subtitles import build_ass
    cfg = mk_cfg(tmp_path)
    lib = filler.library_dir(cfg)
    lib.mkdir(parents=True)
    _video(lib / "blue.mp4", 640, 360, 3, "blue")
    # исходник 16:9: верхняя половина зелёная, нижняя — пурпурная (в режиме «из исходника» полоса заполнена без размытых полей)
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=green:s=1280x720:r=25:d=6",
                    "-f", "lavfi", "-i", "sine=duration=6", "-vf", "drawbox=x=0:y=360:w=1280:h=360:color=magenta:t=fill,format=yuv420p",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src)], check=True)
    clip = tmp_path / "clip.mp4"
    _video(clip, 1080, 1920, 3, "white")
    filler.write_sidecar(clip, src, 1.0, 4.0, build_ass([(0.0, 2.0, "тест")], cfg))
    sc = filler.read_sidecar(clip)
    assert sc and sc["ass_text"] and abs(sc["end"] - 4.0) < 1e-6
    db = DB(tmp_path / "t.db")
    g = db.add_group("g")
    cid = db.add_clip(db.add_job(g, "u", "t"), str(clip), Moment(0, 3, "Название", "d", 5))
    db.update_clip(cid, filler=json.dumps({"position": "bottom", "file": "blue.mp4"}))
    logs = []
    out = bn.ensure_final(db, db.clip(cid), cfg, logs.append)
    info = bn.probe(out)
    assert (info["w"], info["h"]) == (1080, 1920) and abs(info["duration"] - 3) < 0.3 and info["has_audio"]
    assert not any("исходник недоступен" in x for x in logs)
    lay = filler.layout(cfg, "bottom")

    def pix(x, y):
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "2.5", "-i", str(out), "-vf", f"crop=2:2:{x}:{y},format=rgb24",
                            "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True)
        return tuple(r.stdout[:3])
    top, bottom = pix(10, 6), pix(10, lay["main_h"] - 6)
    assert top[1] > 100 and top[0] < 80                      # верх главной полосы — зелёный из исходника
    assert bottom[0] > 150 and bottom[1] < 80                # низ — пурпурный, без размытых полей и белого клипа
    # баннер ложится поверх готового кадра; повторная сборка берёт кэш
    img = tmp_path / "b.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=400x200", "-frames:v", "1", str(img)], check=True)
    spec = bn.normalize_spec({"path": str(img), "start": 0, "duration": 2, "width_percent": 50, "position": "top"}, cfg)
    db.update_clip(cid, banner=bn.specs_to_json([spec]))
    out2 = bn.ensure_final(db, db.clip(cid), cfg, logs.append)
    assert out2.name.endswith(".ff.mp4") and bn.cached_final(db.clip(cid), cfg) == out2
    # исходник пропал — резервный вариант из готового клипа
    src.unlink()
    db.update_clip(cid, banner="")
    logs.clear()
    bn.ensure_final(db, db.clip(cid), cfg, logs.append)
    assert any("исходник недоступен" in x for x in logs)
