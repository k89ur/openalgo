from __future__ import annotations

import asyncio
from datetime import date
import hashlib
import os
import secrets
import threading
import time
from dataclasses import replace
from typing import Literal
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fyers_apiv3.FyersWebsocket import data_ws

from app.providers.fyers import FyersMarketDataProvider

load_dotenv()

app = FastAPI(title="PIPSGOX API", version="0.5.0")

def _codespace_forwarded_url(port: int) -> str:
    codespace_name = os.getenv("CODESPACE_NAME", "").strip()
    forwarding_domain = os.getenv(
        "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN",
        "app.github.dev",
    ).strip()
    if codespace_name and forwarding_domain:
        return f"https://{codespace_name}-{port}.{forwarding_domain}"
    return f"http://127.0.0.1:{port}"


PIPSGOX_WEB_URL = os.getenv(
    "PIPSGOX_WEB_URL",
    _codespace_forwarded_url(3001),
).strip()
FYERS_REDIRECT_URI = os.getenv(
    "FYERS_REDIRECT_URI",
    f"{_codespace_forwarded_url(8000)}/auth/fyers/callback",
).strip()
FYERS_CLIENT_ID = os.getenv("FYERS_CLIENT_ID", "").strip()
FYERS_SECRET_KEY = os.getenv("FYERS_SECRET_KEY", "").strip()
_fyers_states: set[str] = set()
_fyers_token_lock = threading.Lock()
FYERS_TOKEN_FILE = os.getenv(
    "PIPSGOX_FYERS_TOKEN_FILE",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", ".pipsgox", "fyers_access_token"),
).strip()


