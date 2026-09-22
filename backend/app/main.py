from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Literal

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="PIPSGOX API", version="0.3.0")

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
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    source: str = "Upstox V3"


UPSTOX_BASE = "https://api.upstox.com/v3"
UPSTOX_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
SYMBOL_CACHE: dict[str, str] = {}


def require_upstox() -> None:
    if not UPSTOX_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="UPSTOX_ACCESS_TOKEN is not configured. Add it as a Codespaces secret/environment variable.",
        )


def upstox_get(path: str, params: dict[str, str]) -> dict:
    require_upstox()
    response = requests.get(
        f"{UPSTOX_BASE}{path}",
        params=params,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {UPSTOX_TOKEN}",
        },
        timeout=20,
    )
    if not response.ok:
        detail = response.text[:500]
        raise HTTPException(
            status_code=502,
            detail=f"Upstox API error ({response.status_code}): {detail}",
        )
    return response.json()


def resolve_instrument(symbol: str) -> str:
    symbol = symbol.upper().strip()
    cached = SYMBOL_CACHE.get(symbol)
    if cached:
        return cached

    payload = upstox_get(
        "/instruments/search",
        {
            "query": symbol,
            "exchanges": "NSE",
            "segments": "EQ",
            "page_number": "1",
            "records": "30",
        },
    )

    matches = payload.get("data") or []
    exact = [
        item for item in matches
        if item.get("segment") == "NSE_EQ"
        and item.get("instrument_type") == "EQ"
        and str(item.get("trading_symbol", "")).upper() == symbol
    ]
    chosen = exact[0] if exact else next(
        (
            item for item in matches
            if item.get("segment") == "NSE_EQ"
            and item.get("instrument_type") == "EQ"
        ),
        None,
    )

    if not chosen:
        raise HTTPException(status_code=404, detail=f"NSE equity symbol not found: {symbol}")

    key = chosen["instrument_key"]
    SYMBOL_CACHE[symbol] = key
    return key


def timeframe_request(timeframe: Timeframe) -> tuple[str, str]:
    mapping = {
        "1m": ("minutes", "1"),
        "3m": ("minutes", "3"),
        "5m": ("minutes", "5"),
        "15m": ("minutes", "15"),
        "30m": ("minutes", "30"),
        "1h": ("hours", "1"),
        "D": ("days", "1"),
        "W": ("weeks", "1"),
        "M": ("months", "1"),
    }
    return mapping[timeframe]


def default_from_date(timeframe: Timeframe, limit: int) -> date:
    today = date.today()
    if timeframe in {"D"}:
        return today - timedelta(days=max(limit * 2, 365))
    if timeframe == "W":
        return today - timedelta(days=max(limit * 10, 3650))
    if timeframe == "M":
        return today - timedelta(days=max(limit * 35, 3650))
    if timeframe == "1h":
        return today - timedelta(days=max(limit // 5, 90))
    return today - timedelta(days=max(limit // 4, 30))


def parse_candles(payload: dict, limit: int) -> list[Candle]:
    raw = payload.get("data", {}).get("candles") or []
    candles: list[Candle] = []

    for row in raw:
        if len(row) < 6:
            continue
        timestamp = row[0]
        if isinstance(timestamp, str):
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            epoch = int(dt.timestamp())
        else:
            epoch = int(timestamp)

        candles.append(
            Candle(
                time=epoch,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=int(row[5] or 0),
            )
        )

    candles.sort(key=lambda item: item.time)
    return candles[-limit:]


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": "pipsgox",
        "data_provider": "upstox_v3",
        "configured": "true" if UPSTOX_TOKEN else "false",
    }


@app.get("/api/history", response_model=list[Candle])
def history(
    symbol: str = Query(default="BHARTIARTL", min_length=1, max_length=40),
    timeframe: Timeframe = "D",
    limit: int = Query(default=260, ge=50, le=2000),
) -> list[Candle]:
    unit, interval = timeframe_request(timeframe)
    instrument_key = resolve_instrument(symbol)
    from_date = default_from_date(timeframe, limit)
    to_date = date.today()

    # Upstox V3 has different maximum retrieval windows by unit.
    if unit == "minutes":
        from_date = max(from_date, to_date - timedelta(days=30))
    elif unit == "hours":
        from_date = max(from_date, to_date - timedelta(days=90))
    elif unit == "days":
        from_date = max(from_date, to_date - timedelta(days=3650))

    payload = upstox_get(
        f"/historical-candle/{requests.utils.quote(instrument_key, safe='')}/{unit}/{interval}/{to_date.isoformat()}/{from_date.isoformat()}",
        {},
    )
    candles = parse_candles(payload, limit)
    if not candles:
        raise HTTPException(status_code=404, detail=f"No historical data returned for {symbol.upper()}")
    return candles


@app.get("/api/quote", response_model=Quote)
def quote(
    symbol: str = Query(default="BHARTIARTL", min_length=1, max_length=40),
) -> Quote:
    symbol = symbol.upper().strip()
    instrument_key = resolve_instrument(symbol)

    ltp_payload = upstox_get(
        "/market-quote/ltp",
        {"instrument_key": instrument_key},
    )
    data = ltp_payload.get("data") or {}
    item = next(iter(data.values()), None)
    if not item:
        raise HTTPException(status_code=404, detail=f"No quote returned for {symbol}")

    last = float(item.get("last_price", 0))
    previous_close = float(item.get("cp", 0))
    change = last - previous_close
    change_percent = (change / previous_close * 100) if previous_close else 0.0

    daily = history(symbol=symbol, timeframe="D", limit=2)
    current = daily[-1]

    return Quote(
        symbol=symbol,
        exchange="NSE",
        last=last,
        change=change,
        change_percent=change_percent,
        open=current.open,
        high=current.high,
        low=current.low,
        volume=current.volume,
    )
