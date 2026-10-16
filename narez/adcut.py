"""Вырезание рекламы из исходника до нарезки.

Два слоя, результаты объединяются:
1. SponsorBlock (только YouTube): готовая разметка спонсорских вставок от зрителей, запрос по ID видео.
2. Текст (основной для русскоязычных видео): в транскрипте Whisper ищутся рекламные маркеры («промокод», «ссылка в описании»,
   «скидка», «спонсор»…), вокруг подозрительных мест локальная LLM определяет границы вставки, границы подгоняются
   под паузы в речи.
Найденные диапазоны вырезаются в новый файл «<имя>.noads.mp4», а транскрипт пересчитывается под него — дальше пайплайн
работает как обычно, ничего не зная о рекламе. Предохранители: лимит доли вырезанного, минимальная/максимальная длина вставки,
для текстового слоя — обязательные маркеры внутри найденного диапазона и ответ LLM. Без LLM текстовый слой ничего не режет."""
from __future__ import annotations

import bisect
import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import folder
from .editor import find_cut
from .llm import LLMError, chat_json
from .models import Segment, Word

DEFAULTS = {
    "sponsorblock": True,                       # слой 1 (галочка на панели переопределяет)
    "text": True,                               # слой 2
    "sb_categories": ["sponsor", "selfpromo"],  # что вырезать по SponsorBlock (ещё: interaction, intro, outro, preview)
    "sb_api": "https://sponsor.ajay.app",
    "pad": 0.15,                                # запас по краям, если паузу в звуке найти не удалось, с
    "min_seconds": 6.0,                         # текстовый слой: вставка короче не считается рекламой
    "max_seconds": 240.0,                       # ... и длиннее тоже (подозрительно, скорее ошибка модели)
    "max_share": 0.3,                           # если «рекламой» оказалось больше этой доли видео — ничего не режем
    "context_seconds": 45.0,                    # сколько секунд контекста до/после подозрительного места видит LLM
    "threshold": 4,                             # порог «подозрительности» окна (сумма весов маркеров)
}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("adcut") or {})}


def flags(cfg: dict, sb: bool | None, text: bool | None) -> tuple[bool, bool]:
    s = settings(cfg)
    return (bool(s["sponsorblock"]) if sb is None else bool(sb)), (bool(s["text"]) if text is None else bool(text))


@dataclass
class Range:
    start: float
    end: float
    source: str = "text"        # sponsorblock / text / sponsorblock+text
    note: str = ""              # бренд или категория

    @property
    def duration(self) -> float:
        return self.end - self.start


def mmss(t: float) -> str:
    t = int(max(t, 0))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


# ---------------------------------------------------------------------------- слой 1: SponsorBlock

_YT_ID = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|embed/|live/|v/)|youtu\.be/)([\w-]{11})", re.I)


def youtube_id(url: str) -> str | None:
    m = _YT_ID.search(url or "")
    return m.group(1) if m else None


def fetch_sponsorblock(url: str, duration: float, cfg: dict, log=print) -> list[Range]:
    """Сегменты SponsorBlock для YouTube-ссылки. Любая сетевая ошибка — просто предупреждение: нарезка продолжается."""
    vid = youtube_id(url)
    if not vid:
        log("SponsorBlock: ссылка не на YouTube — пропускаю этот слой.")
        return []
    s = settings(cfg)
    prefix = hashlib.sha256(vid.encode()).hexdigest()[:4]  # API по префиксу хэша: сервер не узнаёт, какое именно видео смотрим
    try:
        r = requests.get(f"{s['sb_api']}/api/skipSegments/{prefix}", timeout=12,
                         params={"categories": json.dumps(list(s["sb_categories"])), "actionTypes": json.dumps(["skip"])})
    except requests.RequestException as e:
        log(f"SponsorBlock недоступен ({e.__class__.__name__}) — продолжаю без него.")
        return []
    if r.status_code == 404:
        log("SponsorBlock: для этого видео разметки нет.")
        return []
    if r.status_code != 200:
        log(f"SponsorBlock ответил HTTP {r.status_code} — продолжаю без него.")
        return []
    try:
        data = r.json()
    except ValueError:
        log("SponsorBlock вернул не JSON — продолжаю без него.")
        return []
    out: list[Range] = []
    for item in data if isinstance(data, list) else []:
        if item.get("videoID") != vid:
            continue
        for seg in item.get("segments", []):
            try:
                a, b = (float(x) for x in seg["segment"])
            except (KeyError, TypeError, ValueError):
                continue
            vd = float(seg.get("videoDuration") or 0)
            if vd and duration and abs(vd - duration) > 3:  # разметка от другой версии ролика (другая длина): таймкоды не подойдут
                log(f"SponsorBlock: разметка для видео длиной {vd:.0f} с, а у скачанного {duration:.0f} с — пропускаю сегмент.")
                continue
            if b - a >= 1:
                out.append(Range(a, b, "sponsorblock", str(seg.get("category", ""))))
    log(f"SponsorBlock: найдено сегментов — {len(out)}.")
    return out


