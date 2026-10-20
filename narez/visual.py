"""Визуальные границы историй в видео «Реддит»: в начале каждой истории на экране появляется карточка поста
(аватар, r/сабреддит, жирный заголовок). Ищем моменты, когда в левом верхнем углу кадра внезапно появляется
«чернильный» блок — это и есть начало истории. Работает и на светлой, и на тёмной теме."""
from __future__ import annotations

import subprocess

W, H = 160, 90


def _ink_series(video, log=print) -> list[float]:
    """Доля «контрастных» пикселей в левом верхнем углу для каждой секунды видео (1 кадр в секунду)."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-i", str(video), "-an", "-sn", "-vf", f"fps=1,scale={W}:{H}:flags=area,format=gray",
           "-f", "rawvideo", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 and not p.stdout:
        raise RuntimeError(p.stderr.decode("utf-8", "replace")[-300:])
    raw = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(raw) // (W * H)
    if n == 0:
        return []
    fr = raw[: n * W * H].reshape(n, H, W).astype(np.int16)
    bg = np.median(fr.reshape(n, -1), axis=1)[:, None, None]
    ink = np.abs(fr - bg) > 60
    return [float(x) for x in ink[:, 2:11, 5:40].mean(axis=(1, 2))]


def story_starts_from_series(series: list[float], min_gap: float = 8.0) -> list[float]:
    """Моменты (в секундах), когда карточка поста появляется из «пустого» верха кадра и держится ≥3 с."""
    if len(series) < 5:
        return []
    s = sorted(series)
    p99 = s[min(len(s) - 1, int(len(s) * 0.99))]
    if p99 < 0.04:                       # в углу вообще ничего не появляется — это не «Реддит»-раскладка
        return []
    hi = max(0.04, 0.4 * p99)
    lo = hi * 0.5
    out: list[float] = []
    for i, v in enumerate(series):
        if v < hi:
            continue
        if i > 0 and max(series[max(0, i - 3):i]) >= lo:      # блок уже был на экране — это не появление
            continue
        if not all(x >= hi for x in series[i:i + 3]):         # мигнуло и пропало
            continue
        if out and i - out[-1] < min_gap:
            continue
        out.append(float(i))
    return out


def story_starts(video, log=print) -> list[float]:
    try:
        series = _ink_series(video, log)
    except Exception as e:  # noqa: BLE001
        log(f"Визуальный поиск границ историй не удался: {e}")
        return []
    return story_starts_from_series(series)
