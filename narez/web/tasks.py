"""Фоновые задачи веб-интерфейса: нарезка, публикация, вход в YouTube и т. д. Состояние хранится в памяти процесса."""
from __future__ import annotations

import re
import threading
import time
import uuid

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Busy(RuntimeError):
    pass


class Task:
    def __init__(self, kind: str, title: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.title = kind, title
        self.status = "running"          # running / done / error
        self.log: list[str] = []
        self.result: dict = {}
        self.error = ""
        self.started, self.finished = time.time(), 0.0

    def line(self, msg) -> None:
        self.log.append(_ANSI.sub("", str(msg)))

    def public(self, after: int | None = None) -> dict:
        d = {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status, "error": self.error,
             "result": self.result, "started": self.started, "finished": self.finished,
             "last": self.log[-1] if self.log else ""}
        if after is not None:
            d["log"], d["next"] = self.log[after:], len(self.log)
        return d


class TaskManager:
    def __init__(self, keep: int = 60):
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self.keep = keep

    def running(self, *kinds: str) -> bool:
        return any(t.status == "running" and (not kinds or t.kind in kinds) for t in self._tasks.values())

    def start(self, kind: str, title: str, fn, exclusive: bool = False) -> Task:
        """fn(log) -> dict | None выполняется в отдельном потоке. exclusive: не больше одной такой задачи сразу."""
        with self._lock:
            if exclusive and self.running(kind):
                raise Busy("Такая задача уже выполняется — дождись её завершения.")
            t = Task(kind, title)
            self._tasks[t.id] = t
            for old in sorted(self._tasks.values(), key=lambda x: x.started)[:-self.keep]:
                if old.status != "running":
                    self._tasks.pop(old.id, None)

        def work():
            try:
                t.result = fn(t.line) or {}
                t.status = "done"
            except Exception as e:  # ошибка показывается пользователю как есть, без ANSI-кодов
                t.error = _ANSI.sub("", str(e)) or e.__class__.__name__
                t.line(f"ОШИБКА: {t.error}")
                t.status = "error"
            t.finished = time.time()
        threading.Thread(target=work, daemon=True).start()
        return t

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def recent(self, limit: int = 20) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda x: x.started, reverse=True)[:limit]
