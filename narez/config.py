from __future__ import annotations
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


FOLDER_DEFAULTS = {"sources": "downloads", "clips": "clips", "test": "test"}
FOLDER_TITLES = {"sources": "Исходники", "clips": "Клипы", "test": "Тесты"}


def folder(cfg: dict, key: str) -> Path:
    """Папка narez: sources (скачанные видео), clips (готовые клипы), test (тестовые прогоны).
    Путь из [paths] sources_dir / clips_dir / test_dir; относительный отсчитывается от workdir."""
    p = Path(str(cfg["paths"].get(key + "_dir") or FOLDER_DEFAULTS[key]))
    p = p if p.is_absolute() else Path(cfg["paths"]["workdir"]) / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.toml"
    with open(p, "rb") as f:
        cfg = tomllib.load(f)
    workdir = Path(cfg["paths"]["workdir"])
    if not workdir.is_absolute():
        workdir = ROOT / workdir
    cfg["paths"]["workdir"] = workdir
    db = Path(cfg["paths"]["db"])
    cfg["paths"]["db"] = db if db.is_absolute() else ROOT / db
    cfg["_root"] = str(ROOT)
    workdir.mkdir(parents=True, exist_ok=True)
    return cfg
