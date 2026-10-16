"""Вырезание рекламы: маркеры, окна, проверка моделью, SponsorBlock, пересчёт транскрипта, вырезание ffmpeg, пайплайн."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from narez import adcut
from narez.adcut import Range
from narez.config import load_config
from narez.llm import LLMError
from narez.models import Segment, Word

TALK = ["Сегодня мы поговорим о собаках.", "Первое правило — терпение.", "Щенок учится только через повтор.",
        "Не кричите на него никогда.", "Это только портит доверие.", "Лучше хвалите за малейший успех."]
AD = ["Кстати, этот выпуск при поддержке сервиса быстрой доставки.", "Они привозят продукты за пятнадцать минут.",
      "Я сам заказываю там корм для собаки.", "Скачайте приложение по ссылке в описании.",
      "По промокоду СОБАКА вы получите скидку на первый заказ.", "Ссылка под видео, переходите."]


def build(parts: list[list[str]], step: float = 5.0) -> list[Segment]:
    """Фразы по step секунд подряд; слова равномерно внутри фразы."""
    out, t = [], 0.0
    for part in parts:
        for text in part:
            ws = text.split()
            d = (step - 1.0) / len(ws)
            words = [Word(t + i * d, t + (i + 1) * d, w) for i, w in enumerate(ws)]
            out.append(Segment(words[0].start, words[-1].end, text, words))
            t += step
    return out


def cfgx(**kw):
    cfg = load_config()
    cfg["adcut"] = kw
    return cfg


def test_marker_score_and_plain_text():
    assert adcut.marker_score("По промокоду СОБАКА скидка 20%")[1] >= 2
    assert adcut.marker_score("Ссылка в описании под видео")[1] >= 1
    assert adcut.marker_score("Щенок учится только через повтор.") == (0, 0)
    assert adcut.marker_score("Зайдите на сайт") == (1, 0)      # одно слабое слово — не повод


def test_candidate_windows_only_around_ad():
    sents = build([TALK * 4, AD, TALK * 4])
    sents_plain = build([TALK * 6])
    assert adcut.candidate_windows(sents_plain, cfgx()) == []
    w = adcut.candidate_windows(sents, cfgx())
    assert len(w) == 1
    a0, a1 = len(TALK) * 4, len(TALK) * 4 + len(AD) - 1
    assert w[0][0] <= a0 and w[0][1] >= a1                       # окно накрывает всю вставку
    assert w[0][0] > 0 and w[0][1] < len(sents) - 1              # но не всё видео: только контекст вокруг


def test_detect_text_with_model_extends_and_validates():
    sents = build([TALK * 4, AD, TALK * 4])
    a0 = len(TALK) * 4
    seen = {}

    def chat(cfg, system, user):
        seen["user"] = user
        lines = [l for l in user.splitlines() if l.startswith("[")]
        first_ad = next(i for i, l in enumerate(lines, 1) if "при поддержке" in l)
        return {"ads": [{"start": first_ad, "end": first_ad + 4, "brand": "Доставка"}]}   # модель недобрала последнюю фразу
    r = adcut.detect_text(sents, None, cfgx(), chat=chat, title="Собаки")
    assert len(r) == 1 and r[0].source == "text" and r[0].note == "Доставка"
    assert r[0].start <= sents[a0].start and r[0].start > sents[a0 - 1].end - 0.01
    assert r[0].end >= sents[a0 + len(AD) - 1].end                 # хвост «ссылка под видео» дотянут по маркерам
    assert "Название видео: Собаки" in seen["user"]
    # модель назвала рекламой обычный текст без признаков
    def bad(cfg, system, user):
        lines = [l for l in user.splitlines() if l.startswith("[")]
        i = next(i for i, l in enumerate(lines, 1) if "Первое правило" in l)
        return {"ads": [{"start": i, "end": i + 2}]}
    assert adcut.detect_text(sents, None, cfgx(), chat=bad) == []
    # слишком короткая вставка
    short = lambda cfg, s, u: {"ads": [{"start": 1, "end": 1}]}
    assert adcut.detect_text(build([TALK, AD[3:5], TALK]), None, cfgx(min_seconds=30), chat=short) == []
    # фразы вне окна, мусор в ответе
    assert adcut.detect_text(sents, None, cfgx(), chat=lambda c, s, u: {"ads": [{"start": 900, "end": 901}, {"x": 1}]}) == []


def test_detect_text_llm_down_cuts_nothing():
    def down(cfg, system, user):
        raise LLMError("Ollama выключена")
    logs = []
    assert adcut.detect_text(build([TALK * 4, AD, TALK * 4]), None, cfgx(), logs.append, chat=down) == []
    assert any("Ollama" in l for l in logs)


class Resp:
    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def json(self):
        return self._d


def test_sponsorblock_parsing(monkeypatch):
    import hashlib
    vid = "dQw4w9WgXcQ"
    calls = []

    def get(url, params=None, timeout=0):
        calls.append((url, params))
        return Resp([{"videoID": "AAAAAAAAAAA", "segments": [{"segment": [1, 5], "category": "sponsor"}]},
                     {"videoID": vid, "segments": [{"segment": [10, 40], "category": "sponsor", "videoDuration": 600},
                                                   {"segment": [100, 110], "category": "selfpromo", "videoDuration": 999},
                                                   {"segment": [200, 200.5], "category": "sponsor"}]}])
    monkeypatch.setattr(adcut.requests, "get", get)
    r = adcut.fetch_sponsorblock(f"https://www.youtube.com/watch?v={vid}&t=5", 601.0, cfgx())
    assert [(x.start, x.end, x.note) for x in r] == [(10.0, 40.0, "sponsor")]   # чужое видео, другая длина и «слишком короткий» отброшены
    assert calls[0][0].endswith("/api/skipSegments/" + hashlib.sha256(vid.encode()).hexdigest()[:4])
    assert "sponsor" in calls[0][1]["categories"]
    assert adcut.fetch_sponsorblock("https://vimeo.com/123", 10, cfgx()) == []
    monkeypatch.setattr(adcut.requests, "get", lambda *a, **k: Resp([], 404))
    assert adcut.fetch_sponsorblock(f"https://youtu.be/{vid}", 10, cfgx()) == []

    def boom(*a, **k):
        raise requests.ConnectionError("нет сети")
    monkeypatch.setattr(adcut.requests, "get", boom)
    logs = []
    assert adcut.fetch_sponsorblock(f"https://youtu.be/{vid}", 10, cfgx(), logs.append) == []
    assert any("недоступен" in l for l in logs)
    assert adcut.youtube_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == vid
    assert adcut.youtube_id("https://example.com/watch?v=dQw4w9WgXcQ") is None


def test_merge_keep_and_remove_ranges():
    m = adcut.merge_ranges([Range(10, 20, "text", "A"), Range(21, 30, "sponsorblock", "sponsor"), Range(50, 60, "text")])
    assert [(r.start, r.end) for r in m] == [(10, 30), (50, 60)] and m[0].source == "text+sponsorblock" and "A" in m[0].note
    assert adcut.keep_intervals(m, 100) == [(0, 10), (30, 50), (60, 100)]
    sents = build([TALK, AD, TALK])        # фразы по 5 с: реклама 30–60
    out = adcut.remove_ranges(sents, [Range(29.9, 59.9)])
    assert len(out) == 12 and all("доставк" not in s.text for s in out)
    assert out[6].start == pytest.approx(sents[12].start - 30.0, abs=0.01)   # после вставки время сдвинулось на её длину
    assert out[5].end == pytest.approx(sents[5].end)                          # до вставки — без изменений
    # диапазон посреди фразы: уцелевшие слова остаются, текст пересобирается
    part = adcut.remove_ranges([Segment(0, 5, "а б в г", [Word(0, 1, "а"), Word(1, 2, "б"), Word(2, 3, "в"), Word(3, 4, "г")])], [Range(1.9, 3.1)])
    assert part[0].text == "а б г" and part[0].words[2].start == pytest.approx(1.9)


def _video(p: Path, dur=40, audio=True):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", "-c:a", "aac"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True)


def _dur(p) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout)


def test_cut_video_removes_ranges(tmp_path):
    v = tmp_path / "v.mp4"; _video(v, 40)
    out = adcut.cut_video(v, adcut.keep_intervals([Range(10, 20), Range(30, 35)], 40), tmp_path / "o.mp4", True)
    assert _dur(out) == pytest.approx(25, abs=0.5)
    assert subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                          capture_output=True, text=True).stdout.strip() == "audio"
    v2 = tmp_path / "n.mp4"; _video(v2, 20, audio=False)
    assert _dur(adcut.cut_video(v2, [(0, 5), (10, 20)], tmp_path / "o2.mp4", False)) == pytest.approx(15, abs=0.5)


def test_run_combines_layers_and_guards(tmp_path, monkeypatch):
    cfg = cfgx(); cfg["paths"]["workdir"] = tmp_path / "wd"; (tmp_path / "wd").mkdir()
    sents = build([TALK * 2, AD, TALK * 2])           # 12+6+12 фраз по 5 с = 150 с; реклама 60–90 с
    v = tmp_path / "wd" / "downloads" / "Видео.mp4"; v.parent.mkdir(); _video(v, 150)
    monkeypatch.setattr(adcut, "find_cut", lambda *a, **k: None)

    def chat(cfg_, system, user):
        lines = [l for l in user.splitlines() if l.startswith("[")]
        i = next(i for i, l in enumerate(lines, 1) if "при поддержке" in l)
        return {"ads": [{"start": i, "end": i + 5, "brand": "Доставка"}]}
    monkeypatch.setattr(adcut, "chat_json", chat)
    monkeypatch.setattr(adcut.requests, "get", lambda *a, **k: Resp([{"videoID": "dQw4w9WgXcQ", "segments": [{"segment": [100, 104], "category": "selfpromo"}]}]))
    logs = []
    out, segs = adcut.run(v, sents, "https://youtu.be/dQw4w9WgXcQ", cfg, logs.append, title="Собаки")
    assert out.name == "Видео.noads.mp4" and out.parent == v.parent
    cut = 150 - _dur(out)
    assert 30 < cut < 40                                # вставка 30 с + selfpromo 4 с
    assert all("промокод" not in s.text.lower() for s in segs)
    assert any("Вырезаю рекламу" in l for l in logs)
    # обе галочки выключены — ничего не происходит
    assert adcut.run(v, sents, "x", cfg, sb=False, text=False) == (v, sents)
    # предохранитель по доле
    big = cfgx(max_share=0.1); big["paths"]["workdir"] = cfg["paths"]["workdir"]
    logs2 = []
    out2, segs2 = adcut.run(v, sents, "x", big, logs2.append, sb=False, text=True)
    assert out2 == v and segs2 is sents and any("ничего не режу" in l for l in logs2)
    # рекламы нет
    out3, _ = adcut.run(v, build([TALK * 5]), "x", cfg, sb=False, text=True)
    assert out3 == v


def test_pipeline_uses_ad_free_video(tmp_path, monkeypatch):
    from narez import pipeline
    from narez.db import DB
    cfg = cfgx(); cfg["paths"]["workdir"] = tmp_path / "wd"; (tmp_path / "wd").mkdir()
    cfg["paths"]["db"] = tmp_path / "t.db"; cfg["_root"] = str(tmp_path)
    cfg["subtitles"] = {**cfg["subtitles"], "enabled": False}
    v = tmp_path / "wd" / "downloads" / "Видео.mp4"; v.parent.mkdir(); _video(v, 150)
    sents = build([TALK * 2, AD, TALK * 2])
    monkeypatch.setattr(pipeline, "download", lambda *a, **k: (v, "Видео"))
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: sents)
    monkeypatch.setattr(pipeline, "to_sentences", lambda x: x)
    monkeypatch.setattr(adcut, "find_cut", lambda *a, **k: None)
    monkeypatch.setattr(adcut, "chat_json", lambda cfg_, s, u: {"ads": [{"start": next(i for i, l in enumerate([l for l in u.splitlines() if l.startswith("[")], 1) if "при поддержке" in l),
                                                                         "end": next(i for i, l in enumerate([l for l in u.splitlines() if l.startswith("[")], 1) if "при поддержке" in l) + 5}]})
    got = {}

    def fake_moments(segments, cfg_, log):
        got["segments"] = segments
        from narez.models import Moment
        return [Moment(5, 40, "Клип", "", 5)]
    monkeypatch.setattr(pipeline, "find_moments", fake_moments)
    monkeypatch.setattr(pipeline, "find_cut", lambda *a, **k: None)

    def fake_render(video, a, b, out, ass, cfg_):
        got["video"] = video
        out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"x")
        return out
    monkeypatch.setattr(pipeline, "render_clip", fake_render)
    monkeypatch.setattr(pipeline, "make_metadata", lambda m, *a, **k: (m.title, m.reason))
    db = DB(cfg["paths"]["db"]); g = db.add_group("g")
    job_id, clips = pipeline.run("https://x.y/v", cfg, db, g, lambda *_: None, adcut_sb=False, adcut_text=True)
    assert got["video"].name == "Видео.noads.mp4"
    assert all("промокод" not in s.text.lower() for s in got["segments"]) and len(got["segments"]) == 24
    assert db.job(job_id)["source_path"] == str(v)                       # исходником остаётся скачанный файл
    # без галочек — исходное видео и исходный транскрипт
    got.clear()
    pipeline.run("https://x.y/v", cfg, db, g, lambda *_: None, adcut_sb=False, adcut_text=False)
    assert got["video"] == v and len(got["segments"]) == 30
