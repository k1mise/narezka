"""Рекламный баннер: PNG-картинка или видео (со своим звуком). Накладывается на готовый клип отдельным проходом ffmpeg,
поэтому его можно менять после нарезки, не пересчитывая субтитры и LLM-этапы.

Параметры баннера клипа хранятся словарём (JSON в БД):
  path, start (сек. от начала клипа), duration (0 = видео целиком), width_percent, position (center/top/bottom),
  margin (px), duck_percent (до какого уровня, в %, приглушается звук основного видео, пока играет баннер со звуком)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
POSITIONS = ("center", "top", "bottom")
RAMP = 0.25  # плавность приглушения звука, сек.


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
        "duration": 0.0 if video else float(bn.get("duration", 6.0)),
        "width_percent": float(bn.get("video_width_percent", 100)) if video else float(bn.get("width_percent", 90)),
        "position": bn.get("position", "center") if bn.get("position") in POSITIONS else "center",
        "margin": int(bn.get("margin", 160)),
        "duck_percent": int(bn.get("duck_percent", 15)),
        "pause": bool(bn.get("pause", False)),
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
            "duration": num("duration", dflt["duration"], 0, 600),
            "width_percent": num("width_percent", dflt["width_percent"], 10, 100),
            "position": d.get("position") if d.get("position") in POSITIONS else dflt["position"],
            "margin": int(num("margin", dflt["margin"], 0, 800)),
            "duck_percent": int(num("duck_percent", dflt["duck_percent"], 0, 100)),
            "pause": bool(d.get("pause", dflt["pause"]))}


def config_spec(cfg: dict) -> dict | None:
    """Баннер по умолчанию из config.toml (если включён и файл на месте) — для каждого нового клипа."""
    bn = cfg["banner"]
    if not bn.get("enabled") or bn.get("max_per_clip", 1) <= 0:
        return None
    p = resolve(bn["image"], cfg)
    return default_spec(cfg, p) if p.exists() else None


def geometry(spec: dict, info: dict, cfg: dict) -> tuple[int, int, float, bool]:
    """Итоговые размеры баннера в кадре, доля площади экрана и выполняется ли минимум по площади.
    Для видео-баннера ширина при необходимости увеличивается, чтобы он занимал не меньше min_area_percent."""
    W, H = cfg["clips"]["width"], cfg["clips"]["height"]
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
    return {"kind": info["kind"], "has_audio": info["has_audio"], "duration": info["duration"],
            "width": info["w"], "height": info["h"], "out_w": w, "out_h": h, "area_percent": round(frac * 100, 1), "ok": ok, "text": text}


def describe(spec: dict, cfg: dict) -> str:
    """Короткое описание для окна редактирования."""
    return describe_info(spec, cfg)["text"]


def apply_banner(base: Path, out: Path, spec: dict, cfg: dict, log=print) -> Path:
    if not shutil.which("ffmpeg"):
        raise BannerError("ffmpeg не найден. Установи: winget install Gyan.FFmpeg (и перезапусти терминал)")
    bpath = resolve(spec["path"], cfg)
    if out.resolve() in (base.resolve(), bpath.resolve()):
        raise BannerError("Результат нельзя сохранять поверх исходного клипа или файла баннера")
    binfo, minfo = probe(bpath), probe(base)
    clip_dur = minfo["duration"]
    t0 = max(float(spec.get("start", 0)), 0.0)
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
        chain.append(f"[1:v]trim=duration={dur:.3f},setpts=PTS-STARTPTS+{t0:.3f}/TB,scale={bw}:{bh},setsar=1[bn]")
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
            "-c:v", "libx264", "-crf", str(cfg["clips"]["crf"]), "-preset", cfg["clips"]["preset"],
            *acodec, "-movflags", "+faststart", str(out.resolve())]
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise BannerError(p.stderr[-800:])
    return out


def ensure_final(db, clip, cfg: dict, log=print) -> Path:
    """Файл клипа для публикации: с баннером (собирается при необходимости) или исходный клип без баннера."""
    base = Path(clip["path"])
    if not clip["banner"]:
        return base
    final = Path(clip["final_path"]) if clip["final_path"] else None
    if final and final.exists() and clip["banner_applied"] == clip["banner"]:
        return final
    out = base.with_name(base.stem + ".final.mp4")
    log("Накладываю баннер...")
    apply_banner(base, out, json.loads(clip["banner"]), cfg, log)
    db.update_clip(clip["id"], final_path=str(out), banner_applied=clip["banner"])
    return out
