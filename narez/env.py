"""Минимальный загрузчик .env (без зависимостей): KEY=VALUE, комментарии с #, значения в кавычках."""
from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> bool:
    """Читает .env и кладёт значения в os.environ (уже заданные переменные не перезаписывает).
    Возвращает True, если файл найден."""
    p = Path(path)
    if not p.is_file():
        return False
    for raw in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            os.environ.setdefault(key, val)
    return True
