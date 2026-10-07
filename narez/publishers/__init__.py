"""Этап 2: публикация. Пока — интерфейс и «сухой» издатель, который только пишет в лог."""
from __future__ import annotations

from .base import DryRunPublisher, Publisher, get_publisher

__all__ = ["Publisher", "DryRunPublisher", "get_publisher"]
