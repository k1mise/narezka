"""Поиск интересных моментов: LLM предлагает, код проверяет и подгоняет под 30–60 секунд."""
from __future__ import annotations

import re

from .llm import chat_json
from .models import Moment, Segment

SYSTEM = """Ты — редактор вирусных коротких видео (Shorts/TikTok/Reels).
Тебе дают транскрипт длинного видео с таймкодами в секундах вида [123.4] текст.
Найди самые интересные, цепляющие, законченные по смыслу фрагменты: эмоция, неожиданный факт,
шутка, конфликт, полезный совет, история с развязкой.
ГЛАВНОЕ ПРАВИЛО: фрагмент должен начинаться с сильной фразы-крючка (интригующий вопрос, смелое
утверждение, неожиданный факт) — сразу с сути. НЕ начинай с подводок и воды вроде «ну так вот»,
«итак», «короче», «в общем», приветствий и пояснений контекста. Фрагмент должен быть понятен без
остального видео и заканчиваться законченной мыслью по той же теме, а не на полуслове и не на переходе к другой теме.
Длительность каждого фрагмента — от {min} до {max} секунд, целься примерно в {target} секунд.
Ответь ТОЛЬКО JSON вида:
{{"moments":[{{"start":123.4,"end":170.0,"title":"короткий заголовок","reason":"почему интересно","score":8.5}}]}}
score — от 1 до 10. Таймкоды бери из транскрипта. Не больше {n} фрагментов."""

REFINE = """Ты — редактор коротких видео. Тебе дают пронумерованные фразы вокруг выбранного фрагмента.
Выбери, с какой фразы НАЧАТЬ клип и какой ЗАКОНЧИТЬ, чтобы:
1) первая фраза сразу цепляла зрителя (вопрос, сильное утверждение, интрига) и была понятна без контекста —
   пропусти подводки, воду, слова-паразиты в начале;
2) последняя фраза завершала мысль (развязка, вывод, панчлайн);
3) последняя фраза относилась к ТОЙ ЖЕ теме, что и начало: если дальше идёт переход к другой теме — закончи
   раньше, даже если клип получится короче;
4) общая длительность была от {min} до {max} секунд (лучше около {target}).
Ответь ТОЛЬКО JSON: {{"start":номер_первой_фразы,"end":номер_последней_фразы,"hook":"первая фраза"}}"""


def _chunks(segments: list[Segment], chunk_seconds: float):
    cur, t0 = [], None
    for s in segments:
        if t0 is None:
            t0 = s.start
        if s.end - t0 > chunk_seconds and cur:
            yield cur
            cur, t0 = [], s.start
        cur.append(s)
    if cur:
        yield cur


def _format(chunk: list[Segment]) -> str:
    return "\n".join(f"[{s.start:.1f}] {s.text}" for s in chunk)


def find_moments(segments: list[Segment], cfg: dict, log=print) -> list[Moment]:
    lo, hi = cfg["clips"]["min_seconds"], cfg["clips"]["max_seconds"]
    c = cfg["llm"]
    found: list[Moment] = []
    chunks = list(_chunks(segments, c["chunk_minutes"] * 60))
    for i, chunk in enumerate(chunks, 1):
        log(f"LLM анализирует часть {i}/{len(chunks)}...")
        data = chat_json(
            cfg,
            SYSTEM.format(min=lo, max=hi, n=c["moments_per_chunk"], target=cfg["clips"].get("target_seconds", 45)),
            _format(chunk),
        )
        items = data.get("moments", []) if isinstance(data, dict) else data
        for it in items:
            try:
                found.append(Moment(float(it["start"]), float(it["end"]),
                                    str(it.get("title", "")).strip(),
                                    str(it.get("reason", "")).strip(),
                                    float(it.get("score", 5))))
            except (KeyError, TypeError, ValueError):
                continue
    best = select_moments(found, segments, lo, hi, cfg["clips"]["max_clips"])
    if c.get("refine", True):
        refined = []
        for i, m in enumerate(best, 1):
            log(f"Уточняю начало и конец клипа {i}/{len(best)}...")
            try:
                refined.append(refine_moment(m, segments, cfg))
            except Exception as e:  # LLM ошибся — оставляем как было
                log(f"  не удалось уточнить ({e}), оставляю как есть")
                refined.append(m)
        best = select_moments(refined, segments, lo, hi, cfg["clips"]["max_clips"])
    return best


