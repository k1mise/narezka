from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from narez.config import load_config
from narez.editor import render_clip
from narez.llm import parse_json
from narez.models import Moment, Segment, Word
from narez.moments import select_moments, snap
from narez.subtitles import build_ass, clip_words, group_words


def fake_segments(n=40, dur=5.0):
    segs = []
    for i in range(n):
        s = i * dur
        words = [Word(s + j * 1.0, s + j * 1.0 + 0.9, f"слово{j}") for j in range(5)]
        segs.append(Segment(s, s + dur, " ".join(w.text for w in words), words))
    return segs


def test_parse_json_fenced():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_snap_limits():
    segs = fake_segments()
    for start, end in [(12, 20), (12, 300), (50, 200)]:
        m = snap(Moment(start, end, "t"), segs, 30, 60)
        assert m and 30 <= m.duration <= 60
        assert m.start % 5 == 0 and m.end % 5 == 0  # границы по фразам


def test_select_no_overlap_and_limit():
    segs = fake_segments()
    found = [Moment(10, 50, "a", score=9), Moment(20, 60, "b", score=8), Moment(100, 140, "c", score=7)]
    res = select_moments(found, segs, 30, 60, 2)
    assert len(res) == 2
    assert all(a.end <= b.start for a, b in zip(res, res[1:]))


def test_ass_and_words():
    cfg = load_config()
    segs = fake_segments()
    words = clip_words(segs, 10, 40)
    assert words[0].start == 0
    ass = build_ass(group_words(words, 4), cfg)
    assert "Dialogue:" in ass and "PlayResX: 1080" in ass


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
@pytest.mark.parametrize("layout,banner", [("blur", False), ("crop", True)])
def test_render(tmp_path, layout, banner):
    cfg = load_config()
    cfg["_root"] = str(tmp_path)
    cfg["clips"]["layout"] = layout
    cfg["clips"]["preset"] = "ultrafast"
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "20", "-c:v", "libx264", "-c:a", "aac",
                    str(src)], check=True)
    if banner:
        img = tmp_path / "banner.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=600x200",
                        "-frames:v", "1", str(img)], check=True)
        cfg["banner"].update(enabled=True, image=str(img))
    ass = build_ass(group_words(clip_words(fake_segments(4, 5.0), 0, 10), 4), cfg)
    out = render_clip(src, 2, 12, tmp_path / "o.mp4", ass, cfg)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:format=duration", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout
    assert "1080,1920" in probe


def test_youtube_body():
    import datetime as dt
    from narez.publishers.youtube import YouTubePublisher
    cfg = load_config()
    p = YouTubePublisher(cfg)
    b = p.build_body("A" * 200, "desc")
    assert len(b["snippet"]["title"]) <= 90 and "#Shorts" in b["snippet"]["description"]
    assert b["status"]["privacyStatus"] == "private" and "publishAt" not in b["status"]
    b = p.build_body("t", "d", dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc))
    assert b["status"]["publishAt"] == "2026-10-01T12:00:00.000Z"


def test_refine_moves_start(monkeypatch):
    from narez import moments
    cfg = load_config()
    segs = fake_segments()
    # модель выбирает фразы 4..12 окна; окно начинается с сегмента, где end >= start-25
    monkeypatch.setattr(moments, "chat_json", lambda *a, **k: {"start": 4, "end": 12, "hook": "x"})
    m = moments.refine_moment(Moment(50, 85, "t"), segs, cfg)
    assert 30 <= m.duration <= 60
    monkeypatch.setattr(moments, "chat_json", lambda *a, **k: {"start": 99, "end": 100})
    assert moments.refine_moment(Moment(50, 85, "t"), segs, cfg).start == 50  # мусор -> без изменений


def test_sentences_end_on_punctuation():
    from narez.transcriber import to_sentences
    # whisper-сегмент режет предложение посередине: "Это очень важная" | "мысль. А потом"
    t = 0.0
    def seg(text):
        nonlocal t
        ws = []
        for w in text.split():
            ws.append(Word(t, t + 0.4, w)); t += 0.5
        return Segment(ws[0].start, ws[-1].end, text, ws)
    segs = [seg("Это очень важная"), seg("мысль. А потом пошло"), seg("всё иначе?")]
    sents = to_sentences(segs)
    assert [s.text for s in sents] == ["Это очень важная мысль.", "А потом пошло всё иначе?"]


