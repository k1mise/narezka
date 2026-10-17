"""Уборка диска: после публикации исходное видео, кэш распознавания и готовые клипы удаляются.
Удаляется только внутри рабочей папки (workdir) и только то, что точно уже не понадобится."""
from __future__ import annotations

import glob
from pathlib import Path

from .publishers.base import REAL_PLATFORMS


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _rm(p: Path, workdir: Path) -> int:
    """Удаляет файл, если он лежит в рабочей папке. Возвращает освобождённые байты."""
    try:
        if p.is_file() and _inside(p, workdir):
            n = p.stat().st_size
            p.unlink()
            return n
    except OSError:
        pass
    return 0


def _drop_clip_files(clip, workdir: Path) -> int:
    freed = 0
    for key in ("path", "final_path", "filler_path"):
        if clip[key]:
            freed += _rm(Path(clip[key]), workdir)
    base = Path(clip["path"]) if clip["path"] else None
    if base and _inside(base, workdir):
        from .filler import drop_sidecar
        drop_sidecar(base)
    if base and base.parent.exists() and _inside(base.parent, workdir):
        try:
            base.parent.rmdir()  # пустую папку задания убираем, непустую не трогаем
        except OSError:
            pass
    return freed


def _drop_source(db, job, workdir: Path) -> int:
    src = Path(job["source_path"])
    freed = _rm(src, workdir)
    if src.parent.exists() and _inside(src.parent, workdir):
        for extra in src.parent.glob(glob.escape(src.stem) + ".*"):  # кэш транскрипта, остатки загрузки
            freed += _rm(extra, workdir)
    db.clear_source(job["id"])
    return freed


def discard_clip(db, cfg: dict, clip_id: int) -> None:
    """Пользователь убрал клип из очереди: файлы удаляем, запись остаётся."""
    clip = db.clip(clip_id)
    if clip:
        _drop_clip_files(clip, cfg["paths"]["workdir"])
        db.update_clip(clip_id, status="discarded", path="", final_path="", filler_path="")


def finish_group(db, cfg: dict, group_id: int, log=print, delete_sources: bool = True,
                 only: list[int] | None = None, real_ids: set[int] | None = None) -> int:
    """Клипы, ушедшие во все реальные аккаунты группы, помечаем опубликованными и стираем.
    Исходник задания удаляем, когда в нём не осталось клипов, ждущих публикации. Возвращает освобождённые байты."""
    if not cfg.get("cleanup", {}).get("enabled", True):
        return 0
    # only/real_ids: публикация на выбранные аккаунты — клип считается выложенным, когда он ушёл на ВСЕ выбранные
    real = set(real_ids) if real_ids else {a["id"] for a in db.accounts(group_id) if a["platform"] in REAL_PLATFORMS}
    if not real:
        return 0
    workdir: Path = cfg["paths"]["workdir"]
    freed = 0
    for clip in db.pending_clips(group_id):
        if only is not None and clip["id"] not in only:
            continue
        job = db.job(clip["job_id"])
        if job and job["test"]:
            continue  # тестовые клипы не трогаем
        if real <= db.clip_published_accounts(clip["id"]):
            freed += _drop_clip_files(clip, workdir)
            db.update_clip(clip["id"], status="published", path="", final_path="", filler_path="")
    if delete_sources:
        for job in db.jobs_with_source(None):   # клип мог быть перенесён в другую группу — смотрим исходники всех заданий
            if db.clip_count(job["id"]) > 0 and db.open_clip_count(job["id"]) == 0:
                freed += _drop_source(db, job, workdir)
    if freed:
        log(f"Освобождено места: {freed / 1e6:.0f} МБ (исходники и готовые клипы удалены после публикации).")
    return freed
