from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Moment:
    start: float
    end: float
    title: str
    reason: str = ""
    score: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start