def test_snap_ends_on_sentence():
    from narez.transcriber import to_sentences
    words = []
    t = 0.0
    for i in range(300):
        words.append(Word(t, t + 0.4, f"w{i}" + ("." if i % 9 == 8 else "")))
        t += 0.5
    sents = to_sentences([Segment(0, t, "", words)])
    m = snap(Moment(20, 60, "x"), sents, 30, 60)
    assert m and 30 <= m.duration <= 60
    assert any(abs(m.end - s.end) < 1e-6 for s in sents)  # конец = конец предложения


def test_prompt_and_cache_name():
    from narez.transcriber import build_prompt
    p = build_prompt("Что делать в GTA 5", "Лос-Сантос, дропнуть")
    assert "GTA 5" in p and "Лос-Сантос" in p and len(build_prompt("x" * 1000, "")) <= 400


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_find_cut_in_silence(tmp_path):
    from narez.editor import find_cut
    wav = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=5", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                    "-filter_complex", "[1:a]atrim=0:0.6[s];[0:a][s][2:a]concat=n=3:v=0:a=1", str(wav)], check=True)
    end = find_cut(wav, 5.0, "end")
    assert end is not None and 5.0 <= end <= 5.6          # внутри паузы 5.0–5.6
    start = find_cut(wav, 5.6, "start")
    assert start is not None and 5.0 <= start <= 5.6
    assert find_cut(wav, 2.0, "end") is None              # посреди речи паузы нет


def test_fix_segments_accepts_only_same_word_count(monkeypatch):
    from narez import subtitles
    cfg = load_config()
    seg = Segment(0, 3, "Я бродил по закаукам", [Word(0, 1, "Я"), Word(1, 2, "бродил"), Word(2, 2.5, "по"), Word(2.5, 3, "закаукам")])
    calls = []
    def fake(*a, **k):
        calls.append(1)
        return {"fixed": ["Я бродил по закоулкам", "Я бродил по закоулкам игры"]}
    monkeypatch.setattr(subtitles, "chat_json", fake)
    res = subtitles.fix_segments([seg, seg], cfg, "GTA", log=lambda *_: None)
    assert len(calls) == 1                                   # один запрос на оба предложения
    assert res[0].words[3].text == "закоулкам"
    assert res[1].words[3].text == "закаукам"                # лишнее слово -> без изменений
    monkeypatch.setattr(subtitles, "chat_json", lambda *a, **k: {"fixed": ["только одно"]})
    assert subtitles.fix_segments([seg, seg], cfg, "GTA", log=lambda *_: None)[0].words[3].text == "закаукам"  # неверная длина списка


def test_accept_filters_noise():
    from narez.subtitles import _accept
    V = {"тревор"}
    assert _accept("закаукам", "закоулкам", V) == "закоулкам"
    assert _accept("котек,", "котиков,", V) == "котиков,"      # пунктуация сохранилась
    assert _accept("не", "Не", V) == "не"                      # только регистр
    assert _accept("проведете", "проведёте", V) == "проведете" # только ё
    assert _accept("он", "оно", V) == "он"                     # короткое слово
    assert _accept("андеграунда.", "подземки.", V) == "андеграунда."  # подмена синонимом
    assert _accept("тревор", "Тревор", V) == "Тревор"          # имя из словаря


