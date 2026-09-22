from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

import requests

from app.providers.base import Candle, Quote


@dataclass(frozen=True)
class SymbolInfo:
    api_symbol: str
    exchange: str = "NSE"


class FyersMarketDataProvider:
    name = "fyers_v3"
    base_url = "https://api-t1.fyers.in/data"

    def __init__(self) -> None:
        self.client_id = os.getenv("FYERS_CLIENT_ID", "").strip()
        self.access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.access_token)

    def set_access_token(self, access_token: str) -> None:
        self.access_token = access_token.strip()

    def _headers(self) -> dict[str, str]:
        if not self.configured:
            raise RuntimeError("FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN are not configured.")
        return {
            "Authorization": f"{self.client_id}:{self.access_token}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict[str, str]) -> dict:
        try:
            response = requests.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self._headers(),
                timeout=20,
            )
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
        except requests.RequestException as exc:
            raise ValueError(f"FYERS request failed: {exc}") from exc

        if not response.ok:
            raise ValueError(f"FYERS API error ({response.status_code}): {response.text[:500]}")

        payload = response.json()
        if payload.get("s") == "error":
            raise ValueError(payload.get("message") or "FYERS returned an error.")
        return payload

    @staticmethod
    def symbol_info(symbol: str) -> SymbolInfo:
        symbol = symbol.strip().upper()
        aliases = {
            "NIFTY": "NSE:NIFTY50-INDEX",
            "NIFTY50": "NSE:NIFTY50-INDEX",
            "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
            "FINNIFTY": "NSE:FINNIFTY-INDEX",
            "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
        }
        if ":" in symbol:
            return SymbolInfo(symbol)
        if symbol in aliases:
            return SymbolInfo(aliases[symbol])
        return SymbolInfo(f"NSE:{symbol}-EQ")

    @staticmethod
    def resolution(timeframe: str) -> str:
        mapping = {
            "1m": "1", "3m": "3", "5m": "5", "15m": "15",
            "30m": "30", "1h": "60", "D": "D", "W": "1W", "M": "1M",
        }
        if timeframe not in mapping:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        return mapping[timeframe]

    @staticmethod
    def _chunks(start: date, end: date, days: int) -> Iterable[tuple[date, date]]:
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=days - 1), end)
            yield cursor, chunk_end
            cursor = chunk_end + timedelta(days=1)

    def get_history(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        info = self.symbol_info(symbol)
        resolution = self.resolution(timeframe)
        today = date.today()

        if resolution in {"D", "1W", "1M"}:
            lookback_days = max(limit * (35 if resolution == "1M" else 10), 366)
            chunk_days = 366
        else:
            bars_per_day = {"1": 375, "3": 125, "5": 75, "15": 25, "30": 13, "60": 7}.get(resolution, 25)
            lookback_days = min(max((limit // bars_per_day) + 10, 20), 100)
            chunk_days = 100

        start = today - timedelta(days=lookback_days)
        candles: list[Candle] = []

        for chunk_start, chunk_end in self._chunks(start, today, chunk_days):
            payload = self._get(
                "/data/history",
                {
                    "symbol": info.api_symbol,
                    "resolution": resolution,
                    "date_format": "1",
                    "range_from": chunk_start.isoformat(),
                    "range_to": chunk_end.isoformat(),
                    "cont_flag": "1",
                },
            )
            for row in payload.get("candles") or []:
                if len(row) >= 6:
                    candles.append(Candle(
                        time=int(row[0]), open=float(row[1]), high=float(row[2]),
                        low=float(row[3]), close=float(row[4]), volume=int(row[5] or 0),
                    ))

        unique = {item.time: item for item in candles}
        result = sorted(unique.values(), key=lambda item: item.time)
        if not result:
            raise ValueError(f"No historical data returned for {symbol}.")
        return result[-limit:]

    @staticmethod
    def _quote_from_value(symbol: str, value: dict) -> Quote:
        return Quote(
            symbol=symbol, exchange="NSE",
            last=float(value.get("lp", 0) or 0),
            change=float(value.get("ch", 0) or 0),
            change_percent=float(value.get("chp", 0) or 0),
            open=_number(value.get("open_price")),
            high=_number(value.get("high_price")),
            low=_number(value.get("low_price")),
            volume=_integer(value.get("volume")),
            bid=_number(value.get("bid")),
            ask=_number(value.get("ask")),
        )

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if not symbols:
            return []
        infos = [self.symbol_info(symbol) for symbol in symbols]
        payload = self._get("/data/quotes", {"symbols": ",".join(info.api_symbol for info in infos)})
        requested = {info.api_symbol.upper(): symbol for info, symbol in zip(infos, symbols)}
        result: list[Quote] = []
        for item in payload.get("d") or []:
            value = item.get("v") or {}
            api_symbol = str(item.get("symbol") or value.get("symbol") or "").upper()
            original = requested.get(api_symbol)
            if original:
                result.append(self._quote_from_value(original, value))
        return result

    def get_quote(self, symbol: str) -> Quote:
        result = self.get_quotes([symbol])
        if not result:
            raise ValueError(f"No quote returned for {symbol}.")
        return result[0]


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
