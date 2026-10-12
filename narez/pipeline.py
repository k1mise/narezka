from __future__ import annotations
import json
import re
import time
from pathlib import Path

from . import db as dbm
from .config import folder
from .downloader import download
from .banner import config_spec, probe
from .editor import find_cut, make_thumb, render_clip
from .meta import split_hashtags
from .models import Moment
from .moments import find_moments, make_metadata
from .subtitles import make_ass
from .transcriber import to_sentences, transcribe


def _slug(s: str) -> str:
    """Название клипа для имени файла: читаемое, с пробелами (кириллица остаётся)."""
    t = re.sub(r"[^\w\- ]+", " ", s, flags=re.U)
    return re.sub(r"\s+", " ", t).strip()[:60] or "clip"


def cut_bounds(video: Path, m, sentences, cfg: dict) -> tuple[float, float]:
    """Точки реза: по реальной паузе в звуке; если паузы нет — небольшой запас, но не заходя в соседнее предложение
    (иначе в клип попадает первое слово следующей фразы)."""
    cl = cfg["clips"]
    nxt = next((s for s in sentences if s.start >= m.end - 1e-3), None)
    prv = next((s for s in reversed(sentences) if s.end <= m.start + 1e-3), None)
    end = find_cut(video, m.end, "end")
    if end is None:
        gap = (nxt.start - m.end) if nxt else 1.0
        end = m.end + min(cl.get("end_pad", 0.4), max(gap, 0) * 0.6)
    elif nxt:
        end = min(end, max(nxt.start - 0.03, m.end))
    start = find_cut(video, m.start, "start")
    if start is None:
        gap = (m.start - prv.end) if prv else 1.0
        start = m.start - min(cl.get("start_pad", 0.1), max(gap, 0) * 0.5)
    elif prv:
        start = max(start, min(prv.end + 0.03, m.start))
    return max(start, 0.0), max(end, start + 1)


MIN_TAIL = 3.0  # если после очередной части остаётся меньше стольких секунд нового материала, он присоединяется к ней


def plan_parts(duration: float, length: float, overlap: float) -> list[tuple[float, float]]:
    """Режет видео целиком на части длиной length; каждая следующая начинается за overlap секунд до конца предыдущей.
    Короткий хвост (меньше MIN_TAIL с нового материала) не плодит отдельный клип, а присоединяется к последней части."""
    if length <= 0 or overlap < 0 or overlap >= length:
        raise ValueError("Нахлёст должен быть меньше длины клипа")
    step, parts, s = length - overlap, [], 0.0
    while True:
        end = min(s + length, duration)
        if duration - end < MIN_TAIL:
            end = duration
        parts.append((round(s, 3), round(end, 3)))
        if end >= duration:
            return parts
        s += step


class _Timer:
    """Замеры по этапам — чтобы понять, что именно тормозит."""

    def __init__(self, log):
        self.log, self.t0, self.t = log, time.time(), time.time()
        self.parts: dict[str, float] = {}

    def lap(self, name: str):
        now = time.time()
        self.parts[name] = self.parts.get(name, 0) + now - self.t
        self.t = now

    def report(self):
        total = time.time() - self.t0
        self.log("— Время по этапам —")
        for k, v in self.parts.items():
            self.log(f"  {k}: {v:.0f} с ({v / total * 100:.0f}%)")
        self.log(f"  Всего: {total:.0f} с")