def test_make_metadata(monkeypatch):
    from narez import moments
    cfg = load_config()
    segs = fake_segments()
    m = Moment(10, 50, "старый", "причина")
    monkeypatch.setattr(moments, "chat_json", lambda *a, **k: {"title": "«Новый» заголовок", "description": "Коротко о главном.",
                                                                "hashtags": ["gta5", "#лайфхаки", "плохой тег"]})
    t, d = moments.make_metadata(m, segs, cfg, "Исходник", "https://youtu.be/x")
    assert t == "Новый» заголовок".replace("»", "") or t.startswith("Новый")
    assert "Коротко о главном." in d and "Источник: Исходник https://youtu.be/x" in d
    assert "#gta5" in d and "#лайфхаки" in d and "плохой" not in d
    monkeypatch.setattr(moments, "chat_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert moments.make_metadata(m, segs, cfg) == ("старый", "причина")


# ---------- редактирование, баннер, история, очистка ----------

def test_meta_split_and_compose():
    from narez.meta import api_tags, compose_description, normalize_tags, split_hashtags
    body, tags = split_hashtags("Коротко о главном.\n\nИсточник: X https://y\n\n#gta5 #лайфхаки")
    assert body == "Коротко о главном.\n\nИсточник: X https://y" and tags == "#gta5 #лайфхаки"
    assert split_hashtags("Просто текст") == ("Просто текст", "")
    assert compose_description(body, tags).endswith("#gta5 #лайфхаки")
    assert compose_description("", "#a") == "#a"
    assert normalize_tags("gta5, #лайфхаки  плохой тег, GTA5") == "#gta5 #лайфхаки #плохой #тег"
    assert api_tags("#gta5 #лайфхаки") == ["gta5", "лайфхаки"]


def make_db(tmp_path):
    from narez.db import DB
    return DB(tmp_path / "t.db")


def test_db_migration_and_clip_flow(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)  # «старая» база без новых колонок
    c.executescript("CREATE TABLE groups(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);"
                    "CREATE TABLE jobs(id INTEGER PRIMARY KEY, group_id INTEGER, url TEXT, title TEXT, created TEXT);"
                    "CREATE TABLE clips(id INTEGER PRIMARY KEY, job_id INTEGER, path TEXT, start REAL, end REAL, "
                    "title TEXT, reason TEXT, score REAL, status TEXT DEFAULT 'ready');")
    c.commit(); c.close()
    from narez.db import DB
    db = DB(p)
    g = db.add_group("g")
    j = db.add_job(g, "u", "t", "/x/src.mp4")
    cid = db.add_clip(j, "/x/a.mp4", Moment(0, 40, "T", "D", 5), tags="#a", banner='{"path":"b.png"}')
    assert [r["id"] for r in db.pending_clips(g)] == [cid]
    db.update_clip(cid, title="Новый", reason="Опис")
    assert db.clip(cid)["title"] == "Новый" and db.clip(cid)["tags"] == "#a"
    with pytest.raises(ValueError):
        db.update_clip(cid, id=5)


def test_publication_history(tmp_path):
    db = make_db(tmp_path)
    g = db.add_group("g")
    db.set_account(g, "youtube", "main")
    acc = db.accounts(g)[0]["id"]
    j = db.add_job(g, "u", "t")
    cid = db.add_clip(j, "a.mp4", Moment(0, 40, "T", "D", 5))
    pid = db.add_publication(cid, acc, g, "youtube", "https://youtube.com/shorts/ABC123", "T", "th.png", "")
    row = db.publications()[0]
    assert row["video_id"] == "ABC123" and row["group_name"] == "g" and row["account_label"] == "main"
    assert db.clip_published_accounts(cid) == {acc}
    db.update_stats(pid, 10, 2, 1, "public")
    assert db.publications("youtube")[0]["views"] == 10


def test_cleanup_after_publication(tmp_path):
    from narez.cleanup import discard_clip, finish_group
    db = make_db(tmp_path)
    cfg = load_config()
    wd = tmp_path / "wd"
    cfg["paths"]["workdir"] = wd
    (wd / "downloads").mkdir(parents=True)
    (wd / "clips" / "job1").mkdir(parents=True)
    src = wd / "downloads" / "vid.mp4"; src.write_bytes(b"x" * 100)
    cache = wd / "downloads" / "vid.transcript.small.json"; cache.write_text("{}")
    other = wd / "downloads" / "other.mp4"; other.write_bytes(b"x")
    outside = tmp_path / "outside.mp4"; outside.write_bytes(b"x")  # файл вне workdir трогать нельзя
    g = db.add_group("g")
    db.set_account(g, "youtube", "main"); db.set_account(g, "tiktok", "tt")
    yt = next(a["id"] for a in db.accounts(g) if a["platform"] == "youtube")
    tt = next(a["id"] for a in db.accounts(g) if a["platform"] == "tiktok")
    j = db.add_job(g, "u", "t", str(src))
    paths = []
    for i in range(2):
        p = wd / "clips" / "job1" / f"{i}.mp4"; p.write_bytes(b"c"); paths.append(p)
    c1 = db.add_clip(j, str(paths[0]), Moment(0, 40, "A", "", 5))
    c2 = db.add_clip(j, str(paths[1]), Moment(50, 90, "B", "", 5))
    # пока ничего не опубликовано — ничего не трогаем
    assert finish_group(db, cfg, g, lambda *_: None) == 0 and src.exists() and paths[0].exists()
    db.add_publication(c1, yt, g, "youtube", "https://youtube.com/shorts/A", "A")
    finish_group(db, cfg, g, lambda *_: None)
    assert paths[0].exists()                                               # TikTok теперь тоже настоящая платформа: ждём и её
    db.add_publication(c1, tt, g, "tiktok", "https://www.tiktok.com/@me/video/1", "A")
    finish_group(db, cfg, g, lambda *_: None)
    assert not paths[0].exists() and paths[1].exists() and src.exists()   # исходник нужен, пока есть клип в очереди
    assert db.clip(c1)["status"] == "published"
    discard_clip(db, cfg, c2)                                              # убрали второй клип вручную
    finish_group(db, cfg, g, lambda *_: None)
    assert not paths[1].exists() and not src.exists() and not cache.exists()
    assert other.exists() and outside.exists()                             # чужое не тронуто
    assert db.job(j)["source_path"] == ""


def test_cleanup_ignores_dry_run_only_groups(tmp_path):
    from narez.cleanup import finish_group
    db = make_db(tmp_path)
    cfg = load_config(); cfg["paths"]["workdir"] = tmp_path
    g = db.add_group("g"); db.set_account(g, "tiktok", "tt")
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    j = db.add_job(g, "u", "t", str(f)); db.add_clip(j, str(f), Moment(0, 40, "A", "", 5))
    assert finish_group(db, cfg, g) == 0 and f.exists()                   # «сухие» площадки ничего не удаляют


def test_banner_geometry():
    from narez.banner import geometry
    cfg = load_config()
    w, h, frac, ok = geometry({"width_percent": 40}, {"kind": "video", "w": 1280, "h": 720}, cfg)
    assert ok and frac >= 0.25 and w <= 1080                               # узкий видео-баннер растянулся до 25%+
    w, h, frac, ok = geometry({"width_percent": 100}, {"kind": "video", "w": 3000, "h": 1000}, cfg)
    assert ok and frac >= 0.25 and w == 1080 and h <= 1920                 # слишком вытянутый: растянут по высоте непропорционально, в кадре
    w, h, frac, ok = geometry({"width_percent": 30}, {"kind": "image", "w": 600, "h": 200}, cfg)
    assert ok and w == 324                                                 # картинка: ширина как задана, минимум не навязываем


def _tools(tmp_path):
    base = tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "12", "-c:v", "libx264", "-c:a", "aac",
                    "-pix_fmt", "yuv420p", str(base)], check=True)
    img = tmp_path / "b.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=600x200",
                    "-frames:v", "1", str(img)], check=True)
    vid = tmp_path / "b.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=blue:s=640x360:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=1000", "-t", "4", "-c:v", "libx264", "-c:a", "aac",
                    "-pix_fmt", "yuv420p", str(vid)], check=True)
    silent = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=green:s=640x360:r=25",
                    "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)], check=True)
    return base, img, vid, silent


