"""Время публикации клипа: ввод в локальном времени ПК, хранение в UTC."""
from __future__ import annotations

import datetime as dt

FMT = "%d.%m.%Y %H:%M"
MIN_AHEAD = dt.timedelta(minutes=5)  # ближе — YouTube может не успеть обработать видео к назначенному времени
UTC = dt.timezone.utc


def parse_local(text: str) -> dt.datetime | None:
    """'07.10.2026 18:30' (время на ПК) -> aware UTC. Пустая строка -> None. Неверный формат -> ValueError."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        naive = dt.datetime.strptime(text, FMT)
    except ValueError:
        raise ValueError("Время нужно вводить так: 07.10.2026 18:30 (день.месяц.год часы:минуты)") from None
    return naive.astimezone().astimezone(UTC)


def to_iso(d: dt.datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(s: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat((s or "").replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def format_local(iso: str | None) -> str:
    d = from_iso(iso)
    return d.astimezone().strftime(FMT) if d else ""


def pick_when(publish_at: str | None, now: dt.datetime, delay_minutes: int, k: int) -> tuple[dt.datetime | None, int]:
    """Когда выкладывать клип: (время или None = сразу, новый счётчик k).
    Заданное вручную время побеждает (если оно ещё не прошло — иначе клип уходит сразу). Остальные клипы идут по обычному
    правилу: первый сразу, каждый следующий позже на delay_minutes; k считает только клипы без своего времени."""
    fixed = from_iso(publish_at)
    if fixed:
        return (fixed, k) if fixed > now + dt.timedelta(minutes=1) else (None, k)
    when = now + dt.timedelta(minutes=delay_minutes * k + 2) if (delay_minutes and k > 0) else None
    return when, k + 1
