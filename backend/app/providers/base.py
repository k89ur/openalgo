from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Quote:
    symbol: str
    exchange: str
    last: float
    change: float
    change_percent: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    bid: float | None = None
    ask: float | None = None


class MarketDataProvider(Protocol):
    name: str

    def get_history(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        ...

    def get_quote(self, symbol: str) -> Quote:
        ...
