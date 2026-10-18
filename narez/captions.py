"""Субтитры самого YouTube-видео вместо распознавания речи Whisper.

Ручные субтитры (загрузил автор или зрители) берутся всегда, когда они есть на языке звука. Автоматические субтитры YouTube — только если
так выбрано при скачивании («Автоматические YouTube»); иначе речь распознаёт Whisper. Субтитры скачиваются вместе с видео в файл
«<имя видео>.captions.json» рядом с ним, поэтому работают и при повторной нарезке из папки «Исходники»."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Segment, Word


def sidecar(video: Path) -> Path:
    return video.with_name(video.stem + ".captions.json")


def wanted_lang(info: dict, dubbed_ru: bool, cfg: dict | None) -> str:
    """Язык субтитров должен совпадать с языком скачанного звука: при русском дубляже — русский, иначе язык оригинала."""
    if dubbed_ru:
        return "ru"
    lang = str(info.get("language") or (cfg or {}).get("whisper", {}).get("language") or "").lower()
    return lang.split("-")[0]


def _json3(entries) -> dict | None:
    return next((e for e in entries or [] if e.get("ext") == "json3" and e.get("url")), None)


def pick(info: dict, lang: str, allow_auto: bool) -> tuple[str, str, dict] | None:
    """(вид 'manual'|'auto', язык, запись формата json3) или None. Переведённые автосубтитры (tlang=) не берём."""
    if not lang:
        return None
    for key, entries in (info.get("subtitles") or {}).items():
        if key.lower() == lang or key.lower().startswith(lang + "-"):
            e = _json3(entries)
            if e:
                return "manual", key, e
    if allow_auto:
        auto = info.get("automatic_captions") or {}
        for key in (f"{lang}-orig", lang):
            e = _json3(auto.get(key))
            if e and "tlang=" not in e["url"]:
                return "auto", key, e
    return None


def parse_json3(data: dict) -> list[Segment]:
    """Формат json3: события со словами и смещениями (автосубтитры) или целыми строками (ручные — слова распределяем по времени)."""
    words_all: list[Word] = []
    segs: list[Segment] = []
    for ev in sorted(data.get("events") or [], key=lambda e: e.get("tStartMs", 0)):
        parts = ev.get("segs")
        if not parts:
            continue
        t0 = ev.get("tStartMs", 0) / 1000.0
        t1 = t0 + max(ev.get("dDurationMs", 0), 0) / 1000.0
        toks = []
        for p in parts:
            txt = re.sub(r"\s+", " ", str(p.get("utf8", ""))).strip()
            if txt:
                toks.append((t0 + p.get("tOffsetMs", 0) / 1000.0, txt, "tOffsetMs" in p))
        if not toks:
            continue
        timed = any(x[2] for x in toks) and len(toks) > 1
        words: list[Word] = []
        if timed:
            for i, (ws, txt, _) in enumerate(toks):
                nxt = toks[i + 1][0] if i + 1 < len(toks) else max(t1, ws + 0.4)
                words.append(Word(ws, max(min(nxt, ws + 1.5), ws + 0.05), txt))
        else:
            tokens = [w for _, txt, _ in toks for w in txt.split()]
            total = max(sum(len(w) for w in tokens), 1)
            dur = max(t1 - t0, 0.3 * len(tokens))
            cur = t0
            for w in tokens:
                d = dur * len(w) / total
                words.append(Word(cur, cur + d, w))
                cur += d
        for w in words:    # у «скользящих» автосубтитров слово иногда повторяется в соседнем событии
            if any(x.text == w.text and abs(x.start - w.start) < 0.3 for x in words_all[-8:]):
                continue
            words_all.append(w)
            segs.append(Segment(w.start, w.end, w.text, [w]))
    return [Segment(s.start, s.end, s.text, s.words) for s in segs]


def save(video: Path, kind: str, lang: str, raw: dict) -> None:
    sidecar(video).write_text(json.dumps({"kind": kind, "lang": lang, "data": raw}, ensure_ascii=False), encoding="utf-8")


def fetch(ydl, info: dict, video: Path, lang: str, allow_auto: bool, log=print) -> str | None:
    """Скачивает подходящие субтитры рядом с видео. Возвращает 'manual' / 'auto' или None (ошибки не критичны: останется Whisper)."""
    sidecar(video).unlink(missing_ok=True)
    got = pick(info, lang, allow_auto)
    if not got:
        return None
    kind, key, entry = got
    try:
        raw = json.loads(ydl.urlopen(entry["url"]).read().decode("utf-8"))
        if not parse_json3(raw):
            return None
        save(video, kind, key, raw)
        return kind
    except Exception as e:  # noqa: BLE001
        log(f"Субтитры YouTube скачать не удалось ({str(e)[:100]}) — речь распознает Whisper.")
        return None


def load(video: Path, allow_auto: bool, duration: float | None = None) -> tuple[list[Segment], str, str] | None:
    """(слова, вид, язык) из сохранённых субтитров или None. Автосубтитры без разрешения и слишком короткие (не покрывают видео) отбрасываются."""
    try:
        d = json.loads(sidecar(video).read_text(encoding="utf-8"))
        segs = parse_json3(d["data"])
    except (OSError, ValueError, KeyError):
        return None
    if d.get("kind") == "auto" and not allow_auto:
        return None
    if len(segs) < 20 or (duration and segs[-1].end < duration * 0.5):
        return None
    return segs, d.get("kind", "manual"), d.get("lang", "")
