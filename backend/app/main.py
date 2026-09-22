from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="PIPSGOX API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Timeframe = Literal["1m", "3m", "5m", "15m", "30m", "1h", "D", "W", "M"]


class Candle(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int


class Quote(BaseModel):
    symbol: str
    exchange: str
    last: float
    change: float
    change_percent: float


def timeframe_minutes(timeframe: Timeframe) -> int:
    return {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "D": 1440,
        "W": 10080,
        "M": 43200,
    }[timeframe]


def seed_for(symbol: str) -> int:
    return sum((index + 1) * ord(char) for index, char in enumerate(symbol))


def generate_history(symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
    seed = seed_for(symbol)
    step = timeframe_minutes(timeframe)
    base = 1350 + (seed % 700)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    result: list[Candle] = []
    close = float(base)

    for index in range(limit):
        angle = (index + seed % 37) / 11.0
        trend = math.sin(index / 28.0 + seed / 1000.0) * (4.0 if step < 1440 else 10.0)
        impulse = math.sin(angle) * (2.5 if step < 1440 else 6.0)
        open_price = close
        close = max(10.0, open_price + trend * 0.35 + impulse)
        high = max(open_price, close) + 2.0 + abs(math.sin(index)) * 5.0
        low = min(open_price, close) - 2.0 - abs(math.cos(index)) * 5.0
        volume = int(500_000 + abs(math.sin(index / 5.0)) * 1_600_000 + (seed % 9) * 25_000)

        candle_time = now - timedelta(minutes=step * (limit - 1 - index))
        result.append(
            Candle(
                time=int(candle_time.timestamp()),
                open=round(open_price, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=volume,
            )
        )

    return result


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "pipsgox"}


@app.get("/api/history", response_model=list[Candle])
def history(
    symbol: str = Query(default="BHARTIARTL", min_length=1, max_length=40),
    timeframe: Timeframe = "D",
    limit: int = Query(default=260, ge=50, le=2000),
) -> list[Candle]:
    return generate_history(symbol.upper(), timeframe, limit)


@app.get("/api/quote", response_model=Quote)
def quote(symbol: str = Query(default="BHARTIARTL", min_length=1, max_length=40)) -> Quote:
    candles = generate_history(symbol.upper(), "D", 2)
    previous = candles[-2].close
    last = candles[-1].close
    change = last - previous
    change_percent = (change / previous) * 100 if previous else 0.0
    return Quote(
        symbol=symbol.upper(),
        exchange="NSE",
        last=round(last, 2),
        change=round(change, 2),
        change_percent=round(change_percent, 2),
    )
