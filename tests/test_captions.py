"""Субтитры YouTube вместо Whisper: разбор json3, выбор ручных/автоматических, загрузка, выбор источника речи в пайплайне."""
import json

import pytest

from narez import captions, pipeline
from narez.config import load_config


def auto_json3(n=60):
    """Автосубтитры: события со словами и смещениями; одно слово повторено в соседнем событии."""
    ev, t = [], 0
    for i in range(n // 3):
        segs = [{"utf8": f"слово{i}a", "tOffsetMs": 0}, {"utf8": f" слово{i}b", "tOffsetMs": 400}, {"utf8": f" слово{i}c", "tOffsetMs": 800}]
        ev.append({"tStartMs": t, "dDurationMs": 1500, "segs": segs})
        t += 1500
    ev.insert(1, {"tStartMs": 0, "dDurationMs": 100, "segs": [{"utf8": "слово0a", "tOffsetMs": 0}]})   # дубль
    ev.append({"tStartMs": t, "dDurationMs": 100, "aAppend": 1, "segs": [{"utf8": "\n"}]})            # пустое событие
    return {"events": ev}


def test_parse_auto_and_manual():
    segs = captions.parse_json3(auto_json3(6))
    assert [s.text for s in segs] == ["слово0a", "слово0b", "слово0c", "слово1a", "слово1b", "слово1c"]   # дубль и пустое событие убраны
    assert segs[1].start == pytest.approx(0.4) and segs[0].end <= segs[1].start + 1e-6
    manual = captions.parse_json3({"events": [{"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "Привет, как дела у вас"}]}]})
    assert [s.text for s in manual] == ["Привет,", "как", "дела", "у", "вас"]
    assert manual[0].start == pytest.approx(1.0) and manual[-1].end == pytest.approx(3.0) and manual[0].end <= manual[1].start + 1e-6


def info(**kw):
    e = lambda u: [{"ext": "vtt", "url": u + "&fmt=vtt"}, {"ext": "json3", "url": u}]
    base = {"subtitles": {"ru": e("https://x/m?lang=ru")}, "automatic_captions": {
        "ru": e("https://x/a?lang=ru"), "ru-orig": e("https://x/ao?lang=ru"), "en": e("https://x/a?lang=ru&tlang=en")}}
    base.update(kw)
    return base


def test_pick_rules():
    assert captions.pick(info(), "ru", True)[0] == "manual"                      # ручные важнее
    no_manual = info(subtitles={})
    assert captions.pick(no_manual, "ru", False) is None                          # автоматические выключены
    kind, key, e = captions.pick(no_manual, "ru", True)
    assert kind == "auto" and key == "ru-orig"                                    # «оригинальные» автосубтитры
    assert captions.pick(no_manual, "en", True) is None                           # переведённые не берём
    assert captions.pick(info(subtitles={"ru-RU": [{"ext": "json3", "url": "u"}]}), "ru", False)[1] == "ru-RU"
    assert captions.pick(info(), "", True) is None
    assert captions.wanted_lang({"language": "en"}, True, None) == "ru"           # русский дубляж → русские субтитры
    assert captions.wanted_lang({"language": "en-US"}, False, None) == "en"
    assert captions.wanted_lang({}, False, {"whisper": {"language": "ru"}}) == "ru"


class FakeYDL:
    def __init__(self, payload):
        self.payload, self.urls = payload, []

    def urlopen(self, url):
        self.urls.append(url)
        if isinstance(self.payload, Exception):
            raise self.payload
        return type("R", (), {"read": lambda s: json.dumps(self.payload).encode()})()


def test_fetch_and_load(tmp_path):
    v = tmp_path / "Видео.mp4"
    v.write_bytes(b"x")
    ydl = FakeYDL(auto_json3(60))
    assert captions.fetch(ydl, info(subtitles={}), v, "ru", False) is None and not captions.sidecar(v).exists()   # авто не разрешены
    assert captions.fetch(ydl, info(subtitles={}), v, "ru", True) == "auto" and ydl.urls == ["https://x/ao?lang=ru"]
    segs, kind, lang = captions.load(v, True)
    assert kind == "auto" and lang == "ru-orig" and len(segs) == 60
    assert captions.load(v, False) is None                                         # авто выключены в настройке — не берём
    assert captions.load(v, True, duration=10_000) is None                         # покрывают меньше половины видео — не берём
    assert captions.fetch(FakeYDL(auto_json3(60)), info(), v, "ru", False) == "manual"
    assert captions.load(v, False)[1] == "manual"                                  # ручные берутся всегда
    logs = []
    assert captions.fetch(FakeYDL(RuntimeError("429")), info(), v, "ru", True, logs.append) is None and logs
    assert captions.load(v, True) is None                                          # старая загрузка стёрта, нет файла
    assert captions.load(tmp_path / "нет.mp4", True) is None


def test_speech_prefers_captions_then_whisper(tmp_path, monkeypatch):
    cfg = load_config()
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    captions.save(v, "auto", "ru-orig", auto_json3(60))
    monkeypatch.setattr(pipeline, "probe", lambda p: {"duration": 40.0})
    calls = []
    from narez.models import Segment, Word
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: calls.append(1) or [Segment(0, 1, "w", [Word(0, 1, "w")])])
    logs = []
    s = pipeline.speech(v, "t", cfg, logs.append, "youtube")
    assert s and not calls and any("автоматические субтитры YouTube" in x for x in logs)
    pipeline.speech(v, "t", cfg, logs.append, "whisper")                           # автоматические запрещены → Whisper
    assert calls == [1]
    captions.save(v, "manual", "ru", auto_json3(60))
    calls.clear()
    pipeline.speech(v, "t", cfg, logs.append, "whisper")                           # ручные берутся, даже если выбран Whisper
    assert not calls and any("ручные" in x for x in logs)