def _load_saved_fyers_token() -> str:
    try:
        with open(FYERS_TOKEN_FILE, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except (FileNotFoundError, OSError):
        return ""


def _save_fyers_token(token: str) -> None:
    token = token.strip()
    if not token:
        return

    directory = os.path.dirname(FYERS_TOKEN_FILE)
    os.makedirs(directory, exist_ok=True)
    temporary = f"{FYERS_TOKEN_FILE}.tmp"

    with _fyers_token_lock:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(token)
        os.replace(temporary, FYERS_TOKEN_FILE)
        try:
            os.chmod(FYERS_TOKEN_FILE, 0o600)
        except OSError:
            pass


_fyers_access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip() or _load_saved_fyers_token()

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
    bid: float | None = None
    ask: float | None = None
    source: str = "FYERS API V3"

class QuotesRequest(BaseModel):
    symbols: list[str]


class PipscriptDataRequest(BaseModel):
    type: Literal["history", "quote", "quotes"]
    name: str | None = None
    symbol: str | None = None
    symbols: list[str] = []
    timeframe: Timeframe = "D"
    limit: int = 800
    from_date: date | None = None
    to_date: date | None = None


class PipscriptDataBatchRequest(BaseModel):
    requests: list[PipscriptDataRequest] = []


class SymbolSearchResult(BaseModel):
    symbol: str
    name: str
    exchange: str
    api_symbol: str


provider = FyersMarketDataProvider()

# Pace Pipscript historical-data calls so a multi-index script does not
# burst past FYERS historical-data rate limits.
_pipscript_history_lock = threading.Lock()
_pipscript_last_history_request = 0.0
PIPSCRIPT_HISTORY_INTERVAL_SECONDS = 1.0


def _pipscript_get_history(
    api_symbol: str,
    timeframe: Timeframe,
    limit: int,
    *,
    start: date | None = None,
    end: date | None = None,
    pace: bool = True,
):
    global _pipscript_last_history_request

    if not pace:
        return provider.get_history(
            api_symbol,
            timeframe,
            limit,
            start=start,
            end=end,
        )

    with _pipscript_history_lock:
        now = time.monotonic()
        wait = PIPSCRIPT_HISTORY_INTERVAL_SECONDS - (now - _pipscript_last_history_request)
        if wait > 0:
            time.sleep(wait)

        result = provider.get_history(
            api_symbol,
            timeframe,
            limit,
            start=start,
            end=end,
        )
        _pipscript_last_history_request = time.monotonic()
        return result


if _fyers_access_token:
    provider.set_access_token(_fyers_access_token)


_symbol_master_cache: dict[str, dict] = {}
_symbol_master_date: str | None = None
_symbol_master_lock = threading.Lock()
# Exact ticker -> FYERS API symbol mappings discovered from the master are cached
# so a BE/other-series stock is resolved once and stays fast afterward.
_symbol_resolution_cache: dict[str, list[str]] = {}


def _load_nse_symbol_master() -> dict[str, dict]:
    global _symbol_master_cache, _symbol_master_date

    today = __import__("datetime").date.today().isoformat()
    if _symbol_master_cache and _symbol_master_date == today:
        return _symbol_master_cache

    with _symbol_master_lock:
        if _symbol_master_cache and _symbol_master_date == today:
            return _symbol_master_cache

        try:
            response = requests.get(
                "https://public.fyers.in/sym_details/NSE_CM_sym_master.json",
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            if _symbol_master_cache:
                return _symbol_master_cache
            raise ValueError(f"Could not load FYERS NSE symbol master: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValueError("FYERS NSE symbol master returned an invalid payload.")

        _symbol_master_cache = payload
        _symbol_master_date = today
        _symbol_resolution_cache.clear()
        return _symbol_master_cache


def search_nse_symbols(query: str, limit: int = 12) -> list[SymbolSearchResult]:
    term = query.strip().upper()
    if not term:
        return []

    master = _load_nse_symbol_master()
    matches: list[tuple[int, SymbolSearchResult]] = []

    for api_symbol, item in master.items():
        if not isinstance(item, dict):
            continue

        ticker = str(item.get("symTicker") or "").strip().upper()
        name = str(item.get("exSymName") or item.get("symDetails") or "").strip()
        api = str(api_symbol or "").strip().upper()

        if not ticker or not api:
            continue

        haystack = f"{ticker} {name}".upper()
        if term not in haystack:
            continue

        if ticker == term:
            rank = 0
        elif ticker.startswith(term):
            rank = 1
        elif name.startswith(term):
            rank = 2
        else:
            rank = 3

        matches.append((
            rank,
            SymbolSearchResult(
                symbol=ticker,
                name=name or ticker,
                exchange="NSE",
                api_symbol=api,
            ),
        ))

    matches.sort(key=lambda item: (item[0], item[1].symbol, item[1].name))
    return [item[1] for item in matches[:limit]]


def resolve_api_symbol(symbol: str) -> str | None:
    """Resolve a user-facing symbol to an exact current FYERS API symbol.

    Normal NSE tickers must exist in the current FYERS symbol master. We do
    not fabricate an API symbol when the master has no matching instrument.
    """
    clean = symbol.strip().upper()
    if not clean:
        return None
    if ":" in clean:
        return clean

    # Index aliases must bypass the equity (-EQ) fallback. FYERS uses
    # explicit -INDEX symbols for NSE spot indices.
    alias_symbols = {
        "CNXMETAL", "CNXPHARMA", "NIFTY_IPO", "NIFTY_IND_DEFENCE",
        "NIFTY_HEALTHCARE", "NIFTY_CAPITAL_MKT", "CNXREALTY",
        "NIFTY_CONSR_DURBL", "NIFTY_EV", "NIFTY_TRANS_LOGIS",
        "CNXENERGY", "CNXAUTO", "CNXPSUBANK", "NIFTY_IND_DIGITAL",
        "NIFTYPVTBANK", "BANKNIFTY", "CNXCONSUMPTION", "CNXPSE",
        "CPSE", "NIFTY_IND_TOURISM", "CNXINFRA", "NIFTY_OIL_AND_GAS",
        "CNXFINANCE", "CNXSERVICE", "CNXIT", "CNXFMCG",
        "NIFTY_CEMENT", "NIFTY_CHEMICALS",
    }
    if clean in alias_symbols:
        return provider.symbol_info(clean).api_symbol

    # CSVs may already contain the FYERS series, e.g. MBECL-BE or MBECL-EQ.
    # Preserve that exact series instead of appending another -EQ suffix.
    if "-" in clean:
        base, series = clean.rsplit("-", 1)
        if base and series in {"EQ", "BE"}:
            return f"NSE:{base}-{series}"

    # Resolve ordinary NSE equities against the current FYERS master first.
    # This is important because many valid NSE stocks are currently in BE
    # rather than EQ (for example LOTUSDEV, SWANDEF and NURECA). Constructing
    # NSE:<SYMBOL>-EQ first can make a valid BE-only stock look unavailable.
    candidates = _master_symbol_candidates(clean)
    if candidates:
        return candidates[0]

    # Keep the deterministic EQ form as a temporary fallback if the daily
    # master is unavailable. The FYERS provider remains the authority on
    # whether that exact instrument exists.
    if clean.replace("&", "").replace("-", "").replace("_", "").isalnum():
        return provider.symbol_info(clean).api_symbol

    try:
        master = _load_nse_symbol_master()
    except ValueError:
        return provider.symbol_info(clean).api_symbol

    for api_symbol, item in master.items():
        if not isinstance(item, dict):
            continue

        api = str(api_symbol or "").strip().upper()
        ticker = str(item.get("symTicker") or "").strip().upper()
        exchange_symbol = str(item.get("exSymbol") or "").strip().upper()
        api_base = api.rsplit(":", 1)[-1].rsplit("-", 1)[0]

        if clean in {ticker, exchange_symbol, api_base} and api:
            return api

    return None


def _master_symbol_candidates(symbol: str) -> list[str]:
    """Return all supported current FYERS NSE symbols for a ticker.

    A ticker can exist in more than one exchange series. We prefer EQ, then BE,
    and keep the exact API symbols from FYERS' daily master instead of inventing
    a suffix. FYERS currently documents EQ and BE as the enabled NSE equity
    series; other restricted/SME series are not enabled for trading/data access.
    """
    clean = symbol.strip().upper()
    if not clean:
        return []

    # Explicit FYERS symbols are already authoritative.
    if ":" in clean:
        return [clean]

    # CSVs commonly contain "TICKER-EQ"/"TICKER-BE" without the exchange prefix.
    if "-" in clean:
        head, suffix = clean.rsplit("-", 1)
        if suffix in {"EQ", "BE"} and head:
            return [f"NSE:{head}-{suffix}"]

    with _symbol_master_lock:
        cached = _symbol_resolution_cache.get(clean)
    if cached:
        return list(cached)

    try:
        master = _load_nse_symbol_master()
    except ValueError:
        return []

    candidates: list[tuple[int, str]] = []
    for api_symbol, item in master.items():
        if not isinstance(item, dict):
            continue

        api = str(api_symbol or "").strip().upper()
        ticker = str(item.get("symTicker") or "").strip().upper()
        exchange_symbol = str(item.get("exSymbol") or "").strip().upper()
        if not api or not api.startswith("NSE:"):
            continue

        api_base = api.rsplit(":", 1)[-1]
        if "-" not in api_base:
            continue
        base, series = api_base.rsplit("-", 1)

        # The master has used slightly different identifier fields across
        # versions. Match the authoritative API key as well as ticker/name
        # fields, and tolerate a series suffix in symTicker/exSymbol.
        ticker_base = ticker.rsplit("-", 1)[0] if "-" in ticker else ticker
        exchange_base = (
            exchange_symbol.rsplit("-", 1)[0]
            if "-" in exchange_symbol
            else exchange_symbol
        )

        if clean not in {ticker, ticker_base, exchange_symbol, exchange_base, base}:
            continue

        # Prefer the two NSE equity series that FYERS currently enables.
        # Other NSE-CM records are retained after them so the resolver can
        # identify the exact master instrument instead of silently forcing EQ.
        priority = {"EQ": 0, "BE": 1}.get(series, 10)
        candidates.append((priority, api))

    candidates.sort(key=lambda item: (item[0], item[1]))
    symbols = [api for _, api in candidates]

    if symbols:
        with _symbol_master_lock:
            _symbol_resolution_cache[clean] = list(symbols)

    return symbols


def _resolve_api_symbol_from_master(symbol: str) -> str | None:
    candidates = _master_symbol_candidates(symbol)
    return candidates[0] if candidates else None


def unresolved_symbol_error(symbol: str) -> ValueError:
    return ValueError(
        f"Symbol '{symbol.strip().upper()}' was not found in the current FYERS NSE symbol master."
    )


def resolve_requested_symbols(symbols: list[str]) -> tuple[list[str], dict[str, str]]:
    api_symbols: list[str] = []
    api_to_original: dict[str, str] = {}

    for original in symbols:
        clean = original.strip().upper()
        api_symbol = resolve_api_symbol(clean)
        if not api_symbol:
            # Keep one bad/stale watchlist symbol from blocking all valid symbols.
            continue

        api_symbols.append(api_symbol)
        api_to_original[api_symbol.upper()] = clean

    return api_symbols, api_to_original

class FyersWatchlistStream:
    """One shared FYERS data socket for all PIPSGOX browser clients."""

    def __init__(self) -> None:
        self._socket = None
        self._connect_lock = threading.Lock()
        self._symbol_lock = threading.Lock()
        self._subscribed: set[str] = set()
        self._client_symbols: dict[asyncio.Queue, set[str]] = {}
        self._api_to_app: dict[str, str] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def _api_symbols(self, symbols: set[str]) -> set[str]:
        result = set()
        with self._symbol_lock:
            for symbol in symbols:
                api_symbol = resolve_api_symbol(symbol)
                if not api_symbol:
                    continue
                result.add(api_symbol)
                self._api_to_app[api_symbol.upper()] = symbol
        return result

    def _connect(self) -> None:
        if not provider.configured:
            return
        with self._connect_lock:
            if self._socket is not None:
                return

            token = f"{provider.client_id}:{provider.access_token}"

            def on_message(message) -> None:
                if not isinstance(message, dict):
                    return

                api_symbol = str(message.get("symbol") or "").upper()
                if not api_symbol:
                    return

                with self._symbol_lock:
                    symbol = self._api_to_app.get(api_symbol)

                if not symbol:
                    return

                payload = {
                    "type": "quote",
                    "symbol": symbol,
                    "last": _float_value(message.get("ltp")),
                    "change": _float_value(message.get("ch")),
                    "change_percent": _float_value(message.get("chp")),
                    "open": _float_value(message.get("open_price")),
                    "high": _float_value(message.get("high_price")),
                    "low": _float_value(message.get("low_price")),
                    "volume": _int_value(message.get("vol_traded_today")),
                    "bid": _float_value(message.get("bid_price")),
                    "ask": _float_value(message.get("ask_price")),
                }

                loop = self._loop
                if loop and not loop.is_closed():
                    loop.call_soon_threadsafe(self._broadcast, payload)

            def on_error(message) -> None:
                loop = self._loop
                if loop and not loop.is_closed():
                    loop.call_soon_threadsafe(
                        self._broadcast,
                        {"type": "status", "status": "error", "message": str(message)},
                    )

            def on_close(message) -> None:
                self._socket = None
                loop = self._loop
                if loop and not loop.is_closed():
                    loop.call_soon_threadsafe(
                        self._broadcast,
                        {"type": "status", "status": "disconnected"},
                    )

            def on_connect() -> None:
                with self._symbol_lock:
                    symbols = set(self._subscribed)
                if symbols:
                    self._socket.subscribe(
                        symbols=sorted(symbols),
                        data_type="SymbolUpdate",
                    )
                self._socket.keep_running()

            self._socket = data_ws.FyersDataSocket(
                access_token=token,
                log_path="",
                litemode=False,
                write_to_file=False,
                reconnect=True,
                on_connect=on_connect,
                on_close=on_close,
                on_error=on_error,
                on_message=on_message,
            )

            self._socket.connect()

    async def update_client(self, queue: asyncio.Queue, symbols: list[str]) -> None:
        clean = {item.strip().upper() for item in symbols if item.strip()}
        if len(clean) > 1000:
            raise ValueError("A maximum of 1000 watchlist symbols is supported.")

        self._loop = asyncio.get_running_loop()
        previous = self._client_symbols.get(queue, set())
        self._client_symbols[queue] = clean

        union = set().union(*self._client_symbols.values()) if self._client_symbols else set()
        add = union - self._subscribed
        remove = self._subscribed - union
        self._subscribed = union

        self._api_symbols(union)

        if add or remove:
            if self._socket is None:
                asyncio.create_task(asyncio.to_thread(self._connect))
            else:
                api_add = self._api_symbols(add)
                api_remove = self._api_symbols(remove)
                if api_add:
                    await asyncio.to_thread(
                        self._socket.subscribe,
                        symbols=sorted(api_add),
                        data_type="SymbolUpdate",
                    )
                if api_remove:
                    await asyncio.to_thread(
                        self._socket.unsubscribe,
                        symbols=sorted(api_remove),
                        data_type="SymbolUpdate",
                    )

        if previous != clean:
            await queue.put({
                "type": "status",
                "status": "subscribed",
                "count": len(clean),
            })

    def remove_client(self, queue: asyncio.Queue) -> None:
        self._client_symbols.pop(queue, None)
        union = set().union(*self._client_symbols.values()) if self._client_symbols else set()
        remove = self._subscribed - union
        self._subscribed = union

        if self._socket is not None and remove:
            api_remove = self._api_symbols(remove)
            asyncio.create_task(asyncio.to_thread(
                self._socket.unsubscribe,
                symbols=sorted(api_remove),
                data_type="SymbolUpdate",
            ))

    def _broadcast(self, payload: dict) -> None:
        for queue in list(self._client_symbols):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)


watchlist_stream = FyersWatchlistStream()


def to_candle(item) -> Candle:
    return Candle(
        time=item.time,
        open=item.open,
        high=item.high,
        low=item.low,
        close=item.close,
        volume=item.volume,
    )


def to_quote(item) -> Quote:
    return Quote(
        symbol=item.symbol,
        exchange=item.exchange,
        last=item.last,
        change=item.change,
        change_percent=item.change_percent,
        open=item.open,
        high=item.high,
        low=item.low,
        volume=item.volume,
        bid=item.bid,
        ask=item.ask,
        source="FYERS API V3",
    )


@app.get("/auth/fyers/login")
def fyers_login() -> RedirectResponse:
    if not FYERS_CLIENT_ID:
        raise HTTPException(status_code=503, detail="FYERS_CLIENT_ID is not configured.")

    state = secrets.token_urlsafe(24)
    _fyers_states.add(state)

    params = {
        "client_id": FYERS_CLIENT_ID,
        "redirect_uri": FYERS_REDIRECT_URI,
        "response_type": "code",
        "state": state,
    }
    login_url = "https://api-t1.fyers.in/api/v3/generate-authcode?" + urlencode(params)
    return RedirectResponse(url=login_url, status_code=302)


@app.get("/auth/fyers/callback")
def fyers_callback(
    auth_code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    global _fyers_access_token

    if not auth_code:
        raise HTTPException(status_code=400, detail="FYERS did not return an auth_code.")
    if not state or state not in _fyers_states:
        raise HTTPException(status_code=400, detail="Invalid or expired FYERS login state.")

    _fyers_states.discard(state)

    if not FYERS_CLIENT_ID or not FYERS_SECRET_KEY:
        raise HTTPException(status_code=503, detail="FYERS app credentials are not configured.")

    app_id_hash = hashlib.sha256(
        f"{FYERS_CLIENT_ID}:{FYERS_SECRET_KEY}".encode("utf-8")
    ).hexdigest()

    try:
        response = requests.post(
            "https://api-t1.fyers.in/api/v3/validate-authcode",
            json={
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash,
                "code": auth_code,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"FYERS token request failed: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"FYERS returned a non-JSON response ({response.status_code}).",
        ) from exc

    if not response.ok or payload.get("s") == "error" or not payload.get("access_token"):
        message = payload.get("message") or f"FYERS token exchange failed ({response.status_code})."
        raise HTTPException(status_code=502, detail=message)

    _fyers_access_token = str(payload["access_token"])
    provider.set_access_token(_fyers_access_token)
    _save_fyers_token(_fyers_access_token)

    # Return directly to the web terminal after OAuth so the normal startup
    # flow is: start PIPSGOX -> FYERS login if needed -> back to the chart.
    return RedirectResponse(url=PIPSGOX_WEB_URL, status_code=303)


def _fyers_session_valid() -> bool:
    if not FYERS_CLIENT_ID or not FYERS_SECRET_KEY or not _fyers_access_token:
        return False

    try:
        provider.validate_session()
        return True
    except Exception:
        return False


@app.get("/api/fyers/status")
def fyers_status() -> dict[str, object]:
    configured = bool(FYERS_CLIENT_ID and FYERS_SECRET_KEY)
    connected = _fyers_session_valid() if configured else False
    return {
        "configured": configured,
        "connected": connected,
        "status": "connected" if connected else "not_connected",
        "redirect_uri": FYERS_REDIRECT_URI,
    }


@app.get("/health")
def health() -> dict[str, str]:
    configured = bool(FYERS_CLIENT_ID and FYERS_SECRET_KEY)
    connected = _fyers_session_valid() if configured else False
    return {
        "status": "ok",
        "app": "pipsgox",
        "data_provider": "fyers_v3",
        "configured": "true" if configured else "false",
        "fyers_connected": "true" if connected else "false",
    }


@app.get("/api/symbols/search", response_model=list[SymbolSearchResult])
def symbol_search(
    q: str = Query(default="", max_length=80),
    limit: int = Query(default=12, ge=1, le=25),
) -> list[SymbolSearchResult]:
    try:
        return search_nse_symbols(q, limit)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/history", response_model=list[Candle])
def history(
    symbol: str = Query(default="BHARTIARTL", min_length=1, max_length=40),
    timeframe: Timeframe = "D",
    limit: int = Query(default=800, ge=50, le=2000),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
) -> list[Candle]:
    try:
        api_symbol = resolve_api_symbol(symbol)
        if not api_symbol:
            raise unresolved_symbol_error(symbol)
        try:
            candles = provider.get_history(
                api_symbol,
                timeframe,
                limit,
                start=from_date,
                end=to_date,
            )
        except ValueError as first_error:
            candidates = _master_symbol_candidates(symbol)
            last_error = first_error
            candles = None
            for candidate in candidates:
                if candidate == api_symbol:
                    continue
                try:
                    candles = provider.get_history(
                        candidate,
                        timeframe,
                        limit,
                        start=from_date,
                        end=to_date,
                    )
                    break
                except ValueError as exc:
                    last_error = exc
            if candles is None:
                raise last_error

        return [to_candle(item) for item in candles]
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/pipscript/data")
def pipscript_data(request: PipscriptDataBatchRequest) -> dict[str, object]:
    """Resolve and fetch only the market datasets explicitly requested by Pipscript.

    This is the controlled Data Gateway for the browser Pipscript runtime. Scripts
    never receive arbitrary HTTP/network access; they declare market-data requests
    and the server resolves them through the configured provider.
    """
    if len(request.requests) > 40:
        raise HTTPException(status_code=400, detail="A maximum of 40 Pipscript data requests is supported.")

    history_data: dict[str, dict[str, object]] = {}
    quotes_data: dict[str, object] = {}
    errors: list[dict[str, str]] = []
    # Multi-index scripts need pacing to stay below FYERS historical-data
    # request limits. A single-symbol Pipscript request should not inherit
    # that one-second delay, because it is a common interactive use case.
    history_request_count = sum(1 for item in request.requests if item.type == "history")
    pace_history = history_request_count > 1

    for index, item in enumerate(request.requests):
        name = (item.name or item.symbol or f"request_{index + 1}").strip().upper()
        if not name:
            name = f"REQUEST_{index + 1}"

        try:
            if item.type == "history":
                if not item.symbol:
                    raise ValueError("history request requires symbol.")
                if not 50 <= item.limit <= 2000:
                    raise ValueError("history limit must be between 50 and 2000.")

                original = item.symbol.strip().upper()
                api_symbol = resolve_api_symbol(original)
                if not api_symbol:
                    raise unresolved_symbol_error(original)

                try:
                    candles = _pipscript_get_history(
                        api_symbol,
                        item.timeframe,
                        item.limit,
                        start=item.from_date,
                        end=item.to_date,
                        pace=pace_history,
                    )
                except ValueError as first_error:
                    candidates = _master_symbol_candidates(original)
                    last_error = first_error
                    candles = None
                    for candidate in candidates:
                        if candidate == api_symbol:
                            continue
                        try:
                            candles = _pipscript_get_history(
                                candidate,
                                item.timeframe,
                                item.limit,
                                start=item.from_date,
                                end=item.to_date,
                                pace=pace_history,
                            )
                            break
                        except ValueError as exc:
                            last_error = exc
                    if candles is None:
                        raise last_error
                history_data[name] = {
                    "symbol": original,
                    "timeframe": item.timeframe,
                    "bars": [to_candle(candle).model_dump() for candle in candles],
                }

            elif item.type == "quote":
                if not item.symbol:
                    raise ValueError("quote request requires symbol.")

                original = item.symbol.strip().upper()
                api_symbol = resolve_api_symbol(original)
                if not api_symbol:
                    raise unresolved_symbol_error(original)

                try:
                    result = provider.get_quote(api_symbol)
                except ValueError as first_error:
                    candidates = _master_symbol_candidates(original)
                    last_error = first_error
                    result = None
                    for candidate in candidates:
                        if candidate == api_symbol:
                            continue
                        try:
                            result = provider.get_quote(candidate)
                            break
                        except ValueError as exc:
                            last_error = exc
                    if result is None:
                        raise last_error
                result = replace(result, symbol=original)
                quotes_data[name] = to_quote(result).model_dump()

            elif item.type == "quotes":
                requested = [
                    value.strip().upper()
                    for value in item.symbols
                    if value and value.strip()
                ]
                if not requested:
                    raise ValueError("quotes request requires a non-empty symbols array.")
                if len(requested) > 1000:
                    raise ValueError("A maximum of 1000 symbols can be requested in one quotes request.")

                results = get_quotes_for_symbols(requested)
                quotes_data[name] = {
                    "items": [quote.model_dump() for quote in results],
                }

        except (ValueError, HTTPException) as exc:
            message = exc.detail if isinstance(exc, HTTPException) else str(exc)
            errors.append({
                "name": name,
                "type": item.type,
                "message": str(message),
            })

    return {
        "history": history_data,
        "quotes": quotes_data,
        "errors": errors,
    }


@app.get("/api/quote", response_model=Quote)
def quote(
    symbol: str = Query(default="BHARTIARTL", min_length=1, max_length=40),
) -> Quote:
    try:
        original = symbol.strip().upper()
        api_symbol = resolve_api_symbol(original)
        if not api_symbol:
            raise unresolved_symbol_error(original)
        try:
            result = provider.get_quote(api_symbol)
        except ValueError as first_error:
            candidates = _master_symbol_candidates(original)
            last_error = first_error
            result = None
            for candidate in candidates:
                if candidate == api_symbol:
                    continue
                try:
                    result = provider.get_quote(candidate)
                    break
                except ValueError as exc:
                    last_error = exc
            if result is None:
                raise last_error
        result = replace(result, symbol=original)
        return to_quote(result)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.websocket("/api/ws/quotes")
async def quotes_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
    watchlist_stream._client_symbols[queue] = set()
    watchlist_stream._loop = asyncio.get_running_loop()

    async def sender() -> None:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)

    sender_task = asyncio.create_task(sender())

    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue

            if message.get("action") != "subscribe":
                continue

            symbols = message.get("symbols") or []
            if not isinstance(symbols, list):
                await websocket.send_json({
                    "type": "status",
                    "status": "error",
                    "message": "symbols must be an array",
                })
                continue

            try:
                await watchlist_stream.update_client(queue, [str(item) for item in symbols])
            except ValueError as exc:
                await websocket.send_json({
                    "type": "status",
                    "status": "error",
                    "message": str(exc),
                })

    except WebSocketDisconnect:
        pass
    finally:
        sender_task.cancel()
        watchlist_stream.remove_client(queue)


@app.get("/api/quotes", response_model=list[Quote])
def quotes(
    symbols: str = Query(..., min_length=1, max_length=30000),
) -> list[Quote]:
    requested = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    return get_quotes_for_symbols(requested)


@app.post("/api/quotes", response_model=list[Quote])
def quotes_post(request: QuotesRequest) -> list[Quote]:
    requested = [item.strip().upper() for item in request.symbols if item.strip()]
    return get_quotes_for_symbols(requested)


def get_quotes_for_symbols(requested: list[str]) -> list[Quote]:
    if not requested:
        return []
    if len(requested) > 1000:
        raise HTTPException(status_code=400, detail="A maximum of 1000 symbols can be requested at once.")

    pairs: list[tuple[str, str]] = []
    for original in requested:
        clean = original.strip().upper()
        if not clean:
            continue
        api_symbol = resolve_api_symbol(clean)
        if api_symbol:
            pairs.append((clean, api_symbol))

    results: list[Quote] = []
    api_to_original: dict[str, str] = {
        api_symbol.upper(): original
        for original, api_symbol in pairs
    }

    for start in range(0, len(pairs), 50):
        chunk_pairs = pairs[start:start + 50]
        chunk = [api for _, api in chunk_pairs]
        if not chunk:
            continue

        try:
            batch_results = provider.get_quotes(chunk)
            results.extend(batch_results)

            returned = {item.symbol.upper() for item in batch_results}
            missing_pairs = [
                (original, api)
                for original, api in chunk_pairs
                if api.upper() not in returned
            ]
            if not missing_pairs:
                continue

            # FYERS may return the valid part of a mixed batch without raising
            # an error. Resolve only the missing tickers against the current
            # symbol master so BE/other-series stocks are recovered too.
            fallback_pairs: list[tuple[str, str]] = []
            for original, api in missing_pairs:
                candidates = _master_symbol_candidates(original)
                for candidate in candidates:
                    if candidate.upper() != api.upper():
                        fallback_pairs.append((original, candidate))
                        api_to_original[candidate.upper()] = original

            fallback_chunk = [api for _, api in fallback_pairs]
            if fallback_chunk:
                try:
                    results.extend(provider.get_quotes(fallback_chunk))
                except ValueError:
                    for api_symbol in fallback_chunk:
                        try:
                            results.extend(provider.get_quotes([api_symbol]))
                        except ValueError:
                            continue
            continue
        except ValueError:
            # A whole batch can fail when FYERS rejects one or more symbols.
            # Resolve the batch against the current symbol master and retry.
            fallback_pairs: list[tuple[str, str]] = []
            for original, api in chunk_pairs:
                candidates = _master_symbol_candidates(original)
                added = False
                for candidate in candidates:
                    if candidate.upper() != api.upper():
                        fallback_pairs.append((original, candidate))
                        api_to_original[candidate.upper()] = original
                        added = True
                if not added:
                    fallback_pairs.append((original, api))

            fallback_chunk = [api for _, api in fallback_pairs]
            if fallback_chunk == chunk:
                for api_symbol in chunk:
                    try:
                        results.extend(provider.get_quotes([api_symbol]))
                    except ValueError:
                        continue
                continue

            try:
                results.extend(provider.get_quotes(fallback_chunk))
            except ValueError:
                for api_symbol in fallback_chunk:
                    try:
                        results.extend(provider.get_quotes([api_symbol]))
                    except ValueError:
                        continue

    output: list[Quote] = []
    for item in results:
        original = api_to_original.get(item.symbol.upper(), item.symbol)
        item = replace(item, symbol=original)
        output.append(to_quote(item))

    return output