# ---------------------------------------------------------------------------- слой 2: текст

# (регулярка, вес). Текст приводится к нижнему регистру, ё→е, пунктуация → пробелы.
STRONG = 3
MEDIUM = 1
MARKERS: list[tuple[re.Pattern, int]] = [(re.compile(p, re.U), w) for p, w in [
    (r"промо ?код\w*", STRONG),
    (r"(?:в|под) описани\w*", STRONG),
    (r"ссылк\w* (?:в|под|ниже|на сайт)", STRONG),
    (r"по ссылке", STRONG),
    (r"(?:перейд|переход|кликай|кликни|жми|нажим|нажми)\w*", MEDIUM),
    (r"спонсор\w*", STRONG),
    (r"при поддержке", STRONG),
    (r"в партнер(?:ств|ск)\w*", STRONG),
    (r"рекламн\w* (?:интеграци|вставк|пост|ролик|партнер|размещени)\w*", STRONG),
    (r"\berid\b", STRONG),
    (r"\bскидк\w*", STRONG),
    (r"\bкеш ?бе[кч]\w*|\bкэш ?бе[кч]\w*", STRONG),
    (r"(?:установ|скача)\w* (?:себе )?(?:приложени|прилож)\w*", STRONG),
    (r"на (?:первый|первую|свой первый) (?:заказ|покупк)\w*", STRONG),
    (r"бесплатн\w* (?:пробн|период|доставк|месяц|тестов)\w*", STRONG),
    (r"\bреклам\w*", MEDIUM),
    (r"\bпартнер\w*", MEDIUM),
    (r"\bсервис\w*", MEDIUM),
    (r"\bприложени\w*", MEDIUM),
    (r"\bсайт\w*", MEDIUM),
    (r"\bтариф\w*", MEDIUM),
    (r"\bбонус\w*", MEDIUM),
    (r"\bакци[яиюей]\b", MEDIUM),
    (r"\bдоставк\w*", MEDIUM),
    (r"\bзарегистр\w*|\bрегистрац\w*", MEDIUM),
    (r"\bподписк\w* на (?:сервис|приложени|платформ)\w*", MEDIUM),
    (r"\bбренд\w*", MEDIUM),
]]


def _norm(t: str) -> str:
    return re.sub(r"[^\w\s]", " ", t.lower().replace("ё", "е"))


def marker_score(text: str) -> tuple[int, int]:
    """(сумма весов, сколько сильных маркеров) для одной фразы. Каждый маркер учитывается один раз."""
    t = _norm(text)
    total = strong = 0
    for rx, w in MARKERS:
        if rx.search(t):
            total += w
            strong += w == STRONG
    return min(total, 9), strong


