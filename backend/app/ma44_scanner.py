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
        self._stage = "WAITING"
        self._message = "44 MA scanner waiting for a connected FYERS account."
        self._error = ""
        self._processed = 0
        self._total = 0
        self._account_id: int | None = None
        self._preferred_account_id: int | None = None

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

    def set_preferred_account(self, account_id: int | None) -> None:
        with self._lock:
            self._preferred_account_id = account_id if account_id and account_id > 0 else None

    def _connected_fyers_account(self):
        accounts = broker_accounts.list_accounts()
        with self._lock:
            preferred_id = self._preferred_account_id
        if preferred_id is not None:
            for account in accounts:
                if account.id != preferred_id or account.broker.lower() != "fyers":
                    continue
                # The UI's CONNECTED state is normally authoritative, but the
                # persisted status can briefly lag the access-token state after
                # a fresh FYERS login. A usable token is sufficient for the
                # scanner to attempt the provider connection; provider errors
                # are then surfaced instead of leaving the scanner in WAITING.
                if account.status.lower() == "connected" or broker_accounts.get_access_token(account.id):
                    return account
            return None
        for account in accounts:
            if account.broker.lower() != "fyers":
                continue
            if account.status.lower() == "connected" or broker_accounts.get_access_token(account.id):
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
            "trend_ma44": last44,
            "trend_ma150": last150,
            "trend_ma200": last200,
            "tail_closes": closes[-199:],
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
            self._stage = "BUILDING_TREND"
            self._message = f"Building 44/150/200 SMA trend data · 0/{len(universe):,}"
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
                    self._message = (
                        f"Building 44/150/200 SMA trend data · "
                        f"{self._processed:,}/{self._total:,}"
                    )

        with self._lock:
            self._metrics = fresh
            self._stage = "READY"
            self._message = f"Trend data ready · {len(fresh):,} stocks passed the 20-day trend filter."
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
            if not metric or quote.last is None or quote.open is None or quote.low is None:
                continue
            tail = metric.get("tail_closes") or []
            if len(tail) < 43:
                continue

            # LIVE treats the current price as today's running close and
            # calculates today's 44 SMA from the previous 43 completed closes
            # plus the current price.
            current_close = float(quote.last)
            sma44 = (sum(tail[-43:]) + current_close) / 44.0
            low_distance = _distance(float(quote.low), sma44)

            # Loosened filters:
            # 1) current price > current-day 44 SMA
            # 2) today's low is -0.25% to +2.00% from current-day 44 SMA
            # 3) today's candle is bullish: current price > today's open
            if current_close <= sma44:
                continue
            if not (-0.25 <= low_distance <= 2.0):
                continue
            if current_close <= float(quote.open):
                continue

            results.append({
                "symbol": ticker,
                "closed": current_close,
                "change_percent": quote.change_percent,
                "ma_distance": _distance(current_close, sma44),
                "low_distance": low_distance,
                "sma44": sma44,
                "high": quote.high,
                "low": quote.low,
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
            metrics = dict(self._metrics)
            self._processed = 0
            self._total = len(universe)
            self._status = "SCANNING"
            self._stage = "SCANNING_EOD"
            self._message = f"EOD scan in progress · 0/{len(universe):,}"
            self._error = ""

        if not metrics:
            with self._lock:
                self._stage = "BUILDING_TREND"
                self._message = "Preparing EOD trend data from completed daily candles..."
            self._load_metrics(provider, universe, include_last_day=False)
            with self._lock:
                metrics = dict(self._metrics)
                self._stage = "SCANNING_EOD"
                self._status = "SCANNING"
                self._processed = 0
                self._total = len(universe)
                self._message = f"EOD scan in progress · 0/{len(universe):,}"

        # EOD source is NSE's official final UDiFF bhavcopy. This avoids
        # thousands of FYERS quote/history calls and gives the final exchange
        # OHLC for every equity in one compressed file.
        from app.nse_bhavcopy import fetch_latest

        bhav_date, bhavcopy = fetch_latest()
        api_to_ticker = {api.upper(): ticker for ticker, api in universe}
        ticker_to_api = {ticker.upper(): api for ticker, api in universe}
        results: list[dict] = []

        for processed, (ticker, api) in enumerate(universe, start=1):
            if self._stop.is_set():
                return
            metric = metrics.get(ticker)
            row = bhavcopy.get(ticker.upper())
            if not metric or not row:
                continue

            today_open = float(row["open"])
            today_high = float(row["high"])
            today_low = float(row["low"])
            today_close = float(row["close"])
            previous_close = float(row["previous_close"])

            tail = metric.get("tail_closes") or []
            if len(tail) < 199:
                continue

            sma44 = (sum(tail[-43:]) + today_close) / 44.0
            sma150 = (sum(tail[-149:]) + today_close) / 150.0
            sma200 = (sum(tail[-199:]) + today_close) / 200.0

            trend44 = (list(metric["trend_ma44"]) + [sma44])[-TREND_POINTS:]
            trend150 = (list(metric["trend_ma150"]) + [sma150])[-TREND_POINTS:]
            trend200 = (list(metric["trend_ma200"]) + [sma200])[-TREND_POINTS:]
            rising = (
                all(trend44[i] > trend44[i - 1] for i in range(1, TREND_POINTS))
                and all(trend150[i] > trend150[i - 1] for i in range(1, TREND_POINTS))
                and all(trend200[i] > trend200[i - 1] for i in range(1, TREND_POINTS))
            )
            ordered = all(
                trend44[i] > trend150[i] > trend200[i]
                for i in range(TREND_POINTS)
            )

            low_distance = _distance(today_low, sma44)
            close_distance = _distance(today_close, sma44)

            if (
                rising
                and ordered
                and today_close > today_open
                and today_close > sma44
                and -0.25 <= low_distance <= 2.0
            ):
                change = (
                    (today_close - previous_close) / previous_close * 100.0
                    if previous_close else 0.0
                )
                results.append({
                    "symbol": ticker,
                    "closed": today_close,
                    "change_percent": change,
                    "ma_distance": close_distance,
                    "sma44": sma44,
                    "high": today_high,
                    "low": today_low,
                    "mode": "eod",
                    "bhavcopy_date": bhav_date.isoformat(),
                })

            with self._lock:
                self._processed = processed
                self._message = (
                    f"EOD scan in progress · {processed:,}/{len(universe):,} "
                    f"· {len(results):,} matches"
                )

        results.sort(key=lambda row: row["symbol"])
        with self._lock:
            self._results["eod"] = results
            self._last_eod_scan = time.time()
            self._eod_done_date = _now().date().isoformat()
            self._stage = "COMPLETE"
            self._status = "EOD_COMPLETE"
            self._message = (
                f"EOD scan complete · {len(results):,} matches · "
                f"NSE bhavcopy {bhav_date.isoformat()}"
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                now = _now()
                account = self._connected_fyers_account()
                if not account:
                    with self._lock:
                        self._status = "WAITING"
                        self._stage = "WAITING_FOR_BROKER"
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
                if cached_universe:
                    universe = cached_universe
                else:
                    with self._lock:
                        self._status = "PREPARING"
                        self._stage = "LOADING_UNIVERSE"
                        self._message = "Loading NSE stock universe..."
                    universe = self._load_universe()
                with self._lock:
                    self._universe = universe
                    self._account_id = account.id
                    if self._stage == "LOADING_UNIVERSE":
                        self._message = f"NSE universe loaded · {len(universe):,} stocks"

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
                            self._stage = "COMPLETE"
                    self._status = "EOD_COMPLETE"
                    # EOD is intentionally one run per day.
                    self._stop.wait(30)
                    continue

                with self._lock:
                    self._status = "WAITING"
                    self._stage = "WAITING_FOR_MARKET"
                    self._message = "Waiting for NSE market session..."
                self._stop.wait(30)
            except Exception as exc:
                with self._lock:
                    self._status = "ERROR"
                    self._stage = "ERROR"
                    self._error = str(exc)
                    self._message = "44 MA scanner encountered an error; retrying."
                self._stop.wait(30)

    def snapshot(self, mode: str = "live", account_id: int | None = None) -> dict:
        if account_id is not None:
            self.set_preferred_account(account_id)
        mode = "eod" if mode == "eod" else "live"
        with self._lock:
            return {
                "mode": mode,
                "status": self._status,
                "stage": self._stage,
                "message": self._message,
                "progress": (
                    round((self._processed / self._total) * 100, 1)
                    if self._total else 0
                ),
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
