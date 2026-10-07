from __future__ import annotations
import argparse

from .config import load_config
from .db import DB
from .pipeline import run


def main():
    ap = argparse.ArgumentParser(description="narez: ссылка -> вертикальные клипы")
    ap.add_argument("url")
    ap.add_argument("--group", default="Основная")
    ap.add_argument("--config")
    a = ap.parse_args()
    cfg = load_config(a.config)
    db = DB(cfg["paths"]["db"])
    _, clips = run(a.url, cfg, db, db.add_group(a.group))
    for c in clips:
        print(f"{c['path']}  |  {c['title']}")


if __name__ == "__main__":
    main()
