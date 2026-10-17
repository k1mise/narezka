"""Видео-вставка («удовлетворяющее» видео: чистка ковров, песок, чернила и т. п.) под или над основным клипом.

Раскладка кадра 1080×1920 (проценты можно менять в config.toml, секция [filler]):
  • основной клип обрезается по центру до полосы высотой main_percent % кадра и прижимается к верху или низу;
  • вставка занимает video_percent % оставшегося места по высоте и 100 % по ширине (кадр обрезается, чтобы заполнить полосу);
  • оставшаяся узкая полоска — под название клипа.
Вставка снизу («position = bottom»): клип сверху → полоска с названием между клипом и вставкой → вставка.
Вставка сверху («position = top»): полоска с названием в самом верху → вставка → клип снизу.
Главная часть собирается прямо из исходного видео (на всю ширину полосы, без размытых полей) вместе с субтитрами; если исходника уже нет —
из готового клипа (резервный вариант). Баннеры накладываются уже на готовый кадр со вставкой.
Вставка выбирается случайно из папки assets/fillers (или задаётся файлом) и запоминается в клипе, чтобы пересборка была той же."""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
POSITIONS = ("top", "bottom")
DEFAULTS = {"dir": "assets/fillers", "main_percent": 52, "band_center_percent": 57, "video_percent": 85,
            "strip_color": "#000000", "font": "Arial", "max_font": 66, "min_font": 30}


class FillerError(RuntimeError):
    pass


def fcfg(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("filler") or {})}


def library_dir(cfg: dict) -> Path:
    p = Path(fcfg(cfg)["dir"])
    return p if p.is_absolute() else Path(cfg["_root"]) / p


def library(cfg: dict) -> list[Path]:
    d = library_dir(cfg)
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT) if d.is_dir() else []


