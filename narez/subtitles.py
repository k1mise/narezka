"""Субтитры в формате ASS (стиль «как в шортсах»: крупно, с обводкой) + опциональный перевод."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .llm import chat_json, chat_text
from .models import Segment, Word


def _ts(t: float) -> str:
    t = max(t, 0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def clip_words(segments: list[Segment], start: float, end: float) -> list[Word]:
    """Слова внутри клипа, время пересчитано относительно начала клипа."""
    out = []
    for s in segments:
        for w in s.words:
            if w.start >= start - 0.05 and w.end <= end + 0.05:
                out.append(Word(max(w.start - start, 0), min(w.end, end) - start, w.text))
    return out


def clip_segments(segments: list[Segment], start: float, end: float) -> list[Segment]:
    out = []
    for s in segments:
        if s.start >= start - 0.05 and s.end <= end + 0.05:
            out.append(Segment(max(s.start - start, 0), min(s.end, end) - start, s.text))
    return out


def group_words(words: list[Word], per_line: int) -> list[tuple[float, float, str]]:
    lines, buf = [], []
    for w in words:
        buf.append(w)
        long_pause = len(buf) > 1 and w.start - buf[-2].end > 0.6
        if len(buf) >= per_line or w.text.endswith((".", "?", "!")) or long_pause:
            lines.append(buf)
            buf = []
    if buf:
        lines.append(buf)
    res = []
    for i, ln in enumerate(lines):
        start = ln[0].start
        end = ln[-1].end
        if i + 1 < len(lines):  # не оставляем «дыр» между строками
            end = min(max(end, start + 0.4), lines[i + 1][0].start)
        res.append((start, end, " ".join(w.text for w in ln)))
    return res


def translate_segments(segs: list[Segment], target: str, cfg: dict) -> list[Segment]:
    """Переводит каждую фразу через LLM; слова внутри фразы распределяются по времени равномерно."""
    result = []
    for s in segs:
        text = chat_text(
            cfg,
            f"Ты переводчик субтитров. Переведи на язык: {target}. Верни только перевод, без пояснений, "
            "коротко и естественно, как в разговорной речи.",
            s.text,
        )
        words = text.split()
        if not words:
            continue
        step = (s.end - s.start) / len(words)
        result.append(Segment(s.start, s.end, text,
                              [Word(s.start + i * step, s.start + (i + 1) * step, w) for i, w in enumerate(words)]))
    return result


def _norm(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower().replace("ё", "е"), flags=re.U)


def _accept(old: str, new: str, vocab: set | None = None, min_ratio: float = 0.6) -> str:
    """Принимает правку слова только если это похоже на исправление ошибки распознавания:
    не просто смена регистра/«ё»/знаков и не подмена слова синонимом (низкое сходство букв)."""
    o, n = _norm(old), _norm(new)
    if not n:
        return old
    tail = re.search(r"[^\w]+$", old, flags=re.U)
    core = re.sub(r"[^\w\-]+$", "", new, flags=re.U)
    keep_tail = tail.group(0) if tail else ""
    if o == n:  # то же слово: регистр/ё/пунктуацию не трогаем, кроме имён из словаря («тревор» → «Тревор»)
        return core + keep_tail if vocab and n in vocab and core[:1].isupper() else old
    if min(len(o), len(n)) < 4:
        return old  # короткие слова слишком рискованно править
    if SequenceMatcher(None, o, n).ratio() < min_ratio:
        return old  # слишком другое слово — скорее переформулировка, чем исправление
    return core + keep_tail


def fix_segments(segs: list[Segment], cfg: dict, topic: str, log=print, batch: int = 12) -> list[Segment]:
    """Исправляет ошибки распознавания по смыслу (слова не по теме, неверные окончания).
    Одним запросом отправляется до `batch` предложений (так в разы быстрее, чем по одному).
    Безопасно: правка слова принимается только если число слов в предложении совпало и слово похоже по буквам."""
    vocab = cfg["whisper"].get("vocabulary", "")
    system = (
        "Ты корректор субтитров. Предложения получены автоматическим распознаванием речи и могут содержать "
        "ошибки: слова, похожие по звучанию, но неверные по смыслу, неправильные окончания, слитные или "
        "разделённые слова. Исправь ТОЛЬКО явные ошибки, учитывая тему видео. Не перефразируй, не добавляй и не "
        "убирай слова: количество слов в каждом предложении должно остаться тем же.\n"
        'Ответь ТОЛЬКО JSON: {"fixed": ["предложение 0", "предложение 1", ...]} — ровно столько элементов, '
        "сколько предложений, в том же порядке."
        f"\nТема видео: {topic}.\nВажные термины: {vocab}."
    )
    vocab_set = {_norm(x) for x in re.split(r"[,\s]+", vocab) if x}
    out: list[Segment] = []
    for i in range(0, len(segs), batch):
        part = segs[i:i + batch]
        try:
            data = chat_json(cfg, system, "\n".join(f"{k}: {x.text}" for k, x in enumerate(part)))
            fixed = data.get("fixed") if isinstance(data, dict) else None
            if not isinstance(fixed, list) or len(fixed) != len(part):
                raise ValueError("неверный формат ответа")
        except Exception:
            out.extend(part)  # не получилось — оставляем как есть
            continue
        for s, ans in zip(part, fixed):
            toks = str(ans).split()
            if len(toks) != len(s.words):
                out.append(s)
                continue
            words = []
            for t, w in zip(toks, s.words):
                nt = _accept(w.text, t, vocab_set)
                if nt != w.text:
                    log(f"  субтитры: «{w.text}» → «{nt}»")
                words.append(Word(w.start, w.end, nt))
            out.append(Segment(s.start, s.end, " ".join(x.text for x in words), words))
    return out


def build_ass(lines: list[tuple[float, float, str]], cfg: dict) -> str:
    sc, cl = cfg["subtitles"], cfg["clips"]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {cl['width']}
PlayResY: {cl['height']}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{sc['font']},{sc['font_size']},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,{sc['margin_bottom']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = [f"Dialogue: 0,{_ts(a)},{_ts(b)},Default,,0,0,0,,{_escape(t)}"
          for a, b, t in lines]
    return header + "\n".join(ev) + "\n"


def make_ass(segments: list[Segment], start: float, end: float, cfg: dict, log=print, topic: str = "") -> str:
    sc = cfg["subtitles"]
    target = sc["translate_to"].strip()
    if sc.get("llm_fix", True):
        inside = [x for x in segments if x.end >= start and x.start <= end]
        fixed = dict(zip(map(id, inside), fix_segments(inside, cfg, topic, log)))
        segments = [fixed.get(id(x), x) for x in segments]
    if target:
        log(f"Перевожу субтитры на: {target}")
        words = []
        for s in translate_segments(clip_segments(segments, start, end), target, cfg):
            words.extend(s.words)
    else:
        words = clip_words(segments, start, end)
    return build_ass(group_words(words, sc["words_per_line"]), cfg)
