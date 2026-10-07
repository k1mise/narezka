"""Нарезка и сборка вертикального видео через ffmpeg."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def build_filter(cfg: dict, has_banner: bool, has_subs: bool) -> str:
    cl, bn = cfg["clips"], cfg["banner"]
    w, h = cl["width"], cl["height"]
    if cl["layout"] == "crop":
        chain = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[v0]"
    else:  # blur: оригинал по центру на размытом фоне
        chain = (
            f"[0:v]split[a][b];"
            # фон блюрим в 4 раза меньшем размере и растягиваем обратно: выглядит так же, считается в разы быстрее
            f"[a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},"
            f"boxblur=8:3,scale={w}:{h}:flags=bilinear[bg];"
            f"[b]scale={w}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]"
        )
    last = "v0"
    if has_banner:
        bw = int(w * bn["width_percent"] / 100)
        y = {"top": f"{bn['margin']}", "center": "(H-h)/2", "bottom": f"H-h-{bn['margin']}"}[bn["position"]]
        t0, t1 = bn["start_at"], bn["start_at"] + bn["duration"]
        chain += (f";[1:v]scale={bw}:-1[bn];[{last}][bn]overlay=(W-w)/2:{y}:"
                  f"enable='between(t,{t0},{t1})'[v1]")
        last = "v1"
    if has_subs:
        chain += f";[{last}]ass=subs.ass[v2]"
        last = "v2"
    return chain + f";[{last}]format=yuv420p[vout]"


def render_clip(video: Path, start: float, end: float, out: Path, ass_text: str | None, cfg: dict) -> Path:
    if not shutil.which("ffmpeg"):
        raise FFmpegError("ffmpeg не найден. Установи: winget install Gyan.FFmpeg (и перезапусти терминал)")
    bn = cfg["banner"]
    banner_path = None
    if bn["enabled"] and bn["max_per_clip"] > 0:
        banner_path = Path(bn["image"])
        if not banner_path.is_absolute():
            banner_path = Path(cfg["_root"]) / banner_path
        if not banner_path.exists():
            raise FFmpegError(f"Баннер включён, но файл не найден: {banner_path}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        has_subs = bool(ass_text)
        if has_subs:
            (td / "subs.ass").write_text(ass_text, encoding="utf-8")  # относительный путь -> нет проблем с C:\ в фильтре
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video.resolve())]
        if banner_path:
            cmd += ["-loop", "1", "-i", str(banner_path.resolve())]
        cmd += ["-filter_complex", build_filter(cfg, bool(banner_path), has_subs),
                "-map", "[vout]", "-map", "0:a?", "-shortest",
                "-af", f"afade=t=out:st={max(end - start - 0.3, 0):.2f}:d=0.3",
                "-c:v", "libx264", "-crf", str(cfg["clips"]["crf"]), "-preset", cfg["clips"]["preset"],
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out.resolve())]
        p = subprocess.run(cmd, cwd=td, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            raise FFmpegError(p.stderr[-800:])
    return out


def _silences(video: Path, a: float, b: float) -> list[tuple[float, float]]:
    """Паузы (абсолютное время) в звуке на отрезке [a, b] — через ffmpeg silencedetect."""
    a = max(a, 0.0)
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(video),
           "-vn", "-af", "silencedetect=noise=-32dB:d=0.1", "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out, start = [], None
    for line in p.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(float(m.group(1)), 0.0)
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((a + start, a + float(m.group(1))))
            start = None
    if start is not None:  # пауза тянется до конца окна
        out.append((a + start, b))
    return out


def find_cut(video: Path, t: float, kind: str) -> float | None:
    """Ищет настоящую паузу в звуке рядом с границей предложения и возвращает точку реза внутри неё.
    kind='end': пауза после t; kind='start': пауза перед t. None — паузу найти не удалось (музыка, шум)."""
    if not shutil.which("ffmpeg"):
        return None
    if kind == "end":
        cands = [x for x in _silences(video, t - 0.5, t + 1.0) if t - 0.5 <= x[0] <= t + 0.9]
        if not cands:
            return None
        s0, s1 = min(cands, key=lambda x: abs(x[0] - t))
        return s0 + min(0.25, (s1 - s0) / 2)
    cands = [x for x in _silences(video, t - 1.0, t + 0.5) if t - 0.9 <= x[1] <= t + 0.4]
    if not cands:
        return None
    s0, s1 = min(cands, key=lambda x: abs(x[1] - t))
    return s1 - min(0.15, (s1 - s0) / 2)


def make_thumb(src: Path | str, out: Path, height: int = 128, at: float = 1.0, vertical: bool = False) -> Path | None:
    """Кадр из клипа (или картинка по URL при at=0) как превью для истории. PNG: tkinter читает его без доп. библиотек.
    vertical=True обрезает 16:9-превью YouTube до центральной вертикальной части. None — не получилось."""
    if not shutil.which("ffmpeg"):
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = ("crop=ih*9/16:ih," if vertical else "") + f"scale=-2:{height}"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + (["-ss", f"{at:.2f}"] if at > 0 else [])
    try:
        p = subprocess.run(cmd + ["-i", str(src), "-frames:v", "1", "-vf", vf, str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return out if p.returncode == 0 and out.exists() else None