def candidate_windows(sents: list[Segment], cfg: dict, max_window: float = 360.0) -> list[tuple[int, int]]:
    """Окна (индексы первой и последней фразы), которые стоит показать LLM: места с кучей маркеров рядом + контекст."""
    if not sents:
        return []
    s = settings(cfg)
    sc = [marker_score(x.text) for x in sents]
    pre = [0]
    pre_strong = [0]
    for t, st in sc:
        pre.append(pre[-1] + t)
        pre_strong.append(pre_strong[-1] + st)
    starts = [x.start for x in sents]
    near = 15.0
    seeds: list[int] = []
    for i, x in enumerate(sents):
        if sc[i][0] == 0:
            continue
        lo = bisect.bisect_left(starts, x.start - near)
        hi = bisect.bisect_right(starts, x.end + near)
        total, strong = pre[hi] - pre[lo], pre_strong[hi] - pre_strong[lo]
        if total >= s["threshold"] and (strong >= 1 or total >= 6):
            seeds.append(i)
    if not seeds:
        return []
    zones: list[list[int]] = [[seeds[0], seeds[0]]]
    for i in seeds[1:]:
        if sents[i].start - sents[zones[-1][1]].end <= 60.0:
            zones[-1][1] = i
        else:
            zones.append([i, i])
    ctx = float(s["context_seconds"])
    out: list[tuple[int, int]] = []
    for a, b in zones:
        w0 = bisect.bisect_left(starts, sents[a].start - ctx)
        w1 = max(bisect.bisect_right(starts, sents[b].end + ctx) - 1, b)
        t0 = sents[w0].start
        while w0 <= w1:  # слишком длинное окно режем на куски с нахлёстом, чтобы влезло в контекст модели
            k = w0
            while k < w1 and sents[k + 1].end - t0 <= max_window:
                k += 1
            out.append((w0, k))
            if k >= w1:
                break
            w0 = max(bisect.bisect_left(starts, sents[k].start - ctx), w0 + 1)
            t0 = sents[w0].start
    return out


SYSTEM_AD = """Ты — редактор, который находит рекламные вставки в транскрипте русскоязычного видео.
Тебе дают пронумерованные фразы вида [номер | время] текст.
Реклама — фрагмент, где автор в коммерческих целях рассказывает о товаре, сервисе, приложении, магазине или бренде:
рекламная или спонсорская интеграция, промокод, скидка, призыв перейти по ссылке, установить, зарегистрироваться, купить.
Обычно у неё есть подводка («этот выпуск при поддержке…», «кстати, о…», «а теперь о нашем партнёре»), рассказ о продукте
и концовка с призывом (ссылка в описании, промокод), после чего автор возвращается к теме видео.
НЕ считай рекламой: обсуждение продуктов и компаний как части темы видео (обзор, новости, сравнение, критика); личное мнение
автора без призыва; призыв подписаться на канал или поставить лайк; шутки про рекламу.
Для каждой рекламной вставки укажи номер первой и последней фразы. Захвати вставку целиком — от подводки до последнего призыва,
— но не захватывай обычный контент до и после неё. Если рекламы нет, верни пустой список.
Ответь ТОЛЬКО JSON: {"ads":[{"start":номер,"end":номер,"brand":"название бренда","reason":"кратко, почему это реклама"}]}"""


def _extend(sents: list[Segment], sc: list[tuple[int, int]], i0: int, i1: int) -> tuple[int, int]:
    """Подхватывает вплотную идущие фразы с сильными маркерами (модель часто обрывает вставку на предпоследнем призыве)."""
    for _ in range(3):
        if i1 + 1 < len(sents) and sc[i1 + 1][1] >= 1 and sents[i1 + 1].start - sents[i1].end <= 2.5:
            i1 += 1
        else:
            break
    for _ in range(2):
        if i0 > 0 and sc[i0 - 1][1] >= 1 and sents[i0].start - sents[i0 - 1].end <= 2.5:
            i0 -= 1
        else:
            break
    return i0, i1


def _snap(video: Path | None, sents: list[Segment], i0: int, i1: int, pad: float) -> tuple[float, float]:
    """Границы вставки: внутри реальной паузы в звуке; нет паузы — небольшой запас, не заходя в соседние фразы."""
    a, b = sents[i0].start, sents[i1].end
    prv = sents[i0 - 1] if i0 > 0 else None
    nxt = sents[i1 + 1] if i1 + 1 < len(sents) else None
    st = en = None
    if video is not None:
        try:
            st, en = find_cut(video, a, "start"), find_cut(video, b, "end")
        except Exception:
            st = en = None
    if st is None:
        st = a - min(pad, max(a - prv.end, 0) * 0.5 if prv else pad)
    elif prv:
        st = max(st, min(prv.end + 0.03, a))
    if en is None:
        en = b + min(pad * 2, max(nxt.start - b, 0) * 0.6 if nxt else pad * 2)
    elif nxt:
        en = min(en, max(nxt.start - 0.03, b))
    return max(st, 0.0), max(en, st + 0.5)


