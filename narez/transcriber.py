from __future__ import annotations
import json
from pathlib import Path

from .models import Segment, Word


def _enable_cuda_dlls() -> None:
    """Windows: подключает CUDA-библиотеки из pip-пакетов nvidia-* (cublas/cudnn), если они установлены."""
    import os
    import sys

    if sys.platform != "win32":
        return
    for base in map(Path, sys.path):
        nv = base / "nvidia"
        if nv.is_dir():
            for b in nv.glob("*/bin"):
                try:
                    os.add_dll_directory(str(b))
                except OSError:
                    pass
                os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def build_prompt(hint: str, vocabulary: str) -> str:
    """Подсказка Whisper: название видео и словарь терминов помогают писать имена и сленг правильно."""
    parts = [x.strip() for x in (hint, vocabulary) if x and x.strip()]
    return (". ".join(parts))[:400]


def transcribe(video: Path, cfg: dict, log=print, hint: str = "") -> list[Segment]:
    """Распознаёт речь (faster-whisper) с таймкодами слов. Результат кэшируется рядом с видео."""
    model_name = cfg["whisper"]["model"]
    cache = video.with_name(f"{video.stem}.transcript.{model_name}.json")  # у каждой модели свой кэш
    wd = cfg["paths"]["workdir"].resolve()
    if wd not in video.resolve().parents:  # файл пользователя вне рабочей папки: его папку не засоряем
        cache = wd / "transcripts" / cache.name
        cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        log("Транскрипт найден в кэше")
        return _load(cache)

    w = cfg["whisper"]
    log(f"Распознаю речь (модель {w['model']})... это самый долгий этап")
    prompt = build_prompt(hint, w.get("vocabulary", ""))
    _enable_cuda_dlls()
    try:
        segments, info = _run(video, w, w["device"], w["compute_type"], prompt)
    except RuntimeError as e:
        if w["device"] == "cpu" or not any(x in str(e).lower() for x in ("cublas", "cudnn", "cuda", "dll")):
            raise
        log("Видеокарта (CUDA) недоступна — считаю на процессоре, это медленнее, но работает")
        segments, info = _run(video, w, "cpu", "int8", prompt)
    log(f"Язык: {info.language}, сегментов: {len(segments)}")
    _save(cache, segments)
    return segments


def _save(path: Path, segments: list[Segment]) -> None:
    data = [
        {"start": s.start, "end": s.end, "text": s.text,
         "words": [{"start": w.start, "end": w.end, "text": w.text} for w in s.words]}
        for s in segments
    ]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _load(path: Path) -> list[Segment]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Segment(d["start"], d["end"], d["text"], [Word(**w) for w in d["words"]]) for d in data]


def _run(video: Path, w: dict, device: str, compute_type: str, prompt: str = ""):
    from faster_whisper import WhisperModel

    model = WhisperModel(w["model"], device=device, compute_type=compute_type)
    it, info = model.transcribe(str(video), language=w["language"] or None,
                                word_timestamps=True, vad_filter=True,
                                initial_prompt=prompt or None, beam_size=5)
    segments = []
    for s in it:  # генератор ленивый: ошибки CUDA могут вылезти и здесь
        words = [Word(x.start, x.end, x.word.strip()) for x in (s.words or [])]
        segments.append(Segment(s.start, s.end, s.text.strip(), words))
    return segments, info


_END = (".", "?", "!", "…")


def to_sentences(segments: list[Segment], gap: float = 0.8, max_len: float = 20.0) -> list[Segment]:
    """Перегруппировывает слова в настоящие предложения. Сегменты Whisper режутся где попало (часто посреди
    фразы), поэтому границы клипов нужно искать по знакам конца предложения и паузам, а не по сегментам."""
    words = [w for s in segments for w in s.words]
    out: list[Segment] = []
    buf: list[Word] = []

    def flush():
        nonlocal buf
        if buf:
            out.append(Segment(buf[0].start, buf[-1].end, " ".join(w.text for w in buf), buf))
            buf = []

    for i, w in enumerate(words):
        buf.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = w.text.rstrip("\"»)”").endswith(_END)
        pause = nxt is not None and nxt.start - w.end >= gap
        too_long = w.end - buf[0].start >= max_len and nxt is not None and nxt.start - w.end >= 0.25
        if ends or pause or too_long or nxt is None:
            flush()
    return out
