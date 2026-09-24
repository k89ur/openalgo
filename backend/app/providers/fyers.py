from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

from fyers_apiv3 import fyersModel

from app.providers.base import Candle, Quote


@dataclass(frozen=True)
class SymbolInfo:
    api_symbol: str
    exchange: str = "NSE"


class FyersMarketDataProvider:
    name = "fyers_v3_sdk"
    _history_cache: dict[tuple, tuple[float, list[Candle]]] = {}
    _history_cache_lock = threading.Lock()
    _history_cache_ttl_seconds = 300.0

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
            # User-selected NSE indices for PIPSGOX RS calculations.
            "CNXMETAL": "NSE:CNXMETAL-INDEX",
            "CNXPHARMA": "NSE:CNXPHARMA-INDEX",
            "NIFTY_IPO": "NSE:NIFTY_IPO-INDEX",
            "NIFTY_IND_DEFENCE": "NSE:NIFTY_IND_DEFENCE-INDEX",
            "NIFTY_HEALTHCARE": "NSE:NIFTY_HEALTHCARE-INDEX",
            "NIFTY_CAPITAL_MKT": "NSE:NIFTY_CAPITAL_MKT-INDEX",
            "CNXREALTY": "NSE:CNXREALTY-INDEX",
            "NIFTY_CONSR_DURBL": "NSE:NIFTY_CONSR_DURBL-INDEX",
            "NIFTY_EV": "NSE:NIFTY_EV-INDEX",
            "NIFTY_TRANS_LOGIS": "NSE:NIFTY_TRANS_LOGIS-INDEX",
            "CNXENERGY": "NSE:CNXENERGY-INDEX",
            "CNXAUTO": "NSE:CNXAUTO-INDEX",
            "CNXPSUBANK": "NSE:CNXPSUBANK-INDEX",
            "NIFTY_IND_DIGITAL": "NSE:NIFTY_IND_DIGITAL-INDEX",
            "NIFTYPVTBANK": "NSE:NIFTYPVTBANK-INDEX",
            "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
            "CNXCONSUMPTION": "NSE:CNXCONSUMPTION-INDEX",
            "CNXPSE": "NSE:CNXPSE-INDEX",
            "CPSE": "NSE:CPSE-INDEX",
            "NIFTY_IND_TOURISM": "NSE:NIFTY_IND_TOURISM-INDEX",
            "CNXINFRA": "NSE:CNXINFRA-INDEX",
            "NIFTY_OIL_AND_GAS": "NSE:NIFTY_OIL_AND_GAS-INDEX",
            "CNXFINANCE": "NSE:CNXFINANCE-INDEX",
            "CNXSERVICE": "NSE:CNXSERVICE-INDEX",
            "CNXIT": "NSE:CNXIT-INDEX",
            "CNXFMCG": "NSE:CNXFMCG-INDEX",
            "NIFTY_CEMENT": "NSE:NIFTY_CEMENT-INDEX",
            "NIFTY_CHEMICALS": "NSE:NIFTY_CHEMICALS-INDEX",
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

    def validate_session(self) -> None:
        """Validate the current FYERS access token with an authenticated API call."""
        client = self._require_client()
        payload = client.get_profile()
        self._check_response(payload, "profile")

    def get_history(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Candle]:
        client = self._require_client()
        info = self.symbol_info(symbol)
        resolution = self.resolution(timeframe)
        today = date.today()
        end = end or today

        if start is None:
            if resolution in {"D", "1W", "1M"}:
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
            start = end - timedelta(days=lookback_days)
        else:
            if start > end:
                raise ValueError("History start date must be on or before end date.")
            chunk_days = 366 if resolution in {"D", "1W", "1M"} else 100

        cache_key = (
            info.api_symbol.upper(),
            resolution,
            start.isoformat(),
            end.isoformat(),
            limit,
        )
        now = time.monotonic()
        with self._history_cache_lock:
            cached = self._history_cache.get(cache_key)
            if cached and now - cached[0] < self._history_cache_ttl_seconds:
                return list(cached[1])
            if cached:
                self._history_cache.pop(cache_key, None)

        chunks = list(self._chunks(start, end, chunk_days))

        def load_chunk(chunk: tuple[date, date]) -> list[Candle]:
            chunk_start, chunk_end = chunk
            payload = client.history(data={
                "symbol": info.api_symbol,
                "resolution": resolution,
                "date_format": "1",
                "range_from": chunk_start.isoformat(),
                "range_to": chunk_end.isoformat(),
                "cont_flag": "1",
            })
            payload = self._check_response(payload, "history")
            result: list[Candle] = []
            for row in payload.get("candles") or []:
                if len(row) >= 6:
                    result.append(
                        Candle(
                            time=int(row[0]),
                            open=float(row[1]),
                            high=float(row[2]),
                            low=float(row[3]),
                            close=float(row[4]),
                            volume=int(row[5] or 0),
                        )
                    )
            return result

        # Daily/weekly/monthly history is split into multiple FYERS date
        # ranges. Fetch those independent ranges concurrently so one chart
        # switch does not wait for several sequential round trips.
        candles: list[Candle] = []
        worker_count = min(4, len(chunks))
        if worker_count <= 1:
            for chunk in chunks:
                candles.extend(load_chunk(chunk))
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [executor.submit(load_chunk, chunk) for chunk in chunks]
                for future in as_completed(futures):
                    candles.extend(future.result())

        unique = {item.time: item for item in candles}
        result = sorted(unique.values(), key=lambda item: item.time)[-limit:]
        if not result:
            raise ValueError(f"No historical data returned for {symbol}.")

        with self._history_cache_lock:
            self._history_cache[cache_key] = (time.monotonic(), list(result))
        return result

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