def detect_text(sents: list[Segment], video: Path | None, cfg: dict, log=print, title: str = "",
                chat=None) -> list[Range]:
    chat = chat or chat_json
    s = settings(cfg)
    windows = candidate_windows(sents, cfg)
    if not windows:
        log("Реклама по тексту: подозрительных мест нет.")
        return []
    log(f"Реклама по тексту: подозрительных мест — {len(windows)}, проверяю моделью…")
    sc = [marker_score(x.text) for x in sents]
    out: list[Range] = []
    for n, (w0, w1) in enumerate(windows, 1):
        lines = "\n".join(f"[{k - w0 + 1} | {mmss(sents[k].start)}] {sents[k].text}" for k in range(w0, w1 + 1))
        try:
            data = chat(cfg, SYSTEM_AD, (f"Название видео: {title}\n\n" if title else "") + lines)
        except LLMError as e:
            log(f"Модель недоступна ({str(e).splitlines()[0]}) — без неё рекламу по тексту не режу.")
            return []
        except Exception as e:  # кривой ответ модели не должен ронять нарезку
            log(f"  окно {n}/{len(windows)}: не удалось разобрать ответ модели ({e}) — пропускаю.")
            continue
        ads = data.get("ads", []) if isinstance(data, dict) else data
        for it in ads if isinstance(ads, list) else []:
            try:
                i0, i1 = w0 + int(it["start"]) - 1, w0 + int(it["end"]) - 1
            except (KeyError, TypeError, ValueError):
                continue
            brand = str(it.get("brand", "")).strip()[:60]
            if not (w0 <= i0 <= i1 <= w1):
                log(f"  модель указала фразы вне окна ({it.get('start')}–{it.get('end')}) — пропускаю.")
                continue
            i0, i1 = _extend(sents, sc, i0, i1)
            a, b = _snap(video, sents, i0, i1, float(s["pad"]))
            dur = b - a
            if not float(s["min_seconds"]) <= dur <= float(s["max_seconds"]):
                log(f"  {mmss(a)}–{mmss(b)} ({dur:.0f} с): длина вне {s['min_seconds']:g}–{s['max_seconds']:g} с — не режу.")
                continue
            if sum(sc[k][0] for k in range(max(i0 - 1, 0), min(i1 + 2, len(sents)))) < 3:
                log(f"  {mmss(a)}–{mmss(b)}: модель нашла рекламу, но в тексте нет рекламных признаков — не режу.")
                continue
            out.append(Range(a, b, "text", brand))
    return out


# ---------------------------------------------------------------------------- объединение, вырезание, пересчёт

def merge_ranges(ranges: list[Range], gap: float = 2.0) -> list[Range]:
    out: list[Range] = []
    for r in sorted(ranges, key=lambda x: (x.start, x.end)):
        if out and r.start - out[-1].end <= gap:
            last = out[-1]
            src = last.source if r.source in last.source else f"{last.source}+{r.source}"
            note = last.note if (not r.note or r.note in last.note) else (f"{last.note}, {r.note}" if last.note else r.note)
            out[-1] = Range(last.start, max(last.end, r.end), src, note)
        else:
            out.append(Range(r.start, r.end, r.source, r.note))
    return out


def keep_intervals(ranges: list[Range], duration: float) -> list[tuple[float, float]]:
    keep, pos = [], 0.0
    for r in merge_ranges(ranges, 0.0):
        if r.start - pos > 0.05:
            keep.append((pos, r.start))
        pos = max(pos, r.end)
    if duration - pos > 0.05:
        keep.append((pos, duration))
    return keep


