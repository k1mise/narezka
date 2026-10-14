"""Фоновые задачи веб-интерфейса: нарезка, публикация, вход в YouTube и т. д. Состояние хранится в памяти процесса."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Busy(RuntimeError):
    pass


class Task:
    def __init__(self, kind: str, title: str, store: Path | None = None):
        self.store = store
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.title = kind, title
        self.status = "running"          # running / done / error
        self.log: list[str] = []
        self.result: dict = {}
        self.error = ""
        self.started, self.finished = time.time(), 0.0

    def line(self, msg) -> None:
        text = _ANSI.sub("", str(msg))
        self.log.append(text)
        if self.store:      # лог пишется на диск сразу: он переживает перезапуск программы и закрытие страницы
            try:
                with open(self.store / f"{self.id}.log", "a", encoding="utf-8") as f:
                    f.write(text.replace("\n", "\n  ") + "\n")
            except OSError:
                pass

    def save(self) -> None:
        if not self.store:
            return
        try:
            meta = {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status, "error": self.error,
                    "result": self.result, "started": self.started, "finished": self.finished}
            (self.store / f"{self.id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        except (OSError, TypeError):
            pass

    def public(self, after: int | None = None) -> dict:
        d = {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status, "error": self.error,
             "result": self.result, "started": self.started, "finished": self.finished,
             "last": self.log[-1] if self.log else ""}
        if after is not None:
            d["log"], d["next"] = self.log[after:], len(self.log)
        return d


class TaskManager:
    def __init__(self, keep: int = 60, store: Path | None = None):
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self.keep = keep
        self.store = Path(store) if store else None
        if self.store:
            self.store.mkdir(parents=True, exist_ok=True)
            self._load()

    def _load(self) -> None:
        """Подхватывает журнал прошлых запусков; задача, которая не успела завершиться, помечается прерванной."""
        metas = []
        for f in self.store.glob("*.json"):
            try:
                metas.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        metas.sort(key=lambda m: m.get("started", 0))
        for m in metas[:-self.keep]:                         # старые журналы удаляем
            for ext in ("json", "log"):
                (self.store / f"{m.get('id')}.{ext}").unlink(missing_ok=True)
        for m in metas[-self.keep:]:
            t = Task(m.get("kind", ""), m.get("title", ""), self.store)
            t.id, t.status, t.error = m["id"], m.get("status", "error"), m.get("error", "")
            t.result, t.started, t.finished = m.get("result") or {}, m.get("started", 0), m.get("finished", 0)
            try:
                t.log = (self.store / f"{t.id}.log").read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                t.log = []
            if t.status == "running":
                t.status, t.error, t.finished = "error", "Прервана: программу закрыли или перезапустили до завершения.", t.started
                t.log.append("ОШИБКА: " + t.error)
                t.save()
            self._tasks[t.id] = t

    def running(self, *kinds: str) -> bool:
        return any(t.status == "running" and (not kinds or t.kind in kinds) for t in self._tasks.values())

    def start(self, kind: str, title: str, fn, exclusive: bool = False) -> Task:
        """fn(log) -> dict | None выполняется в отдельном потоке. exclusive: не больше одной такой задачи сразу."""
        with self._lock:
            if exclusive and self.running(kind):
                raise Busy("Такая задача уже выполняется — дождись её завершения.")
            t = Task(kind, title, self.store)
            self._tasks[t.id] = t
            t.save()
            for old in sorted(self._tasks.values(), key=lambda x: x.started)[:-self.keep]:
                if old.status != "running":
                    self._tasks.pop(old.id, None)
                    if self.store:
                        for ext in ("json", "log"):
                            (self.store / f"{old.id}.{ext}").unlink(missing_ok=True)

        def work():
            try:
                t.result = fn(t.line) or {}
                t.status = "done"
            except Exception as e:  # ошибка показывается пользователю как есть, без ANSI-кодов
                t.error = _ANSI.sub("", str(e)) or e.__class__.__name__
                t.line(f"ОШИБКА: {t.error}")
                t.status = "error"
            t.finished = time.time()
            t.save()
        threading.Thread(target=work, daemon=True).start()
        return t

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def recent(self, limit: int = 20) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda x: x.started, reverse=True)[:limit]