def normalize(raw, cfg: dict | None = None) -> dict | None:
    """Описание вставки клипа: {"position": top|bottom, "file": "" (случайное) или имя файла, + служебные picked/offset}. None — без вставки."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except ValueError:
            raw = None
    if not isinstance(raw, dict) or raw.get("position") not in POSITIONS:
        return None
    out = {"position": raw["position"], "file": Path(str(raw.get("file") or "")).name}
    if raw.get("picked"):
        out["picked"] = Path(str(raw["picked"])).name
        try:
            out["offset"] = max(0.0, float(raw.get("offset") or 0))
        except (TypeError, ValueError):
            out["offset"] = 0.0
    return out


def layout(cfg: dict, position: str) -> dict:
    """Координаты блоков в кадре: основной клип, полоска названия, вставка."""
    c = fcfg(cfg)
    w, h = cfg["clips"]["width"], cfg["clips"]["height"]
    even = lambda v: int(v) // 2 * 2
    main_h = even(h * min(max(c["main_percent"], 20), 80) / 100)
    rest = h - main_h
    vid_h = even(rest * min(max(c["video_percent"], 50), 95) / 100)
    strip_h = rest - vid_h
    band_y = even(min(max(h * c["band_center_percent"] / 100 - main_h / 2, 0), h - main_h))   # откуда берём полосу из исходного кадра
    if position == "bottom":
        return {"w": w, "h": h, "main_h": main_h, "band_y": band_y, "main_y": 0, "strip_y": main_h, "strip_h": strip_h,
                "vid_y": main_h + strip_h, "vid_h": vid_h}
    return {"w": w, "h": h, "main_h": main_h, "band_y": band_y, "main_y": rest, "strip_y": 0, "strip_h": strip_h,
            "vid_y": strip_h, "vid_h": vid_h}


def wrap_title(title: str, width: int, height: int, max_font: int, min_font: int) -> tuple[int, list[str]]:
    """Подбирает размер шрифта и перенос так, чтобы название влезло в полоску (до трёх строк, иначе обрезается с «…»)."""
    title = " ".join((title or "").split())
    for size in range(max_font, min_font - 1, -2):
        lines = textwrap.wrap(title, width=max(6, int(width / (0.58 * size)))) or [""]
        if len(lines) * size * 1.2 <= height:
            return size, lines
    size = min_font
    per = max(6, int(width / (0.58 * size)))
    lines = textwrap.wrap(title, width=per) or [""]
    cap = max(1, int(height // (size * 1.2)))
    if len(lines) > cap:
        lines = lines[:cap]
        lines[-1] = lines[-1].rstrip(" ,.;:-")[:max(per - 1, 1)] + "…"
    return size, lines


def title_ass(title: str, lay: dict, cfg: dict) -> str:
    c = fcfg(cfg)
    size, lines = wrap_title(title, lay["w"] - 80, lay["strip_h"] - 8, int(c["max_font"]), int(c["min_font"]))
    text = r"\N".join(x.replace("{", "(").replace("}", ")") for x in lines)
    cy = lay["strip_y"] + lay["strip_h"] // 2
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {lay['w']}\nPlayResY: {lay['h']}\nWrapStyle: 2\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,{c['font']},{size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,5,40,40,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,0:00:00.00,9:59:59.00,Default,,0,0,0,,{{\\an5\\pos({lay['w'] // 2},{cy})}}{text}\n")


def build_filter(lay: dict, cfg: dict) -> str:
    w, h = lay["w"], lay["h"]
    color = "0x" + fcfg(cfg)["strip_color"].lstrip("#")
    return (f"[0:v]crop={w}:{lay['main_h']}:0:{lay['band_y']},setsar=1[m];"
            f"[1:v]scale={w}:{lay['vid_h']}:force_original_aspect_ratio=increase,crop={w}:{lay['vid_h']},setsar=1,fps=30[f];"
            f"color=c={color}:s={w}x{h}:r=30[bg];"
            f"[bg][m]overlay=0:{lay['main_y']}[a];[a][f]overlay=0:{lay['vid_y']}[b];"
            f"[b]ass=title.ass,format=yuv420p[vout]")


def sidecar_paths(clip_path: Path) -> tuple[Path, Path]:
    return clip_path.with_name(clip_path.stem + ".narez.json"), clip_path.with_name(clip_path.stem + ".narez.ass")


def write_sidecar(clip_path: Path, src: Path, start: float, end: float, ass_text: str | None) -> None:
    """Рядом с готовым клипом запоминаем, из какого исходника и каких секунд он сделан и какие у него субтитры —
    чтобы позже собрать главную часть кадра под вставку напрямую из исходника."""
    meta, ass = sidecar_paths(clip_path)
    try:
        meta.write_text(json.dumps({"src": str(src.resolve()), "start": start, "end": end, "ass": bool(ass_text)}), encoding="utf-8")
        if ass_text:
            ass.write_text(ass_text, encoding="utf-8")
        else:
            ass.unlink(missing_ok=True)
    except OSError:
        pass


def read_sidecar(clip_path: Path) -> dict | None:
    meta, ass = sidecar_paths(clip_path)
    try:
        d = json.loads(meta.read_text(encoding="utf-8"))
        if not Path(d["src"]).is_file():
            return None
        d["ass_text"] = ass.read_text(encoding="utf-8") if d.get("ass") and ass.exists() else ""
        return d
    except (OSError, ValueError, KeyError):
        return None


def copy_sidecar(src_clip: Path, dst_clip: Path) -> None:
    import shutil as _sh
    for a, b in zip(sidecar_paths(src_clip), sidecar_paths(dst_clip)):
        if a.exists():
            _sh.copy2(a, b)


def drop_sidecar(clip_path: Path) -> None:
    for p in sidecar_paths(clip_path):
        p.unlink(missing_ok=True)


def fit_subs(ass_text: str, main_h: int, margin_v: int) -> str:
    """Субтитры клипа (написаны под кадр 1080×1920) переносим в нижнюю часть главной полосы."""
    ass_text = re.sub(r"^PlayResY:.*$", f"PlayResY: {main_h}", ass_text, flags=re.M)
    return re.sub(r"^(Style: Default,.*,2,\d+,\d+,)\d+(,\d+)$", lambda m: f"{m.group(1)}{margin_v}{m.group(2)}", ass_text, flags=re.M)


def _run(cmd: list[str], td: Path) -> None:
    p = subprocess.run(cmd, cwd=td, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise FillerError(p.stderr[-800:])


def _encode_args(cfg: dict) -> list[str]:
    cl = cfg["clips"]
    return ["-c:v", "libx264", "-crf", str(cl["crf"]), "-preset", cl["preset"], "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]


def compose(base: Path, out: Path, filler: Path, offset: float, title: str, position: str, cfg: dict, log=print,
            sidecar: dict | None = None) -> Path:
    """Собирает кадр. С sidecar — главная часть берётся из исходника (cover на всю ширину полосы, субтитры внутри полосы);
    без него — вырезается полоса из готового клипа."""
    from .banner import probe
    if not shutil.which("ffmpeg"):
        raise FillerError("ffmpeg не найден")
    lay = layout(cfg, position)
    w, h = lay["w"], lay["h"]
    color = "0x" + fcfg(cfg)["strip_color"].lstrip("#")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "title.ass").write_text(title_ass(title, lay, cfg), encoding="utf-8")
        tail = (f"color=c={color}:s={w}x{h}:r=30[bg];[bg][m]overlay=0:{lay['main_y']}[a];[a][f]overlay=0:{lay['vid_y']}[b];"
                f"[b]ass=title.ass,format=yuv420p[vout]")
        fill = (f"[1:v]scale={w}:{lay['vid_h']}:force_original_aspect_ratio=increase,crop={w}:{lay['vid_h']},setsar=1,fps=30[f];")
        if sidecar:
            dur = max(sidecar["end"] - sidecar["start"], 0.5)
            main = f"[0:v]scale={w}:{lay['main_h']}:force_original_aspect_ratio=increase,crop={w}:{lay['main_h']}:(iw-ow)/2:(ih-oh)*0.3,setsar=1"
            if sidecar.get("ass_text"):
                # клип внизу экрана («вставка сверху») — субтитры поднимаем над нижней зоной интерфейса соцсети
                (td / "subs.ass").write_text(fit_subs(sidecar["ass_text"], lay["main_h"], 300 if position == "top" else 70), encoding="utf-8")
                main += ",ass=subs.ass"
            fc = main + "[m];" + fill + tail
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{sidecar['start']:.3f}", "-to", f"{sidecar['end']:.3f}",
                   "-i", str(Path(sidecar["src"]).resolve()), "-stream_loop", "-1", "-ss", f"{max(offset, 0):.2f}", "-i", str(filler.resolve()),
                   "-filter_complex", fc, "-map", "[vout]", "-map", "0:a?", "-t", f"{dur:.3f}",
                   "-af", f"afade=t=out:st={max(dur - 0.3, 0):.2f}:d=0.3", *_encode_args(cfg), str(out.resolve())]
        else:
            dur = probe(base)["duration"]
            fc = f"[0:v]crop={w}:{lay['main_h']}:0:{lay['band_y']},setsar=1[m];" + fill + tail
            cl = cfg["clips"]
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(base.resolve()),
                   "-stream_loop", "-1", "-ss", f"{max(offset, 0):.2f}", "-i", str(filler.resolve()),
                   "-filter_complex", fc, "-map", "[vout]", "-map", "0:a?", "-t", f"{dur:.3f}",
                   "-c:v", "libx264", "-crf", str(cl["crf"]), "-preset", cl["preset"], "-c:a", "copy", "-movflags", "+faststart",
                   str(out.resolve())]
        _run(cmd, td)
    return out


def resolve_pick(spec: dict, cfg: dict) -> tuple[dict, Path]:
    """Фиксирует вставку в описании: случайную или заданную файлом. Возвращает (описание с picked/offset, путь к файлу)."""
    from .banner import probe
    files = library(cfg)
    by_name = {p.name: p for p in files}
    if not files:
        raise FillerError(f"Нет видео для вставки: положи ролики в папку {library_dir(cfg)} (или запусти get_fillers.py).")
    if spec.get("file"):
        if spec["file"] not in by_name:
            raise FillerError(f"Видео для вставки «{spec['file']}» не найдено в {library_dir(cfg)}")
        name = spec["file"]
    elif spec.get("picked") in by_name:
        name = spec["picked"]
    else:
        name = random.choice(files).name
    offset = spec.get("offset") if spec.get("picked") == name else None
    if offset is None:
        try:
            length = probe(by_name[name])["duration"]
        except Exception:   # noqa: BLE001
            length = 0.0
        offset = round(random.uniform(0, max(length - 2, 0)), 2) if length > 3 else 0.0
    return {**spec, "picked": name, "offset": offset}, by_name[name]


def cache_key(clip, spec: dict, cfg: dict, sidecar: dict | None) -> str:
    base = Path(clip["path"])
    return json.dumps({"spec": spec, "title": clip["title"] or "", "layout": layout(cfg, spec["position"]),
                       "mode": "direct" if sidecar else "band", "base": f"{base.name}:{int(base.stat().st_mtime) if base.exists() else 0}"},
                      ensure_ascii=False, sort_keys=True)


def cached(clip, cfg: dict) -> Path | None:
    """Готовый файл со вставкой, если он актуален (то же описание, название и раскладка)."""
    spec = normalize(clip["filler"])
    if not spec or not spec.get("picked") or not clip["filler_path"] or not Path(clip["filler_path"]).exists() or not clip["path"]:
        return None
    key = cache_key(clip, spec, cfg, read_sidecar(Path(clip["path"])))
    return Path(clip["filler_path"]) if clip["filler_applied"] == key else None


def apply_to_clip(db, clip, cfg: dict, log=print) -> Path:
    """Собирает кадр «клип + вставка + название» (без баннеров) и запоминает результат; повторный вызов берёт готовый файл."""
    spec = normalize(clip["filler"])
    base = Path(clip["path"])
    spec, fpath = resolve_pick(spec, cfg)
    sidecar = read_sidecar(base)
    key = cache_key(clip, spec, cfg, sidecar)
    if clip["filler_path"] and Path(clip["filler_path"]).exists() and clip["filler_applied"] == key and normalize(clip["filler"]).get("picked") == spec["picked"]:
        return Path(clip["filler_path"])
    out = base.with_name(base.stem + ".filler.mp4")
    log(f"Добавляю видео-вставку ({'сверху' if spec['position'] == 'top' else 'снизу'}): {spec['picked']}"
        + ("" if sidecar else " (исходник недоступен — беру кадр из готового клипа)"))
    compose(base, out, fpath, spec["offset"], clip["title"] or "", spec["position"], cfg, log, sidecar)
    db.update_clip(clip["id"], filler=json.dumps(spec, ensure_ascii=False), filler_path=str(out), filler_applied=key)
    return out