def remove_ranges(segments: list[Segment], ranges: list[Range]) -> list[Segment]:
    """Транскрипт без вырезанных диапазонов, таймкоды сдвинуты под новый файл. Слова внутри диапазона выбрасываются."""
    rm = merge_ranges(ranges, 0.0)

    def inside(t: float) -> bool:
        return any(r.start <= t < r.end for r in rm)

    def shift(t: float) -> float:
        return t - sum(min(r.end, t) - r.start for r in rm if r.start < t)

    out: list[Segment] = []
    for sg in segments:
        words = [Word(shift(w.start), shift(w.end), w.text) for w in sg.words if not inside((w.start + w.end) / 2)]
        if sg.words:
            if words:
                out.append(Segment(words[0].start, words[-1].end, " ".join(w.text for w in words), words))
        elif not inside((sg.start + sg.end) / 2):
            out.append(Segment(shift(sg.start), shift(sg.end), sg.text, []))
    return out


def cut_video(video: Path, keep: list[tuple[float, float]], out: Path, has_audio: bool) -> Path:
    """Склеивает оставленные куски в новый файл (одно перекодирование; дальше нарезка работает с ним как с обычным исходником)."""
    n = len(keep)
    parts = []
    for i, (a, b) in enumerate(keep):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
    ins = "".join(f"[v{i}][a{i}]" if has_audio else f"[v{i}]" for i in range(n))
    parts.append(f"{ins}concat=n={n}:v=1:a={1 if has_audio else 0}[vout]" + ("[aout]" if has_audio else ""))
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video.resolve()),
           "-filter_complex", ";".join(parts), "-map", "[vout]"]
    if has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-crf", "17", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out.resolve())]
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("Не удалось вырезать рекламу (ffmpeg): " + p.stderr[-600:])
    return out


def noads_path(video: Path, cfg: dict) -> Path:
    wd = Path(cfg["paths"]["workdir"]).resolve()
    inside = wd in video.resolve().parents
    return (video.parent if inside else folder(cfg, "sources")) / f"{video.stem}.noads.mp4"


def run(video: Path, segments: list[Segment], url: str, cfg: dict, log=print, sb: bool | None = None,
        text: bool | None = None, title: str = "") -> tuple[Path, list[Segment]]:
    """Ищет рекламу и вырезает её. Возвращает (видео без рекламы, транскрипт под него); ничего не нашли — исходные."""
    from .banner import probe

    use_sb, use_text = flags(cfg, sb, text)
    if not (use_sb or use_text):
        return video, segments
    s = settings(cfg)
    info = probe(video)
    dur = info["duration"]
    found: list[Range] = []
    if use_sb:
        found += fetch_sponsorblock(url, dur, cfg, log)
    if use_text:
        if segments:
            found += detect_text(segments, video, cfg, log, title)
        else:
            log("Реклама по тексту: транскрипта нет — слой пропущен.")
    ranges = [Range(max(r.start, 0.0), min(r.end, dur), r.source, r.note) for r in found if r.end > 0 and r.start < dur]
    ranges = merge_ranges(ranges)
    if not ranges:
        log("Рекламы не найдено — исходник остаётся как есть.")
        return video, segments
    cut = sum(r.duration for r in ranges)
    if dur and cut > dur * float(s["max_share"]):
        log(f"Найдено рекламы {cut:.0f} с из {dur:.0f} с (больше {float(s['max_share']) * 100:.0f}% видео) — это похоже на ошибку, "
            f"ничего не режу. Порог — [adcut] max_share в config.toml.")
        return video, segments
    for r in ranges:
        ex = " ".join(g.text for g in segments if g.end > r.start and g.start < r.end)[:100]
        log(f"Вырезаю рекламу {mmss(r.start)}–{mmss(r.end)} ({r.duration:.0f} с, {r.source}{', ' + r.note if r.note else ''})"
            + (f": «{ex}»" if ex else ""))
    out = noads_path(video, cfg)
    log(f"Склеиваю видео без рекламы (−{cut:.0f} с)…")
    cut_video(video, keep_intervals(ranges, dur), out, info["has_audio"])
    return out, remove_ranges(segments, ranges)
