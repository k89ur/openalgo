from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dt_time, timedelta, timezone

from app import broker_accounts

# Termux installations may not include the system IANA tzdata package.
# India uses a fixed UTC+05:30 offset year-round, so avoid a ZoneInfo
# dependency for the scanner's market-session clock.
IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)
EOD_START = dt_time(15, 35)
REFRESH_SECONDS = 60
HISTORY_LIMIT = 260
TREND_POINTS = 21


def _now() -> datetime:
    return datetime.now(IST)


def _is_weekday(day: date) -> bool:
    return day.weekday() < 5


def _market_open(now: datetime) -> bool:
    return _is_weekday(now.date()) and MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _after_market(now: datetime) -> bool:
    return _is_weekday(now.date()) and now.time() >= EOD_START


def _distance(price: float, ma: float) -> float:
    return ((price - ma) / ma) * 100.0 if ma else 0.0


class MA44Scanner:
    """Background, broker-account-bound 44 SMA scanner.

    The scanner deliberately uses the FYERS NSE-CM master as its universe.
    Live mode refreshes quotes; EOD mode evaluates completed daily candles once.
    Historical calculations are cached in memory by the FYERS provider.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running_key: str | None = None
        self._results: dict[str, list[dict]] = {"live": [], "eod": []}
        self._metrics: dict[str, dict] = {}
        self._universe: list[tuple[str, str]] = []
        self._last_date: str | None = None
        self._eod_done_date: str | None = None
        self._last_live_scan: float | None = None
        self._last_eod_scan: float | None = None
        self._status = "IDLE"
        self._message = "44 MA scanner waiting for a connected FYERS account."
        self._error = ""
        self._processed = 0
        self._total = 0
        self._account_id: int | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="pipsgox-ma44-scanner",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _connected_fyers_account(self):
        accounts = broker_accounts.list_accounts()
        for account in accounts:
            if account.broker.lower() == "fyers" and account.status.lower() == "connected":
                return account
        return None

    def _load_universe(self) -> list[tuple[str, str]]:
        import requests

        response = requests.get(
            "https://public.fyers.in/sym_details/NSE_CM_sym_master.json",
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("FYERS NSE symbol master returned an invalid payload.")

        # Prefer EQ when both EQ and BE records exist for one ticker. Both are
        # stock series; indices and non-equity instruments are excluded.
        selected: dict[str, tuple[int, str]] = {}
        for api_symbol, item in payload.items():
            if not isinstance(item, dict):
                continue
            api = str(api_symbol or "").strip().upper()
            if not api.startswith("NSE:") or "-" not in api:
                continue
            base, series = api.rsplit(":", 1)[-1].rsplit("-", 1)
            if series not in {"EQ", "BE"}:
                continue
            ticker = str(item.get("symTicker") or "").strip().upper()
            if not ticker:
                ticker = base
            ticker = ticker.rsplit("-", 1)[0]
            if not ticker:
                continue
            priority = 0 if series == "EQ" else 1
            previous = selected.get(ticker)
            if previous is None or priority < previous[0]:
                selected[ticker] = (priority, api)

        return [(ticker, api) for ticker, (_, api) in sorted(selected.items())]

    @staticmethod
    def _metrics_from_candles(candles, *, include_last_day: bool) -> dict | None:
        if len(candles) < 221:
            return None

        candles = sorted(candles, key=lambda item: item.time)
        closes = [float(c.close) for c in candles if float(c.close) > 0]
        if len(closes) < 221:
            return None

        ma44 = []
        ma150 = []
        ma200 = []
        for i in range(200, len(closes)):
            ma44.append(sum(closes[i - 43:i + 1]) / 44.0)
            ma150.append(sum(closes[i - 149:i + 1]) / 150.0)
            ma200.append(sum(closes[i - 199:i + 1]) / 200.0)

        if len(ma44) < TREND_POINTS:
            return None

        last44 = ma44[-TREND_POINTS:]
        last150 = ma150[-TREND_POINTS:]
        last200 = ma200[-TREND_POINTS:]

        rising = (
            all(last44[i] > last44[i - 1] for i in range(1, TREND_POINTS))
            and all(last150[i] > last150[i - 1] for i in range(1, TREND_POINTS))
            and all(last200[i] > last200[i - 1] for i in range(1, TREND_POINTS))
        )
        ordered = all(
            last44[i] > last150[i] > last200[i]
            for i in range(TREND_POINTS)
        )
        if not (rising and ordered):
            return None

        return {
            "sma44": last44[-1],
            "sma150": last150[-1],
            "sma200": last200[-1],
            "trend_valid": True,
            "last_close": closes[-1],
        }

    def _history_metrics(self, provider, api_symbol: str, include_last_day: bool) -> dict | None:
        candles = provider.get_history(
            api_symbol,
            "D",
            HISTORY_LIMIT,
            start=None,
            end=date.today(),
        )
        if not include_last_day:
            today = _now().date()
            candles = [candle for candle in candles if datetime.fromtimestamp(candle.time, IST).date() < today]
        return self._metrics_from_candles(candles, include_last_day=include_last_day)

    def _load_metrics(self, provider, universe: list[tuple[str, str]], include_last_day: bool) -> None:
        with self._lock:
            self._processed = 0
            self._total = len(universe)
            self._status = "PREPARING"
            self._message = "Building 44/150/200 SMA trend data..."
            self._error = ""

        fresh: dict[str, dict] = {}

        def one(item):
            ticker, api = item
            try:
                metric = self._history_metrics(provider, api, include_last_day)
                return ticker, metric, ""
            except Exception as exc:
                return ticker, None, str(exc)

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ma44-history") as pool:
            futures = [pool.submit(one, item) for item in universe]
            for future in as_completed(futures):
                if self._stop.is_set():
                    return
                ticker, metric, error = future.result()
                if metric:
                    fresh[ticker] = metric
                with self._lock:
                    self._processed += 1

        with self._lock:
            self._metrics = fresh
            self._message = f"Trend data ready for {len(fresh):,} stocks."
            self._status = "READY"

    def _provider(self, account_id: int):
        account, client_id, api_key, _ = broker_accounts.get_account_credentials(account_id)
        token = broker_accounts.get_access_token(account_id)
        if account.broker.lower() != "fyers":
            raise ValueError("44 MA scanner requires a connected FYERS account.")
        if not token:
            raise ValueError("Selected FYERS account is not connected.")
        from app.providers.fyers import FyersMarketDataProvider
        return FyersMarketDataProvider(
            client_id=api_key.strip() or client_id.strip(),
            access_token=token,
        )

    def _scan_live(self, provider) -> None:
        with self._lock:
            universe = list(self._universe)
            metrics = dict(self._metrics)

        if not universe or not metrics:
            return

        quotes = provider.get_quotes([api for _, api in universe])
        api_to_ticker = {api.upper(): ticker for ticker, api in universe}
        results: list[dict] = []
        for quote in quotes:
            ticker = api_to_ticker.get(quote.symbol.upper())
            metric = metrics.get(ticker or "")
            if not metric or quote.high is None:
                continue
            high_distance = _distance(float(quote.high), metric["sma44"])
            low_distance = _distance(float(quote.low), metric["sma44"]) if quote.low is not None else None
            # LIVE changes only the EOD close leg to today's HIGH. The EOD
            # low-to-44-SMA proximity rule remains unchanged, and LTP is never
            # used as a trigger.
            if low_distance is None or not (-0.25 <= low_distance <= 0.20):
                continue
            if not (0.0 <= high_distance <= 5.0):
                continue
            results.append({
                "symbol": ticker,
                "closed": metric["last_close"],
                "change_percent": quote.change_percent,
                "ma_distance": high_distance,
                "low_distance": low_distance,
                "sma44": metric["sma44"],
                "high": quote.high,
                "mode": "live",
            })

        results.sort(key=lambda row: row["symbol"])
        with self._lock:
            self._results["live"] = results
            self._last_live_scan = time.time()
            self._status = "LIVE"
            self._message = f"Live scan active · {len(results)} matches"

    def _scan_eod(self, provider) -> None:
        with self._lock:
            universe = list(self._universe)

        results: list[dict] = []
        for ticker, api in universe:
            if self._stop.is_set():
                return
            try:
                candles = provider.get_history(api, "D", HISTORY_LIMIT, start=None, end=date.today(), force_refresh=True)
                metric = self._metrics_from_candles(candles, include_last_day=True)
                if not metric or not candles:
                    continue
                last = sorted(candles, key=lambda item: item.time)[-1]
                low_distance = _distance(float(last.low), metric["sma44"])
                close_distance = _distance(float(last.close), metric["sma44"])
                if not (-0.25 <= low_distance <= 0.20):
                    continue
                if not (0.0 <= close_distance <= 5.0):
                    continue
                ordered = sorted(candles, key=lambda item: item.time)
                previous = ordered[-2].close if len(ordered) >= 2 else last.close
                change = ((last.close - previous) / previous * 100.0) if previous else 0.0
                results.append({
                    "symbol": ticker,
                    "closed": float(last.close),
                    "change_percent": change,
                    "ma_distance": close_distance,
                    "sma44": metric["sma44"],
                    "low": float(last.low),
                    "mode": "eod",
                })
            except Exception:
                continue
            with self._lock:
                self._processed += 1

        results.sort(key=lambda row: row["symbol"])
        with self._lock:
            self._results["eod"] = results
            self._last_eod_scan = time.time()
            self._eod_done_date = _now().date().isoformat()
            self._status = "EOD_COMPLETE"
            self._message = f"EOD scan complete · {len(results)} matches"

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                now = _now()
                account = self._connected_fyers_account()
                if not account:
                    with self._lock:
                        self._status = "WAITING"
                        self._message = "Waiting for a connected FYERS account."
                        self._account_id = None
                    self._stop.wait(15)
                    continue

                with self._lock:
                    if self._last_date != now.date().isoformat():
                        self._last_date = now.date().isoformat()
                        self._metrics = {}
                        self._results["live"] = []
                        self._results["eod"] = []
                        self._eod_done_date = None

                provider = self._provider(account.id)
                with self._lock:
                    cached_universe = list(self._universe)
                universe = cached_universe or self._load_universe()
                with self._lock:
                    self._universe = universe
                    self._account_id = account.id

                if _market_open(now):
                    if not self._metrics:
                        self._load_metrics(provider, universe, include_last_day=False)
                    if self._stop.is_set():
                        break
                    self._scan_live(provider)
                    self._stop.wait(REFRESH_SECONDS)
                    continue

                if _after_market(now):
                    if self._eod_done_date != now.date().isoformat():
                        self._scan_eod(provider)
                    else:
                        with self._lock:
                            self._status = "EOD_COMPLETE"
                    # EOD is intentionally one run per day.
                    self._stop.wait(30)
                    continue

                with self._lock:
                    self._status = "WAITING"
                    self._message = "Waiting for NSE market session..."
                self._stop.wait(30)
            except Exception as exc:
                with self._lock:
                    self._status = "ERROR"
                    self._error = str(exc)
                    self._message = "44 MA scanner encountered an error; retrying."
                self._stop.wait(30)

    def snapshot(self, mode: str = "live") -> dict:
        mode = "eod" if mode == "eod" else "live"
        with self._lock:
            return {
                "mode": mode,
                "status": self._status,
                "message": self._message,
                "error": self._error,
                "account_id": self._account_id,
                "universe_count": len(self._universe),
                "eligible_trend_count": len(self._metrics),
                "processed": self._processed,
                "total": self._total,
                "last_scan": self._last_live_scan if mode == "live" else self._last_eod_scan,
                "results": list(self._results[mode]),
            }


scanner = MA44Scanner()