def snap(m: Moment, segments: list[Segment], lo: float, hi: float) -> Moment | None:
    """Подгоняет границы под начало/конец фраз и укладывает в [lo, hi]."""
    if not segments:
        return None
    start_seg = min(segments, key=lambda s: abs(s.start - m.start))
    start = start_seg.start
    # берём фразы подряд, пока не набрали минимум; останавливаемся у ближайшего конца фразы к m.end
    ends = [s.end for s in segments if s.end > start]
    if not ends:
        return None
    valid = [e for e in ends if lo <= e - start <= hi]
    if valid:
        end = min(valid, key=lambda e: abs(e - m.end))
    else:
        under = [e for e in ends if e - start <= hi]
        if not under:
            end = start + hi
        else:
            end = max(under)
            if end - start < lo:
                end = min(start + lo, ends[-1])
    if end - start < lo * 0.7:  # хвост видео слишком короткий
        return None
    return Moment(start, end, m.title, m.reason, m.score)


def select_moments(found, segments, lo, hi, max_clips) -> list[Moment]:
    snapped = [x for x in (snap(m, segments, lo, hi) for m in found) if x]
    snapped.sort(key=lambda m: m.score, reverse=True)
    chosen: list[Moment] = []
    for m in snapped:
        if all(m.end <= c.start or m.start >= c.end for c in chosen):  # без пересечений
            chosen.append(m)
        if len(chosen) >= max_clips:
            break
    return sorted(chosen, key=lambda m: m.start)


def refine_moment(m: Moment, segments: list[Segment], cfg: dict) -> Moment:
    """Второй проход: модель выбирает по номерам фраз, с какой начать, чтобы в начале был крючок."""
    lo, hi = cfg["clips"]["min_seconds"], cfg["clips"]["max_seconds"]
    target = cfg["clips"].get("target_seconds", 45)
    window = [s for s in segments if s.end >= m.start - 25 and s.start <= m.end + 15]
    if len(window) < 2:
        return m
    text = "\n".join(f"{i}: {s.text}" for i, s in enumerate(window))
    data = chat_json(cfg, REFINE.format(min=lo, max=hi, target=target), text)
    i, j = int(data["start"]), int(data["end"])
    if not (0 <= i <= j < len(window)):
        return m
    cand = Moment(window[i].start, window[j].end, m.title, m.reason, m.score)
    return snap(cand, segments, lo, hi) or m


META = """Ты — автор подписей для YouTube Shorts. Тебе дают текст короткого видео (расшифровка речи).
Придумай: 1) title — цепляющий заголовок до 70 символов, без кавычек и без эмодзи; 2) description — 1–2 коротких
предложения о чём ролик, без воды и без выдуманных фактов; 3) hashtags — 3–5 хэштегов по теме, на языке видео,
без пробелов внутри. Опирайся только на текст ролика.
Заголовок придумай сам, коротко и по сути именно этого фрагмента. Нигде (ни в заголовке, ни в описании, ни в хэштегах)
не упоминай автора, блогера, канал или название исходного видео, не пиши «источник», «автор», ссылки и @-имена.
Ответь ТОЛЬКО JSON: {{"title":"...","description":"...","hashtags":["#тег1","#тег2"]}}"""


_CREDIT = re.compile(r"^\s*(источник|автор|канал|credits?|source|via)\b.*$", re.I | re.M)


def scrub(text: str) -> str:
    """Убирает из заголовка/описания ссылки, @-имена и строки вида «Источник: …» / «Автор: …»."""
    t = _CREDIT.sub("", text or "")
    t = re.sub(r"https?://\S+|\b(?:www\.)?(?:youtu\.be|youtube\.com)/\S+|@[\w.\-]+", "", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def make_metadata(m: Moment, sentences: list[Segment], cfg: dict, source_title: str = "", source_url: str = "") -> tuple[str, str]:
    """Заголовок и описание для публикации: модель придумывает их по тексту самого клипа (название исходного видео ей не дают),
    упоминаний автора и источника нет. При любой ошибке остаются исходные title и reason (тоже очищенные)."""
    try:
        text = " ".join(s.text for s in sentences if s.start >= m.start - 0.05 and s.end <= m.end + 0.5)
        if not text.strip():
            raise ValueError("нет текста клипа")
        data = chat_json(cfg, META, f"Текст ролика: {text[:3500]}")
        title = scrub(str(data.get("title", "")).strip().strip('"«»'))[:90] or scrub(m.title)
        desc = scrub(str(data.get("description", "")).strip())
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in data.get("hashtags", [])[:5]
                        if isinstance(t, str) and t.strip() and " " not in t.strip())
        parts = [p for p in (desc, tags) if p]
        return title, "\n\n".join(parts)
    except Exception:
        return scrub(m.title), scrub(m.reason)