def run(url: str, cfg: dict, db: dbm.DB, group_id: int | None = None, log=print, test: bool = False,
        mode: str = "moments", length: float = 45.0, overlap: float = 2.5):
    """Ссылка -> скачивание -> транскрипт -> моменты -> готовые вертикальные клипы. Возвращает (job_id, [clips]).
    mode="full": без выбора моментов — всё видео режется подряд на части длиной length с нахлёстом overlap."""
    if mode == "full":
        plan_parts(100.0, length, overlap)  # проверка параметров до скачивания
    workdir: Path = cfg["paths"]["workdir"]
    tm = _Timer(log)
    log("Скачиваю видео...")
    video, title = download(url, workdir, log, cfg)
    log(f"Скачано: {title}")
    tm.lap("скачивание")

    full = mode == "full"
    if full:
        # речь нужна только для субтитров; без субтитров распознавание пропускаем (это самый долгий этап)
        segments = to_sentences(transcribe(video, cfg, log, hint=title)) if cfg["subtitles"]["enabled"] else []
        tm.lap("распознавание речи")
        total = probe(video)["duration"]
        parts = plan_parts(total, length, overlap)
        credit = f"Источник: {title} {url}".strip() if cfg.get("youtube", {}).get("credit_source", True) else ""
        moments = []
        for i, (a, b) in enumerate(parts, 1):
            suffix = f" — часть {i}/{len(parts)}"
            moments.append(Moment(a, b, title[:90 - len(suffix)].rstrip() + suffix, credit, 0.0))
        log(f"Режу видео целиком: {len(moments)} частей по {length:g} с, нахлёст {overlap:g} с.")
    else:
        segments = to_sentences(transcribe(video, cfg, log, hint=title))
        tm.lap("распознавание речи")
        if not segments:
            raise RuntimeError("В видео не найдено речи — нечего нарезать.")
        moments = find_moments(segments, cfg, log)
        if not moments:
            raise RuntimeError("Модель не нашла подходящих моментов. Попробуй модель побольше или другое видео.")
        log(f"Выбрано моментов: {len(moments)}")
        tm.lap("выбор моментов (LLM)")

    job_id = db.add_job(group_id, url, title, str(video), test=test)
    out_dir = folder(cfg, "test" if test else "clips") / f"job{job_id}"
    # баннер на клип накладывается отдельно (его можно поменять в окне редактирования), поэтому при рендере его выключаем
    render_cfg = {**cfg, "banner": {**cfg["banner"], "enabled": False}}
    sub_cfg = cfg
    if full:
        # «целиком» = десятки клипов: LLM-правка субтитров на каждый занимала больше половины времени, поэтому по умолчанию выключена;
        # кодирование чуть быстрее (файл немного больше). Вернуть можно в config.toml: [subtitles] llm_fix_full = true, [clips] full_preset = "veryfast"
        render_cfg["clips"] = {**cfg["clips"], "preset": cfg["clips"].get("full_preset", "superfast")}
        sub_cfg = {**cfg, "subtitles": {**cfg["subtitles"], "llm_fix": bool(cfg["subtitles"].get("llm_fix_full", False))}}
    spec = config_spec(cfg)
    banner_json = json.dumps(spec, ensure_ascii=False) if spec else ""
    width = max(2, len(str(len(moments))))
    for i, m in enumerate(moments, 1):
        log(f"Рендерю клип {i}/{len(moments)}: «{m.title}» ({m.duration:.0f} с)")
        # режем по реальным паузам в звуке; субтитры считаем от тех же границ
        # (в режиме «целиком» границы строго по сетке — иначе нахлёст между частями поплывёт)
        rs, re_ = (m.start, m.end) if full else cut_bounds(video, m, segments, cfg)
        tm.lap("поиск точек реза")
        ass = make_ass(segments, rs, re_, sub_cfg, log, topic=title) if cfg["subtitles"]["enabled"] else None
        tm.lap("субтитры (LLM)")
        out = render_clip(video, rs, re_, out_dir / f"{i:0{width}d} {_slug(m.title)}.mp4", ass, render_cfg)
        tm.lap("рендер клипов (ffmpeg)")
        if not full:
            m.title, m.reason = make_metadata(m, segments, cfg, title, url)  # заголовок и описание для публикации
            tm.lap("заголовки и описания (LLM)")
        body, tags = split_hashtags(m.reason)  # хэштеги храним отдельно, чтобы их можно было править
        m.reason = body
        thumb = make_thumb(out, workdir / "thumbs" / f"job{job_id}_{i:02d}.png")
        db.add_clip(job_id, str(out), m, tags=tags, banner=banner_json, thumb=str(thumb or ""))
    tm.report()
    log("Готово!")
    return job_id, db.clips(job_id)
