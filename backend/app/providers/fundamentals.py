from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FundamentalPoint:
    time: int
    value: float


@dataclass(frozen=True)
class HistoricalEpsPoint:
    time: int
    ttm_eps: float


class FundamentalsProvider(Protocol):
    name: str

    def get_historical_eps(self, symbol: str, limit: int = 40) -> list[HistoricalEpsPoint]:
        ...
