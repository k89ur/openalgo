from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FundamentalPoint:
    time: int
    value: float


class FundamentalsProvider(Protocol):
    name: str

    def get_historical_pe(self, symbol: str, limit: int = 40) -> list[FundamentalPoint]:
        ...
