"""Рекламный баннер: PNG-картинка или видео (со своим звуком). Накладывается на готовый клип отдельным проходом ffmpeg,
поэтому его можно менять после нарезки, не пересчитывая субтитры и LLM-этапы.

Параметры баннера клипа хранятся словарём (JSON в БД):
  path, start (сек. от начала клипа), duration (0 = видео целиком), width_percent, position (center/top/bottom),
  margin (px), duck_percent (до какого уровня, в %, приглушается звук основного видео, пока играет баннер со звуком)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
POSITIONS = ("center", "top", "bottom")
RAMP = 0.25  # плавность приглушения звука, сек.
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
GRID = 240   # размер кадра (px), в который сжимаются кадры видео-баннера для поиска фона и границ содержимого


class BannerError(RuntimeError):
    pass


def resolve(path: str | Path, cfg: dict) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path(cfg["_root"]) / p


def probe(path: str | Path) -> dict:
    """kind (image/video), размеры, длительность (сек.), есть ли звук."""
    p = Path(path)
    if not p.exists():
        raise BannerError(f"Файл не найден: {p}")
    if not shutil.which("ffprobe"):
        raise BannerError("ffprobe не найден (он ставится вместе с ffmpeg: winget install Gyan.FFmpeg)")
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                        "-of", "json", str(p)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v or not int(v.get("width") or 0) or not int(v.get("height") or 0):  # битый файл: ffprobe отдаёт поток 0×0
        raise BannerError(f"В файле нет видео/картинки: {p.name}")
    kind = "image" if p.suffix.lower() in IMAGE_EXT else "video"
    dur = float(data.get("format", {}).get("duration") or 0)
    return {"kind": kind, "w": int(v["width"]), "h": int(v["height"]),
            "duration": dur if kind == "video" else 0.0,
            "has_audio": kind == "video" and any(s.get("codec_type") == "audio" for s in streams)}


def default_spec(cfg: dict, path: str | Path, kind: str | None = None) -> dict:
    bn = cfg["banner"]
    kind = kind or ("image" if Path(path).suffix.lower() in IMAGE_EXT else "video")
    video = kind == "video"
    return {
        "path": str(path),
        "start": float(bn.get("start_at", 3.0)),
        "start_mode": "seconds",     # seconds = start в секундах от начала клипа, percent = start_percent % длины клипа
        "start_percent": float(bn.get("start_percent", 10.0)),
        "duration": 0.0 if video else float(bn.get("duration", 6.0)),
        "width_percent": float(bn.get("video_width_percent", 100)) if video else float(bn.get("width_percent", 90)),
        "position": bn.get("position", "center") if bn.get("position") in POSITIONS else "center",
        "margin": int(bn.get("margin", 160)),
        "duck_percent": int(bn.get("duck_percent", 15)),
        "pause": bool(bn.get("pause", False)),
        **_chroma_defaults(cfg, path, video),
    }


def normalize_spec(d: dict, cfg: dict) -> dict:
    """Приводит параметры баннера из интерфейса к допустимым значениям (числа, диапазоны, положение)."""
    def num(key, default, lo, hi):
        try:
            v = float(d.get(key, default))
        except (TypeError, ValueError):
            v = float(default)
        return min(max(v, lo), hi)
    dflt = default_spec(cfg, d["path"])
    return {"path": str(d["path"]), "start": num("start", dflt["start"], 0, 3600),
            "start_mode": "percent" if d.get("start_mode") == "percent" else "seconds",
            "start_percent": num("start_percent", dflt["start_percent"], 0, 99),
            "duration": num("duration", dflt["duration"], 0, 600),
            "width_percent": num("width_percent", dflt["width_percent"], 10, 100),
            "position": d.get("position") if d.get("position") in POSITIONS else dflt["position"],
            "margin": int(num("margin", dflt["margin"], 0, 800)),
            "duck_percent": int(num("duck_percent", dflt["duck_percent"], 0, 100)),
            "pause": bool(d.get("pause", dflt["pause"])),
            **_chroma_norm(d, dflt)}


def config_spec(cfg: dict) -> dict | None:
    """Баннер по умолчанию из config.toml (если включён и файл на месте) — для каждого нового клипа."""
    bn = cfg["banner"]
    if not bn.get("enabled") or bn.get("max_per_clip", 1) <= 0:
        return None
    p = resolve(bn["image"], cfg)
    return default_spec(cfg, p) if p.exists() else None


def specs_from_json(raw: str | list | dict | None) -> list[dict]:
    """Баннеры клипа из БД: в старых записях — один словарь, в новых при нескольких баннерах — список."""
    if not raw:
        return []
    data = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict) and d.get("path")]


def specs_to_json(specs: list[dict]) -> str:
    """Один баннер хранится словарём (как раньше), несколько — списком; пусто — пустой строкой."""
    if not specs:
        return ""
    return json.dumps(specs[0] if len(specs) == 1 else specs, ensure_ascii=False)


def start_seconds(spec: dict, clip_dur: float) -> float:
    """Когда баннер появляется (сек. от начала клипа): прямо в секундах или как процент длины клипа."""
    if spec.get("start_mode") == "percent":
        return max(0.0, clip_dur * min(max(float(spec.get("start_percent", 0)), 0.0), 100.0) / 100.0)
    return max(float(spec.get("start", 0)), 0.0)


# ---------- хромакей: убираем однотонный (обычно зелёный) фон видео-баннера ----------
@lru_cache(maxsize=6)
def _frames(path: str, mtime: float):
    """До 16 кадров видео (2 кадра/с, первые 30 с), сжатых до GRID×GRID: массив (n, GRID, GRID, 3) или None."""
    import numpy as np
    if not shutil.which("ffmpeg"):
        return None
    r = subprocess.run(["ffmpeg", "-v", "error", "-t", "30", "-i", path, "-vf", f"fps=2,scale={GRID}:{GRID}",
                        "-frames:v", "16", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    n = len(r.stdout) // (GRID * GRID * 3)
    if r.returncode != 0 or n == 0:
        return None
    return np.frombuffer(r.stdout[: n * GRID * GRID * 3], dtype="uint8").reshape(n, GRID, GRID, 3)


def _frames_of(path: str | Path):
    p = Path(path)
    return _frames(str(p), p.stat().st_mtime)


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*[int(round(v)) for v in rgb])


def detect_key(path: str | Path) -> str | None:
    """Цвет однотонного зелёного фона (#rrggbb) по краям кадра или None, если фон не зелёный/не однотонный.
    Почти чёрные пиксели (полосы леттербокса по краю кадра) не учитываются."""
    import numpy as np
    try:
        fr = _frames_of(path)
    except OSError:
        return None
    if fr is None:
        return None
    m = int(GRID * 0.15)
    ring = np.ones((GRID, GRID), dtype=bool)
    ring[m:-m, m:-m] = False
    px = fr[:: max(1, len(fr) // 4)][:, ring].reshape(-1, 3).astype(float)
    px = px[px.max(axis=1) > 24]
    if len(px) < 200:
        return None
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    green = px[(g >= 90) & (g - r >= 40) & (g - b >= 40)]
    if len(green) < 0.5 * len(px):
        return None     # по краям не зелёный фон
    mid = np.median(green, axis=0)
    if np.percentile(np.linalg.norm(green - mid, axis=1), 80) > 50:
        return None     # зелёный, но не однотонный (например, зелёная картинка)
    return _hex(mid)


def content_box(path: str | Path, key_hex: str, tol: float = 80.0, black: int = 24) -> tuple[float, float, float, float] | None:
    """Прямоугольник (x0, y0, x1, y1) в долях кадра, где есть что-то кроме фона key_hex (по всем кадрам). None — обрезать нечего.
    Почти чёрные пиксели тоже считаются фоном: у зелёных баннеров часто остаются чёрные полосы по краям кадра (леттербокс)."""
    import numpy as np
    fr = _frames_of(path)
    if fr is None:
        return None
    key = np.array([int(key_hex[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)
    dist = np.linalg.norm(fr.astype(float) - key, axis=-1)
    f = fr.astype(int)
    r, g, b, mx = f[..., 0], f[..., 1], f[..., 2], f.max(axis=-1)
    dark_green = (mx <= 90) & (g - r >= 20) & (g - b >= 20)    # переход «чёрная полоса → зелёный фон» после сжатия кадра
    content = (dist > tol) & (mx > black) & ~dark_green
    mask = content.any(axis=0)
    cols, rows = np.where(mask.sum(axis=0) >= 2)[0], np.where(mask.sum(axis=1) >= 2)[0]
    if not len(cols) or not len(rows):
        return None
    pad = 0.006
    x0, x1 = max(cols[0] / GRID - pad, 0.0), min((cols[-1] + 1) / GRID + pad, 1.0)
    y0, y1 = max(rows[0] / GRID - pad, 0.0), min((rows[-1] + 1) / GRID + pad, 1.0)
    if x1 - x0 > 0.97 and y1 - y0 > 0.97:
        return None
    return float(round(x0, 4)), float(round(y0, 4)), float(round(x1, 4)), float(round(y1, 4))


def _chroma_defaults(cfg: dict, path: str | Path, video: bool) -> dict:
    key = None
    if video and cfg["banner"].get("chroma_auto", True):
        try:
            p = resolve(path, cfg)
            key = detect_key(p) if p.is_file() else None
        except Exception:    # noqa: BLE001 — автоопределение не должно ломать работу с баннером
            key = None
    return {"chroma": key is not None, "chroma_color": key or "#00ff00"}


def _chroma_norm(d: dict, dflt: dict) -> dict:
    color = d.get("chroma_color")
    color = color.lower() if isinstance(color, str) and HEX_RE.match(color) else dflt["chroma_color"]
    on = d.get("chroma")
    return {"chroma": bool(on) if on is not None else dflt["chroma"], "chroma_color": color}


def effective_info(spec: dict, info: dict, cfg: dict) -> dict:
    """Параметры баннера «как он будет виден»: при хромакее — размеры после обрезки пустого фона (поле crop = (w, h, x, y) в пикселях файла)."""
    out = {**info, "crop": None}
    if info["kind"] == "video" and spec.get("chroma"):
        try:
            box = content_box(resolve(spec["path"], cfg), spec.get("chroma_color") or "#00ff00")
        except OSError:
            box = None
        if box:
            x0, y0, x1, y1 = box
            cx, cy = int(x0 * info["w"]) // 2 * 2, int(y0 * info["h"]) // 2 * 2
            cw = min(max(int((x1 - x0) * info["w"]) // 2 * 2, 2), info["w"] - cx)
            ch = min(max(int((y1 - y0) * info["h"]) // 2 * 2, 2), info["h"] - cy)
            out.update(w=cw, h=ch, crop=(cw, ch, cx, cy))
    return out


def chroma_filter(spec: dict, cfg: dict) -> str:
    color = "0x" + (spec.get("chroma_color") or "#00ff00")[1:]
    bn = cfg["banner"]
    f = f"format=rgba,chromakey=color={color}:similarity={float(bn.get('chroma_similarity', 0.22)):.3f}:blend={float(bn.get('chroma_blend', 0.08)):.3f}"
    return f + ",despill=type=green,format=rgba"   # despill убирает зелёную кайму на краях объекта


def geometry(spec: dict, info: dict, cfg: dict) -> tuple[int, int, float, bool]:
    """Итоговые размеры баннера в кадре, доля площади экрана и выполняется ли минимум по площади.
    Для видео-баннера ширина при необходимости увеличивается, чтобы он занимал не меньше min_area_percent."""
    W, H = cfg["clips"]["width"], cfg["clips"]["height"]
    info = effective_info(spec, info, cfg)      # при хромакее считаем по видимой части, а не по всему кадру с фоном
    aspect = info["w"] / info["h"]
    w = W * float(spec.get("width_percent", 100)) / 100
    min_area = float(cfg["banner"].get("min_area_percent", 25)) / 100
    if info["kind"] == "video":
        w = max(w, (min_area * W * H * aspect) ** 0.5)
    w = min(w, W, H * aspect)
    w = max(int(w) // 2 * 2, 2)
    h = max(int(w / aspect) // 2 * 2, 2)
    frac = w * h / (W * H)
    return w, h, frac, info["kind"] != "video" or frac >= min_area - 0.005


def describe_info(spec: dict, cfg: dict) -> dict:
    """Сведения о баннере для интерфейса: тип, звук, длительность, доля экрана, выполняется ли минимум по площади."""
    info = probe(resolve(spec["path"], cfg))
    w, h, frac, ok = geometry(spec, info, cfg)
    min_area = cfg["banner"].get("min_area_percent", 25)
    if info["kind"] == "image":
        text = f"Картинка {info['w']}×{info['h']}, займёт {frac * 100:.0f}% экрана."
    else:
        snd = "со звуком" if info["has_audio"] else "без звука"
        text = f"Видео {info['w']}×{info['h']}, {info['duration']:.1f} с, {snd}; займёт {frac * 100:.0f}% экрана"
        text += " ✓" if ok else f" — МЕНЬШЕ {min_area}%: у баннера слишком вытянутая форма."
        if spec.get("chroma"):
            ei = effective_info(spec, info, cfg)
            text += f". Фон {spec.get('chroma_color')} вырезан" + (f", видимая часть {ei['w']}×{ei['h']}" if ei["crop"] else "")
    return {"kind": info["kind"], "has_audio": info["has_audio"], "duration": info["duration"],
            "width": info["w"], "height": info["h"], "out_w": w, "out_h": h, "area_percent": round(frac * 100, 1), "ok": ok, "text": text}


def describe(spec: dict, cfg: dict) -> str:
    """Короткое описание для окна редактирования."""
    return describe_info(spec, cfg)["text"]


def apply_banner(base: Path, out: Path, spec: dict, cfg: dict, log=print) -> Path:
    return _apply_one(base, out, spec, cfg, log)[0]


def _apply_one(base: Path, out: Path, spec: dict, cfg: dict, log=print, t0: float | None = None,
               draft: bool = False) -> tuple[Path, float]:
    """Накладывает один баннер. t0 — начало в этом файле (если не задано — из параметров баннера).
    draft=True — промежуточный проход (почти без потерь, чтобы не копились артефакты сжатия).
    Возвращает (файл, сколько секунд добавилось к длине клипа: при паузе — длительность баннера, иначе 0)."""
    if not shutil.which("ffmpeg"):
        raise BannerError("ffmpeg не найден. Установи: winget install Gyan.FFmpeg (и перезапусти терминал)")
    bpath = resolve(spec["path"], cfg)
    if out.resolve() in (base.resolve(), bpath.resolve()):
        raise BannerError("Результат нельзя сохранять поверх исходного клипа или файла баннера")
    binfo, minfo = probe(bpath), probe(base)
    clip_dur = minfo["duration"]
    if t0 is None:
        t0 = start_seconds(spec, clip_dur)
    if t0 >= clip_dur - 0.5:
        raise BannerError(f"Баннер должен начинаться раньше конца клипа ({clip_dur:.0f} с)")
    want = float(spec.get("duration") or 0)
    if binfo["kind"] == "video":
        dur = binfo["duration"] if want <= 0 else min(want, binfo["duration"])
    else:
        dur = want if want > 0 else float(cfg["banner"].get("duration", 6.0))
    pause = bool(spec.get("pause"))
    if not pause:
        dur = min(dur, clip_dur - t0)
    t1 = t0 + dur
    total = clip_dur + dur if pause else clip_dur  # при паузе клип становится длиннее на время баннера
    bw, bh, frac, ok = geometry(spec, binfo, cfg)
    if not ok:
        log(f"Внимание: баннер занимает только {frac * 100:.0f}% экрана (нужно ≥{cfg['banner'].get('min_area_percent', 25)}%)")
    pos = spec.get("position", "center")
    margin = int(spec.get("margin", 160))
    y = {"top": f"{margin}", "bottom": f"H-h-{margin}"}.get(pos, "(H-h)/2")

    chain, vin, ain = [], "[0:v]", "[0:a]"
    if pause:
        # основное видео замирает на кадре t0 на время баннера: до — стоп-кадр — после; звук на паузе молчит
        chain.append(f"[0:v]trim=end={t0:.3f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={dur:.3f}[pv1]")
        chain.append(f"[0:v]trim=start={t0:.3f},setpts=PTS-STARTPTS[pv2]")
        chain.append("[pv1][pv2]concat=n=2:v=1:a=0[mv]")
        vin = "[mv]"
        if minfo["has_audio"]:
            chain.append(f"[0:a]atrim=end={t0:.3f},asetpts=PTS-STARTPTS,apad=pad_dur={dur:.3f}[pa1]")
            chain.append(f"[0:a]atrim=start={t0:.3f},asetpts=PTS-STARTPTS[pa2]")
            chain.append("[pa1][pa2]concat=n=2:v=0:a=1[ma]")
            ain = "[ma]"

    if binfo["kind"] == "video":
        pre = ""
        if spec.get("chroma"):
            crop = effective_info(spec, binfo, cfg)["crop"]
            pre = (f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else "") + chroma_filter(spec, cfg) + ","
        chain.append(f"[1:v]trim=duration={dur:.3f},setpts=PTS-STARTPTS+{t0:.3f}/TB,{pre}scale={bw}:{bh},setsar=1[bn]")
    else:
        chain.append(f"[1:v]scale={bw}:{bh},format=rgba[bn]")
    chain.append(f"{vin}[bn]overlay=(W-w)/2:{y}:eof_action=pass:enable='between(t,{t0:.3f},{t1:.3f})'[v1]")
    chain.append("[v1]format=yuv420p[vout]")

    amap, acodec = ("[ma]" if (pause and minfo["has_audio"]) else "0:a?"), ["-c:a", "copy"]
    if pause and minfo["has_audio"]:
        amap, acodec = "[ma]", ["-c:a", "aac", "-b:a", "160k"]
    if binfo["has_audio"]:
        ms = int(t0 * 1000)
        chain.append(f"[1:a]atrim=duration={dur:.3f},asetpts=PTS-STARTPTS,adelay={ms}:all=1[ba]")
        if minfo["has_audio"]:
            if pause:
                chain.append(f"{ain}[ba]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
            else:
                level = max(0, min(100, int(spec.get("duck_percent", 15)))) / 100
                # звук основного видео плавно падает до level на время баннера и так же плавно возвращается
                chain.append(
                    f"[0:a]volume='if(between(t,{t0:.3f},{t1:.3f}),"
                    f"1-(1-{level})*min(1,min((t-{t0:.3f})/{RAMP},({t1:.3f}-t)/{RAMP})),1)':eval=frame[am]")
                chain.append("[am][ba]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
        else:
            chain.append("[ba]apad[aout]")
        amap, acodec = "[aout]", ["-c:a", "aac", "-b:a", "160k"]

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(base.resolve())]
    if binfo["kind"] == "image":
        cmd += ["-loop", "1", "-t", f"{total:.3f}"]
    cmd += ["-i", str(bpath.resolve()), "-filter_complex", ";".join(chain),
            "-map", "[vout]", "-map", amap, "-t", f"{total:.3f}",
            "-c:v", "libx264", "-crf", "12" if draft else str(cfg["clips"]["crf"]),
            "-preset", "ultrafast" if draft else cfg["clips"]["preset"],
            *acodec, "-movflags", "+faststart", str(out.resolve())]
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise BannerError(p.stderr[-800:])
    return out, (dur if pause else 0.0)


def apply_banners(base: Path, out: Path, specs: list[dict], cfg: dict, log=print) -> Path:
    """Накладывает несколько баннеров по очереди (по времени появления). Начало каждого считается по исходному клипу;
    если у более раннего баннера включена пауза, последующие сдвигаются на её длину."""
    if len(specs) == 1:
        return apply_banner(base, out, specs[0], cfg, log)
    clip_dur = probe(base)["duration"]
    order = sorted(specs, key=lambda sp: start_seconds(sp, clip_dur))
    shift, cur, temps = 0.0, base, []
    try:
        for i, sp in enumerate(order):
            last = i == len(order) - 1
            target = out if last else out.with_name(f"{out.stem}.part{i}.mp4")
            if not last:
                temps.append(target)
            log(f"Накладываю баннер {i + 1} из {len(order)}...")
            _, added = _apply_one(cur, target, sp, cfg, log, t0=start_seconds(sp, clip_dur) + shift, draft=not last)
            shift += added
            cur = target
    finally:
        for t in temps:
            t.unlink(missing_ok=True)
    return out


def ensure_final(db, clip, cfg: dict, log=print) -> Path:
    """Файл клипа для публикации: с баннером (собирается при необходимости) или исходный клип без баннера."""
    base = Path(clip["path"])
    specs = specs_from_json(clip["banner"])
    if not specs:
        return base
    final = Path(clip["final_path"]) if clip["final_path"] else None
    if final and final.exists() and clip["banner_applied"] == clip["banner"]:
        return final
    out = base.with_name(base.stem + ".final.mp4")
    log("Накладываю баннер..." if len(specs) == 1 else f"Накладываю баннеры: {len(specs)}...")
    apply_banners(base, out, specs, cfg, log)
    db.update_clip(clip["id"], final_path=str(out), banner_applied=clip["banner"])
    return out
