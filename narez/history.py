"""Импорт уже опубликованных видео в историю (те, что были загружены до появления вкладки «История»)."""
from __future__ import annotations

import re
from pathlib import Path

from .editor import make_thumb

_ID = re.compile(r"(?:shorts/|v=|youtu\.be/|embed/)([\w-]{11})|(?<![\w-])([\w-]{11})(?![\w-])")


def parse_video_ids(text: str) -> list[str]:
    """Достаёт ID видео из ссылок (youtube.com/shorts/ID, watch?v=ID, youtu.be/ID) или голых ID; без повторов."""
    seen: list[str] = []
    for line in re.split(r"[\s,;]+", text or ""):
        if not line:
            continue
        m = _ID.search(line)
        vid = (m.group(1) or m.group(2)) if m else None
        if vid and vid not in seen:
            seen.append(vid)
    return seen


def import_videos(db, cfg: dict, publisher, account_id: int, group_id: int | None, ids: list[str],
                  log=print, thumb_fn=make_thumb) -> int:
    """Тянет название и статистику (videos.list: 1 ед. квоты на 50 видео), качает превью и добавляет видео в историю."""
    known = db.known_video_ids("youtube")
    new = [i for i in ids if i not in known]
    if len(new) < len(ids):
        log(f"Уже в истории: {len(ids) - len(new)} — пропускаю.")
    if not new:
        return 0
    stats = publisher.fetch_stats(account_id, new)
    thumbs = cfg["paths"]["workdir"] / "thumbs"
    added = 0
    for vid in new:
        s = stats[vid]
        if s["privacy"] == "deleted":
            log(f"{vid}: видео не найдено (удалено или нет доступа с этого аккаунта) — пропускаю.")
            continue
        th = thumb_fn(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", thumbs / f"yt_{vid}.png", at=0.0, vertical=True)
        uploaded = s["published"][:19].replace("T", " ") if s.get("published") else None
        pid = db.add_publication(None, account_id, group_id, "youtube", f"https://youtube.com/shorts/{vid}",
                                 s["title"] or vid, str(th or ""), "", uploaded)
        db.update_stats(pid, s["views"], s["likes"], s["comments"], s["privacy"], s["publish_at"] or None)
        added += 1
    return added
