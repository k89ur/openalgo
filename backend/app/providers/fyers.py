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
    # History is shared by chart, watchlist and Pipscript consumers.
    _history_cache: dict[tuple[str, str], tuple[float, date, date, list[Candle]]] = {}
    _history_cache_lock = threading.Lock()
    _history_inflight: dict[tuple, threading.Event] = {}
    _history_cache_ttl_seconds = 300.0

    # Bound actual FYERS calls so concurrent browser requests cannot create a
    # burst of upstream traffic.
    _history_upstream_semaphore = threading.BoundedSemaphore(2)
    _history_request_lock = threading.Lock()
    _history_last_upstream_request = 0.0
    _history_min_interval_seconds = 0.20
    _history_refresh_threads: set[threading.Thread] = set()

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
            "CNXMETAL": "NSE:NIFTYMETAL-INDEX",
            "CNXPHARMA": "NSE:NIFTYPHARMA-INDEX",
            "NIFTY_IPO": "NSE:NIFTYIPO-INDEX",
            "NIFTY_IND_DEFENCE": "NSE:NIFTYINDDEFENCE-INDEX",
            "NIFTY_HEALTHCARE": "NSE:NIFTYHEALTHCARE-INDEX",
            "NIFTY_CAPITAL_MKT": "NSE:NIFTYCAPITALMKT-INDEX",
            "CNXREALTY": "NSE:NIFTYREALTY-INDEX",
            "NIFTY_CONSR_DURBL": "NSE:NIFTYCONSRDURBL-INDEX",
            "NIFTY_EV": "NSE:NIFTYEV-INDEX",
            "NIFTY_TRANS_LOGIS": "NSE:NIFTYTRANSLOGIS-INDEX",
            "CNXENERGY": "NSE:NIFTYENERGY-INDEX",
            "CNXAUTO": "NSE:NIFTYAUTO-INDEX",
            "CNXPSUBANK": "NSE:NIFTYPSUBANK-INDEX",
            "NIFTY_IND_DIGITAL": "NSE:NIFTYINDDIGITAL-INDEX",
            "NIFTYPVTBANK": "NSE:NIFTYPVTBANK-INDEX",
            "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
            "CNXCONSUMPTION": "NSE:NIFTYCONSUMPTION-INDEX",
            "CNXPSE": "NSE:NIFTYPSE-INDEX",
            "CPSE": "NSE:NIFTYCPSE-INDEX",
            "NIFTY_IND_TOURISM": "NSE:NIFTYINDTOURISM-INDEX",
            "CNXINFRA": "NSE:NIFTYINFRA-INDEX",
            "NIFTY_OIL_AND_GAS": "NSE:NIFTYOILANDGAS-INDEX",
            "CNXFINANCE": "NSE:NIFTYFINANCIALSERVICES-INDEX",
            "CNXSERVICE": "NSE:NIFTYSERVSECTOR-INDEX",
            "CNXIT": "NSE:NIFTYIT-INDEX",
            "CNXFMCG": "NSE:NIFTYFMCG-INDEX",
            "NIFTY_CEMENT": "NSE:NIFTYCEMENT-INDEX",
            "NIFTY_CHEMICALS": "NSE:NIFTYCHEMICALS-INDEX",
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

    @staticmethod
    def _is_retryable_history_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(token in message for token in (
            "429", "rate limit", "rate-limit", "too many request",
            "too many requests", "temporarily unavailable", "timeout",
            "timed out", "connection reset", "connection aborted",
            "connection error",
        ))

    def _history_upstream_call(self, client, payload: dict) -> dict:
        with self._history_upstream_semaphore:
            for attempt in range(3):
                with self._history_request_lock:
                    now = time.monotonic()
                    wait = self._history_min_interval_seconds - (
                        now - self._history_last_upstream_request
                    )
                    if wait > 0:
                        time.sleep(wait)
                    self._history_last_upstream_request = time.monotonic()

                try:
                    response = client.history(data=payload)
                    return self._check_response(response, "history")
                except Exception as exc:
                    if attempt >= 2 or not self._is_retryable_history_error(exc):
                        raise
                    time.sleep(0.5 * (2 ** attempt))

        raise RuntimeError("FYERS history request failed after retries.")

    def _fetch_history_and_cache(
        self,
        *,
        client,
        info: SymbolInfo,
        resolution: str,
        limit: int,
        start: date,
        end: date,
        chunk_days: int,
        inflight_key: tuple,
    ) -> list[Candle]:
        try:
            chunks = list(self._chunks(start, end, chunk_days))

            def load_chunk(chunk: tuple[date, date]) -> list[Candle]:
                chunk_start, chunk_end = chunk
                payload = self._history_upstream_call(
                    client,
                    {
                        "symbol": info.api_symbol,
                        "resolution": resolution,
                        "date_format": "1",
                        "range_from": chunk_start.isoformat(),
                        "range_to": chunk_end.isoformat(),
                        "cont_flag": "1",
                    },
                )
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
                raise ValueError(f"No historical data returned for {info.api_symbol}.")

            with self._history_cache_lock:
                self._history_cache[(info.api_symbol.upper(), resolution)] = (
                    time.monotonic(),
                    start,
                    end,
                    list(result),
                )
            return result
        finally:
            with self._history_cache_lock:
                event = self._history_inflight.pop(inflight_key, None)
                if event is not None:
                    event.set()

    def _start_background_refresh(
        self,
        *,
        client,
        info: SymbolInfo,
        resolution: str,
        limit: int,
        start: date,
        end: date,
        chunk_days: int,
        inflight_key: tuple,
    ) -> None:
        def refresh() -> None:
            try:
                self._fetch_history_and_cache(
                    client=client,
                    info=info,
                    resolution=resolution,
                    limit=limit,
                    start=start,
                    end=end,
                    chunk_days=chunk_days,
                    inflight_key=inflight_key,
                )
            except Exception:
                with self._history_cache_lock:
                    self._history_inflight.pop(inflight_key, None)
            finally:
                with self._history_cache_lock:
                    self._history_refresh_threads.discard(threading.current_thread())

        thread = threading.Thread(
            target=refresh,
            name="pipsgox-history-refresh",
            daemon=True,
        )
        self._history_refresh_threads.add(thread)
        thread.start()

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

        cache_key = (info.api_symbol.upper(), resolution)
        inflight_key = (
            info.api_symbol.upper(),
            resolution,
            start.isoformat(),
            end.isoformat(),
        )

        # A larger cached dataset can satisfy a smaller request immediately.
        with self._history_cache_lock:
            cached = self._history_cache.get(cache_key)
            inflight = self._history_inflight.get(inflight_key)
            if cached:
                cached_at, cached_start, cached_end, cached_bars = cached
                covers_request = cached_start <= start and cached_end >= end
                if covers_request:
                    result = list(cached_bars[-limit:])
                    if time.monotonic() - cached_at < self._history_cache_ttl_seconds:
                        return result

                    # Stale-while-refresh keeps an interactive chart responsive.
                    if inflight is None:
                        self._history_inflight[inflight_key] = threading.Event()
                        self._start_background_refresh(
                            client=client,
                            info=info,
                            resolution=resolution,
                            limit=max(limit, len(cached_bars)),
                            start=start,
                            end=end,
                            chunk_days=chunk_days,
                            inflight_key=inflight_key,
                        )
                    return result

            if inflight is None:
                self._history_inflight[inflight_key] = threading.Event()
                owner = True
            else:
                owner = False
                event = inflight

        if not owner:
            event.wait(timeout=30.0)
            with self._history_cache_lock:
                cached = self._history_cache.get(cache_key)
                if cached:
                    _, cached_start, cached_end, cached_bars = cached
                    if cached_start <= start and cached_end >= end:
                        return list(cached_bars[-limit:])

                if inflight_key not in self._history_inflight:
                    self._history_inflight[inflight_key] = threading.Event()
                    owner = True
                else:
                    raise ValueError(f"Historical data request is still unavailable for {symbol}.")

        return self._fetch_history_and_cache(
            client=client,
            info=info,
            resolution=resolution,
            limit=limit,
            start=start,
            end=end,
            chunk_days=chunk_days,
            inflight_key=inflight_key,
        )

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