def _vol(path, a, b):
    """Средняя громкость (дБ) на отрезке [a, b] — чтобы проверить приглушение звука."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", str(a), "-t", str(b - a), "-i", str(path),
                        "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    import re
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", p.stderr).group(1))


def _rgb_at(path, t, x, y):
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", f"crop=1:1:{x}:{y},format=rgb24", "-f", "rawvideo", "-"], capture_output=True).stdout
    return tuple(out[:3])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_apply_banner_image_and_video(tmp_path):
    from narez.banner import apply_banner, default_spec, probe
    cfg = load_config(); cfg["clips"]["preset"] = "ultrafast"; cfg["_root"] = str(tmp_path)
    base, img, vid, silent = _tools(tmp_path)

    # картинка: появляется на 3-й секунде на 4 секунды, до и после — чистый кадр
    spec = default_spec(cfg, img); spec.update(start=3, duration=4, width_percent=50)
    out = apply_banner(base, tmp_path / "i.mp4", spec, cfg)
    assert probe(out)["w"] == 1080 and abs(probe(out)["duration"] - 12) < 0.3 and probe(out)["has_audio"]
    mid = _rgb_at(out, 5, 540, 960)
    assert mid[0] > 200 and mid[1] < 60 and mid[2] < 60                     # красный баннер в центре
    assert _rgb_at(out, 1, 540, 960) != mid and _rgb_at(out, 9, 540, 960) != mid  # вне окна баннера нет

    # видео со звуком: звук основного видео приглушается, пока идёт баннер, потом возвращается
    spec = default_spec(cfg, vid); spec.update(start=4, duck_percent=10)
    out = apply_banner(base, tmp_path / "v.mp4", spec, cfg)
    assert abs(probe(out)["duration"] - 12) < 0.3
    b = _rgb_at(out, 6, 540, 960)
    assert b[2] > 150 and b[0] < 60                                         # синее видео-баннер в центре
    assert _rgb_at(out, 9, 540, 960) != b                                   # баннер (4 с, с 4-й по 8-ю) закончился
    # вне окна баннера громкость такая же, как у исходного клипа (приглушение точно проверяет test_banner_ducks_main_audio)
    ctrl = _vol(base, 5, 7)
    assert abs(_vol(out, 1, 3) - ctrl) < 2 and abs(_vol(out, 10, 12) - ctrl) < 2

    # видео без звука: звук основного видео остаётся нетронутым
    spec = default_spec(cfg, silent); spec.update(start=2, duration=0)
    out = apply_banner(base, tmp_path / "out_silent.mp4", spec, cfg)
    assert abs(_vol(out, 3, 4) - _vol(base, 3, 4)) < 1.5


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_banner_ducks_main_audio(tmp_path):
    """Баннер без собственного тона, но «со звуком» (тишина): основной звук должен просесть до duck_percent."""
    from narez.banner import apply_banner, default_spec
    cfg = load_config(); cfg["clips"]["preset"] = "ultrafast"; cfg["_root"] = str(tmp_path)
    base, *_ = _tools(tmp_path)
    quiet = tmp_path / "q.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=blue:s=640x360:r=25",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "4", "-c:v", "libx264", "-c:a", "aac",
                    "-pix_fmt", "yuv420p", str(quiet)], check=True)
    spec = default_spec(cfg, quiet); spec.update(start=4, duck_percent=10)
    out = apply_banner(base, tmp_path / "d.mp4", spec, cfg)
    drop = _vol(base, 5, 7) - _vol(out, 5, 7)
    assert 15 < drop < 25                                                  # 10% громкости ≈ -20 дБ


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_ensure_final_caches_and_rerenders(tmp_path):
    import json
    from narez.banner import default_spec, ensure_final
    cfg = load_config(); cfg["clips"]["preset"] = "ultrafast"; cfg["_root"] = str(tmp_path)
    base, img, *_ = _tools(tmp_path)
    db = make_db(tmp_path)
    g = db.add_group("g"); j = db.add_job(g, "u", "t")
    cid = db.add_clip(j, str(base), Moment(0, 40, "A", "", 5))
    assert ensure_final(db, db.clip(cid), cfg) == base                      # без баннера — исходный клип
    spec = default_spec(cfg, img)
    db.update_clip(cid, banner=json.dumps(spec))
    f1 = ensure_final(db, db.clip(cid), cfg)
    assert f1 != base and f1.exists()
    m1 = f1.stat().st_mtime_ns
    assert ensure_final(db, db.clip(cid), cfg) == f1 and f1.stat().st_mtime_ns == m1   # повторно не пересобирается
    spec["start"] = 5
    db.update_clip(cid, banner=json.dumps(spec))
    ensure_final(db, db.clip(cid), cfg)
    assert f1.stat().st_mtime_ns != m1                                      # настройки поменяли — собрали заново


def test_youtube_body_tags_and_scope(monkeypatch):
    from narez.publishers.youtube import YouTubePublisher
    p = YouTubePublisher(load_config())
    b = p.build_body("t", "d", tags=["gta5", "лайфхаки"])
    assert b["snippet"]["tags"] == ["gta5", "лайфхаки"]
    assert "tags" not in p.build_body("t", "d")["snippet"]


def test_parse_video_ids_and_import(tmp_path):
    from narez.history import import_videos, parse_video_ids
    ids = parse_video_ids("https://www.youtube.com/shorts/7DETubxR3Jg\nhttps://youtu.be/AAAAAAAAAAA?si=x, "
                          "https://www.youtube.com/watch?v=BBBBBBBBBBB&t=5 7DETubxR3Jg CCCCCCCCCCC мусор")
    assert ids == ["7DETubxR3Jg", "AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC"]
    assert parse_video_ids("") == []
    db = make_db(tmp_path)
    cfg = load_config(); cfg["paths"]["workdir"] = tmp_path
    g = db.add_group("g"); db.set_account(g, "youtube", "main"); acc = db.accounts(g)[0]["id"]
    db.add_publication(None, acc, g, "youtube", "https://youtube.com/shorts/AAAAAAAAAAA", "уже есть")

    class Pub:
        def fetch_stats(self, account_id, vids):
            assert vids == ["7DETubxR3Jg", "BBBBBBBBBBB", "CCCCCCCCCCC"]            # уже известное не запрашиваем
            ok = lambda t: {"title": t, "published": "2026-10-05T10:00:00Z", "views": 5, "likes": 1, "comments": 0,
                            "privacy": "public", "publish_at": ""}
            return {"7DETubxR3Jg": ok("Первый"), "BBBBBBBBBBB": ok("Второй"),
                    "CCCCCCCCCCC": {"title": "", "published": "", "views": None, "likes": None, "comments": None,
                                    "privacy": "deleted", "publish_at": ""}}
    logs = []
    n = import_videos(db, cfg, Pub(), acc, g, ids, logs.append, thumb_fn=lambda *a, **k: None)
    assert n == 2 and any("не найдено" in x for x in logs)
    rows = {r["video_id"]: r for r in db.publications()}
    assert rows["7DETubxR3Jg"]["title"] == "Первый" and rows["7DETubxR3Jg"]["views"] == 5
    assert rows["7DETubxR3Jg"]["uploaded_at"] == "2026-10-05 10:00:00"
    assert import_videos(db, cfg, Pub(), acc, g, ["AAAAAAAAAAA"], logs.append, thumb_fn=lambda *a, **k: None) == 0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_make_thumb_from_image_vertical(tmp_path):
    from narez.editor import make_thumb
    jpg = tmp_path / "t.jpg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360",
                    "-frames:v", "1", str(jpg)], check=True)
    out = make_thumb(jpg, tmp_path / "o.png", at=0.0, vertical=True)
    assert out and out.exists()
    w, h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0:s=x", str(out)], capture_output=True, text=True).stdout.strip().split("x")
    assert int(h) == 128 and int(w) < int(h)                                # вертикальная миниатюра
    assert make_thumb(tmp_path / "нет.jpg", tmp_path / "x.png", at=0.0) is None


def test_schedule_parse_and_pick():
    import datetime as dt
    from narez import schedule as S
    d = S.parse_local("07.10.2026 18:30")
    assert d.tzinfo is not None and S.format_local(S.to_iso(d)) == "07.10.2026 18:30"   # круг: ввод -> UTC -> показ
    assert S.parse_local("  ") is None
    with pytest.raises(ValueError):
        S.parse_local("завтра в 5")
    now = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.timezone.utc)
    future, past = "2026-10-07T15:00:00Z", "2026-10-07T09:00:00Z"
    assert S.pick_when(future, now, 60, 0) == (dt.datetime(2026, 10, 7, 15, 0, tzinfo=dt.timezone.utc), 0)  # своё время
    assert S.pick_when(past, now, 60, 3) == (None, 3)                                    # время прошло -> сразу
    assert S.pick_when("", now, 60, 0) == (None, 1)                                      # первый без времени — сразу
    when, k = S.pick_when("", now, 60, 1)
    assert when == now + dt.timedelta(minutes=62) and k == 2                             # второй — с задержкой
    assert S.pick_when("", now, 0, 5)[0] is None                                         # delay=0 — всё сразу


def _region(path, t):
    """Хэш нижней части кадра (там у testsrc бегут цифры), вне зоны баннера по центру."""
    import hashlib
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", "crop=1080:500:0:1400,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    return hashlib.md5(out).hexdigest()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_banner_pause_freezes_main_video(tmp_path):
    """pause: основное видео замирает на время баннера (клип длиннее на длительность баннера), звук основного молчит."""
    from narez.banner import apply_banner, default_spec, probe
    cfg = load_config(); cfg["clips"]["preset"] = "ultrafast"; cfg["_root"] = str(tmp_path)
    base, img, vid, silent = _tools(tmp_path)

    # видео-баннер 4 с со своим звуком (1000 Гц), старт на 3-й секунде
    spec = default_spec(cfg, vid); spec.update(start=3, pause=True)
    out = apply_banner(base, tmp_path / "p.mp4", spec, cfg)
    info = probe(out)
    assert abs(info["duration"] - 16) < 0.4 and info["has_audio"]                # 12 с клипа + 4 с паузы
    # угол кадра (баннер его не закрывает): до паузы меняется, на паузе (3–7 с) не меняется, после — снова идёт
    corner = lambda t: _region(out, t)
    assert corner(1) != corner(2)
    assert corner(3.6) == corner(5) == corner(6.5)
    assert corner(8) != corner(9.5)
    # на паузе слышен только баннер, а основной звук (440 Гц) молчит: громкость ≈ как у самого баннера
    assert abs(_vol(out, 4, 6) - _vol(vid, 1, 3)) < 3
    # после паузы основной звук возвращается
    assert abs(_vol(out, 9, 11) - _vol(base, 3, 5)) < 2

    # картинка 5 секунд: пауза тоже работает, звук основного на паузе молчит (тишина)
    spec = default_spec(cfg, img); spec.update(start=4, duration=5, pause=True, width_percent=50)
    out2 = apply_banner(base, tmp_path / "pi.mp4", spec, cfg)
    assert abs(probe(out2)["duration"] - 17) < 0.4
    assert _region(out2, 5) == _region(out2, 8)
    assert _rgb_at(out2, 6, 540, 960)[0] > 200                                  # красный баннер в центре


def test_subprocess_text_calls_force_utf8():
    """На Windows text=True без encoding читает вывод ffmpeg как cp1251 и падает на кириллице в метаданных
    (UnicodeDecodeError -> stderr=None -> «'NoneType' object has no attribute 'splitlines'»)."""
    import re
    bad = []
    for p in (Path(__file__).resolve().parent.parent / "narez").rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r"subprocess\.run\((?:[^()]|\([^()]*\))*\)", src, flags=re.S):
            call = m.group(0)
            if "text=True" in call and "encoding=" not in call:
                bad.append(f"{p.name}: {call[:60]}")
    assert not bad, bad



def test_sync_channels_adds_missing_shorts_only(tmp_path):
    from narez.history import sync_channels
    db = make_db(tmp_path)
    cfg = load_config(); cfg["paths"]["workdir"] = tmp_path
    g1, g2 = db.add_group("A"), db.add_group("B")
    db.set_account(g1, "youtube", "yt1"); db.set_account(g2, "youtube", "yt2"); db.set_account(g2, "tiktok", "tt")
    a1 = next(a["id"] for a in db.accounts(g1)); a2 = next(a["id"] for a in db.accounts(g2) if a["platform"] == "youtube")
    db.add_publication(None, a1, g1, "youtube", "https://youtube.com/shorts/KNOWNKNOWN1", "уже есть")

    class Pub:
        def list_uploads(self, acc, limit):
            if acc == a2:
                raise RuntimeError("нет токена")
            assert limit == 200
            return ["KNOWNKNOWN1", "NEWSHORT001", "LONGVIDEO01", "DELETED0001"]

        def fetch_stats(self, acc, ids):
            assert ids == ["NEWSHORT001", "LONGVIDEO01", "DELETED0001"]
            mk = lambda t, d, pr="public": {"title": t, "published": "2026-10-01T10:00:00Z", "views": 7, "likes": 2, "comments": 1,
                                             "privacy": pr, "publish_at": "", "duration": d}
            return {"NEWSHORT001": mk("Новый шортс", 40), "LONGVIDEO01": mk("Длинное", 900), "DELETED0001": mk("", 0, "deleted")}
    logs = []
    assert sync_channels(db, cfg, Pub(), logs.append, thumb_fn=lambda *a, **k: None) == 1
    rows = {r["video_id"]: r for r in db.publications()}
    assert set(rows) == {"KNOWNKNOWN1", "NEWSHORT001"}                      # длинное видео и удалённое не добавлены
    assert rows["NEWSHORT001"]["group_id"] == g1 and rows["NEWSHORT001"]["views"] == 7 and rows["NEWSHORT001"]["title"] == "Новый шортс"
    assert any("не удалось получить список" in x for x in logs) and any("добавлено 1" in x for x in logs)
    cfg["history"] = {"sync_channel": False}
    assert sync_channels(db, cfg, Pub(), logs.append) == 0                  # можно отключить в config.toml
    assert sync_channels(db, load_config(), object(), logs.append) == 0     # у публикатора нет list_uploads — молча пропускаем


def test_iso_duration():
    from narez.publishers.youtube import iso_duration
    assert iso_duration("PT1M5S") == 65 and iso_duration("PT45S") == 45 and iso_duration("PT3M") == 180 and iso_duration("") == 0
