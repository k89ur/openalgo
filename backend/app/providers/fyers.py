from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from fyers_apiv3 import fyersModel

from app.providers.base import Candle, Quote


@dataclass(frozen=True)
class SymbolInfo:
    api_symbol: str
    exchange: str = "NSE"


class FyersMarketDataProvider:
    name = "fyers_v3_sdk"

    def __init__(self) -> None:
        self.client_id = os.getenv("FYERS_CLIENT_ID", "").strip()
        self.access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()
        self._client = None
        self._build_client()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.access_token)

    def set_access_token(self, access_token: str) -> None:
        self.access_token = access_token.strip()
        self._build_client()

    def _build_client(self) -> None:
        if not self.client_id or not self.access_token:
            self._client = None
            return
        self._client = fyersModel.FyersModel(
            client_id=self.client_id,
            token=self.access_token,
            is_async=False,
            log_path="",
        )

    def _require_client(self):
        if self._client is None:
            raise ValueError(
                "FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN are not configured."
            )
        return self._client

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
        return SymbolInfo(aliases.get(symbol, f"NSE:{symbol}-EQ"))

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

    @staticmethod
    def _check_response(payload: dict, operation: str) -> dict:
        if not isinstance(payload, dict):
            raise ValueError(f"FYERS {operation} returned an invalid response.")
        if payload.get("s") == "error":
            raise ValueError(
                payload.get("message")
                or f"FYERS {operation} failed (code {payload.get('code', 'unknown')})."
            )
        return payload

    def get_history(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        client = self._require_client()
        info = self.symbol_info(symbol)
        resolution = self.resolution(timeframe)
        today = date.today()

        if resolution in {"D", "1W", "1M"}:
            # Estimate calendar days needed for the requested number of bars.
            # FYERS allows up to 366 days per daily/weekly/monthly request,
            # so avoid making dozens of unnecessary requests for old data.
            days_per_bar = {"D": 2, "1W": 8, "1M": 32}[resolution]
            lookback_days = max(limit * days_per_bar, 366)
            chunk_days = 366
        else:
            bars_per_day = {
                "1": 375, "3": 125, "5": 75, "15": 25,
                "30": 13, "60": 7,
            }.get(resolution, 25)
            lookback_days = min(max((limit // bars_per_day) + 10, 20), 100)
            chunk_days = 100

        start = today - timedelta(days=lookback_days)
        candles: list[Candle] = []

        for chunk_start, chunk_end in self._chunks(start, today, chunk_days):
            payload = client.history(data={
                "symbol": info.api_symbol,
                "resolution": resolution,
                "date_format": "1",
                "range_from": chunk_start.isoformat(),
                "range_to": chunk_end.isoformat(),
                "cont_flag": "1",
            })
            payload = self._check_response(payload, "history")
            for row in payload.get("candles") or []:
                if len(row) >= 6:
                    candles.append(
                        Candle(
                            time=int(row[0]),
                            open=float(row[1]),
                            high=float(row[2]),
                            low=float(row[3]),
                            close=float(row[4]),
                            volume=int(row[5] or 0),
                        )
                    )

        unique = {item.time: item for item in candles}
        result = sorted(unique.values(), key=lambda item: item.time)
        if not result:
            raise ValueError(f"No historical data returned for {symbol}.")
        return result[-limit:]

    @staticmethod
    def _quote_from_value(symbol: str, value: dict) -> Quote:
        return Quote(
            symbol=symbol,
            exchange="NSE",
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
        client = self._require_client()
        if not symbols:
            return []

        infos = [self.symbol_info(symbol) for symbol in symbols]
        payload = client.quotes(
            data={"symbols": ",".join(info.api_symbol for info in infos)}
        )
        payload = self._check_response(payload, "quotes")

        requested = {
            info.api_symbol.upper(): symbol
            for info, symbol in zip(infos, symbols)
        }

        result: list[Quote] = []
        for item in payload.get("d") or []:
            value = item.get("v") or {}
            api_symbol = str(
                item.get("symbol") or value.get("symbol") or ""
            ).upper()
            original = requested.get(api_symbol)
            if original:
                result.append(self._quote_from_value(original, value))

        if not result and payload.get("d"):
            raise ValueError(
                f"FYERS returned quote data, but no requested symbols matched: "
                f"{', '.join(symbols)}"
            )
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
