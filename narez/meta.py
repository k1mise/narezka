"""Заголовок / описание / хэштеги: разбор и сборка.

В БД описание (reason) и хэштеги (tags) хранятся раздельно, чтобы их можно было править по отдельности;
на публикации они склеиваются обратно."""
from __future__ import annotations

import re


def normalize_tags(text: str) -> str:
    """'gta5, #лайфхаки  плохой тег' -> '#gta5 #лайфхаки #плохой #тег' (без повторов, не больше 15)."""
    seen: list[str] = []
    for t in re.findall(r"#?[\w\-]+", text or "", flags=re.U):
        t = "#" + t.lstrip("#")
        if t.lower() not in (x.lower() for x in seen):
            seen.append(t)
    return " ".join(seen[:15])


def split_hashtags(text: str) -> tuple[str, str]:
    """Отделяет хвост из хэштегов (последние строки, где одни #теги) от основного текста описания."""
    lines = (text or "").rstrip().split("\n")
    tags: list[str] = []
    while lines and lines[-1].strip() and all(t.startswith("#") for t in lines[-1].split()):
        tags.insert(0, lines.pop().strip())
    return "\n".join(lines).strip(), " ".join(tags)


def compose_description(body: str, tags: str) -> str:
    return "\n\n".join(p for p in ((body or "").strip(), (tags or "").strip()) if p)


def api_tags(tags: str, limit: int = 450) -> list[str]:
    """Теги для поля snippet.tags в YouTube API (без #, суммарно не длиннее лимита)."""
    out, total = [], 0
    for t in (tags or "").split():
        t = t.lstrip("#")
        if t and total + len(t) + 1 <= limit:
            out.append(t)
            total += len(t) + 1
    return out
