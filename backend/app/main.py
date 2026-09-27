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
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from fyers_apiv3.FyersWebsocket import data_ws

from app.providers.fyers import FyersMarketDataProvider
from app import auth, dev_control, broker_accounts, security_audit
from app.broker_manager import BrokerManager

load_dotenv()

app = FastAPI(title="PIPSGOX API", version="0.5.0")

_trusted_hosts = [
    item.strip() for item in os.getenv(
        "PIPSGOX_TRUSTED_HOSTS",
        "localhost,127.0.0.1,*.app.github.dev",
    ).split(",") if item.strip()
]
if _trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)

if os.getenv("PIPSGOX_FORCE_HTTPS", "0").strip().lower() in {"1", "true", "yes"}:
    app.add_middleware(HTTPSRedirectMiddleware)

broker_accounts.initialize()
auth.initialize()

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
_fyers_states: dict[str, str] = {}
_broker_auth_states: dict[str, tuple[int, str, str]] = {}
_dhan_auth_states: dict[str, tuple[int, str]] = {}\n_LOGIN_ATTEMPTS: dict[str, tuple[int, float]] = {}\n_LOGIN_MAX_ATTEMPTS = 5\n_LOGIN_WINDOW_SECONDS = 300
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
    allow_origins=[PIPSGOX_WEB_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_COOKIE_SECURE = PIPSGOX_WEB_URL.startswith("https://")

def _request_user(request: Request) -> dict[str, object] | None:
    return auth.get_user(request.cookies.get(auth.SESSION_COOKIE))


@app.middleware("http")
async def require_private_api(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        if _request_user(request) is None:
            return Response(
                content='{"detail":"Authentication required."}',
                status_code=401,
                media_type="application/json",
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if path.startswith("/api/") or path.startswith("/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class AuthCredentials(BaseModel):
    username: str
    password: str


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, object]:
    user = _request_user(request)
    return {
        "setup_required": not auth.has_user(),
        "authenticated": user is not None,
        "username": user["username"] if user else None,
    }


@app.post("/api/auth/setup")
def auth_setup(payload: AuthCredentials, response: Response) -> dict[str, object]:
    if auth.has_user():
        raise HTTPException(status_code=409, detail="Initial account is already configured.")
    try:
        auth.create_initial_user(payload.username, payload.password)
        token = auth.authenticate(payload.username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not token:
        raise HTTPException(status_code=500, detail="Could not create the initial session.")
    response.set_cookie(
        auth.SESSION_COOKIE, token, httponly=True, secure=SESSION_COOKIE_SECURE,
        samesite="lax", max_age=auth.SESSION_TTL_SECONDS, path="/",
    )
    security_audit.record("initial_setup", username=payload.username.strip(), success=True)
    return {"authenticated": True, "username": payload.username.strip()}


@app.post("/api/auth/login")
def auth_login(payload: AuthCredentials, request: Request, response: Response) -> dict[str, object]:
    now = time.monotonic()
    ip = request.client.host if request.client else "unknown"
    attempts, started = _LOGIN_ATTEMPTS.get(ip, (0, now))
    if now - started >= _LOGIN_WINDOW_SECONDS:
        attempts, started = 0, now
    if attempts >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    token = auth.authenticate(payload.username, payload.password)
    if not token:
        _LOGIN_ATTEMPTS[ip] = (attempts + 1, started)
        security_audit.record("login", username=payload.username.strip(), success=False)
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    _LOGIN_ATTEMPTS.pop(ip, None)
    security_audit.record("login", username=payload.username.strip(), success=True)
    response.set_cookie(
        auth.SESSION_COOKIE, token, httponly=True, secure=SESSION_COOKIE_SECURE,
        samesite="lax", max_age=auth.SESSION_TTL_SECONDS, path="/",
    )
    return {"authenticated": True, "username": payload.username.strip()}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, bool]:
    user = _request_user(request)
    auth.revoke(request.cookies.get(auth.SESSION_COOKIE))
    security_audit.record("logout", username=str(user["username"]) if user else "", success=True)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"authenticated": False}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, object]:
    user = _request_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


@app.post("/api/security/disconnect-all")
def security_disconnect_all(request: Request) -> dict[str, object]:
    user = _request_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    count = broker_accounts.disconnect_all()
    security_audit.record("broker_disconnect_all", username=str(user["username"]), success=True)
    return {"disconnected_accounts": count}


@app.post("/api/security/logout-all")
def security_logout_all(request: Request, response: Response) -> dict[str, object]:
    user = _request_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    count = auth.revoke_all(int(user["id"]))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    security_audit.record("logout_all_sessions", username=str(user["username"]), success=True)
    return {"revoked_sessions": count}


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


OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT", "STOP_LOSS", "STOP_LOSS_MARKET"]
OrderProduct = Literal["CNC", "INTRADAY", "MARGIN", "MTF"]
OrderValidity = Literal["DAY", "IOC"]


class OrderRequest(BaseModel):
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType = "MARKET"
    product_type: OrderProduct = "INTRADAY"
    validity: OrderValidity = "DAY"
    price: float | None = None
    trigger_price: float | None = None
    disclosed_quantity: int = 0
    after_market_order: bool = False
    correlation_id: str | None = None


class OrderModifyRequest(BaseModel):
    quantity: int
    order_type: OrderType = "MARKET"
    validity: OrderValidity = "DAY"
    price: float | None = None
    trigger_price: float | None = None
    disclosed_quantity: int = 0


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
        """Resolve all usable FYERS EQ/BE series for live watchlist data."""
        result: set[str] = set()
        with self._symbol_lock:
            for symbol in symbols:
                clean = symbol.strip().upper()
                if not clean:
                    continue

                candidates = _master_symbol_candidates(clean)
                if not candidates:
                    api_symbol = resolve_api_symbol(clean)
                    candidates = [api_symbol] if api_symbol else []

                for api_symbol in candidates:
                    suffix = api_symbol.rsplit("-", 1)[-1].upper()
                    if "-" in api_symbol and suffix not in {"EQ", "BE", "INDEX"}:
                        continue
                    result.add(api_symbol)
                    self._api_to_app[api_symbol.upper()] = clean
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
def fyers_login(request: Request) -> RedirectResponse:
    if _request_user(request) is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not FYERS_CLIENT_ID:
        raise HTTPException(status_code=503, detail="FYERS_CLIENT_ID is not configured.")

    state = secrets.token_urlsafe(24)
    app_session = request.cookies.get(auth.SESSION_COOKIE)
    if not app_session:
        raise HTTPException(status_code=401, detail="Authentication required.")
    _fyers_states[state] = hashlib.sha256(app_session.encode("utf-8")).hexdigest()

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
    request: Request = None,
) -> RedirectResponse:
    global _fyers_access_token

    if not auth_code:
        raise HTTPException(status_code=400, detail="FYERS did not return an auth_code.")
    expected_session = _fyers_states.pop(state, None) if state else None
    current_session = request.cookies.get(auth.SESSION_COOKIE) if request else None
    valid_session = bool(expected_session and current_session and hmac.compare_digest(
        expected_session,
        hashlib.sha256(current_session.encode("utf-8")).hexdigest(),
    ))
    if not valid_session:
        raise HTTPException(status_code=400, detail="Invalid or expired FYERS login state.")

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




class BrokerAccountCreate(BaseModel):
    broker: str
    account_name: str
    client_id: str = ""
    api_secret: str
    api_key: str = ""


class BrokerAccountResponse(BaseModel):
    id: int
    broker: str
    account_name: str
    client_id: str
    status: str
    created_at: str
    updated_at: str


def _broker_account_response(account) -> BrokerAccountResponse:
    return BrokerAccountResponse(
        id=account.id,
        broker=account.broker,
        account_name=account.account_name,
        client_id=broker_accounts.mask_client_id(account.client_id),
        status=account.status,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


@app.get("/api/broker/accounts", response_model=list[BrokerAccountResponse])
def broker_accounts_list() -> list[BrokerAccountResponse]:
    return [_broker_account_response(account) for account in broker_accounts.list_accounts()]


@app.post("/api/broker/accounts", response_model=BrokerAccountResponse, status_code=201)
def broker_accounts_create(payload: BrokerAccountCreate) -> BrokerAccountResponse:
    try:
        account = broker_accounts.create_account(
            payload.broker, payload.account_name, payload.client_id, payload.api_secret, payload.api_key
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _broker_account_response(account)



@app.get("/api/broker/accounts/{account_id}/connect")
def broker_account_connect(account_id: int, request: Request, response: Response) -> dict[str, str]:
    try:
        account, client_id, api_key, api_secret = broker_accounts.get_account_credentials(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    app_session = request.cookies.get(auth.SESSION_COOKIE)
    if not app_session:
        raise HTTPException(status_code=401, detail="Authentication required.")
    session_hash = hashlib.sha256(app_session.encode("utf-8")).hexdigest()
    state = secrets.token_urlsafe(24)
    _broker_auth_states[state] = (account.id, account.broker, session_hash)

    try:
        if account.broker == "fyers":
            result = BrokerManager.start_fyers(
                account.id,
                client_id,
                f"{_codespace_forwarded_url(8000)}/auth/broker/fyers/callback",
                state,
            )
        elif account.broker == "dhan":
            login_state = secrets.token_urlsafe(32)
            app_session = request.cookies.get(auth.SESSION_COOKIE)
            if not app_session:
                raise HTTPException(status_code=401, detail="Authentication required.")
            _dhan_auth_states[login_state] = (
                account.id,
                hashlib.sha256(app_session.encode("utf-8")).hexdigest(),
            )
            result = BrokerManager.start_dhan(account.id, client_id, api_key, api_secret)
        else:
            raise ValueError(f"Unsupported broker '{account.broker}'.")
    except ValueError as exc:
        _broker_auth_states.pop(state, None)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _broker_auth_states.pop(state, None)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"broker": result.broker, "authorization_url": result.authorization_url}


@app.get("/auth/broker/fyers/callback")
def broker_fyers_callback(
    auth_code: str | None = None,
    state: str | None = None,
    request: Request = None,
) -> RedirectResponse:
    if not auth_code or not state:
        raise HTTPException(status_code=400, detail="FYERS did not return a valid authorization response.")
    context = _broker_auth_states.pop(state, None)
    current_session = request.cookies.get(auth.SESSION_COOKIE) if request else None
    valid_session = bool(
        context
        and current_session
        and hmac.compare_digest(
            context[2],
            hashlib.sha256(current_session.encode("utf-8")).hexdigest(),
        )
    )
    if not context or context[1] != "fyers" or not valid_session:
        raise HTTPException(status_code=400, detail="Invalid or expired broker login state.")

    account_id = context[0]
    try:
        _, client_id, _, api_secret = broker_accounts.get_account_credentials(account_id)
        token = BrokerManager.exchange_fyers_code(client_id, api_secret, auth_code)
        BrokerManager.validate_fyers_token(client_id, token)
        broker_accounts.set_access_token(account_id, token, "connected")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        broker_accounts.set_status(account_id, "error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return RedirectResponse(url=PIPSGOX_WEB_URL, status_code=303)


@app.get("/auth/broker/dhan/callback")
def broker_dhan_callback(token_id: str | None = None, request: Request = None) -> RedirectResponse:
    state = request.cookies.get("pipsgox_dhan_state") if request else None
    context = _dhan_auth_states.pop(state, None) if state else None
    current_session = request.cookies.get(auth.SESSION_COOKIE) if request else None
    account_id = context[0] if context else None
    valid_session = bool(context and current_session and hmac.compare_digest(
        context[1],
        hashlib.sha256(current_session.encode("utf-8")).hexdigest(),
    ))
    if not token_id or account_id is None or not valid_session:
        raise HTTPException(status_code=400, detail="Dhan login state is missing, invalid, or expired.")
    try:
        account, client_id, api_key, api_secret = broker_accounts.get_account_credentials(account_id)
        if account.broker != "dhan":
            raise ValueError("Selected account is not a Dhan account.")
        token = BrokerManager.exchange_dhan_token(api_key, api_secret, token_id)
        BrokerManager.validate_dhan_token(client_id, token)
        broker_accounts.set_access_token(account_id, token, "connected")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        broker_accounts.set_status(account_id, "error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    redirect = RedirectResponse(url=PIPSGOX_WEB_URL, status_code=303)
    redirect.delete_cookie("pipsgox_dhan_state", path="/")
    return redirect


@app.get("/api/broker/accounts/{account_id}/session")
def broker_account_session(account_id: int) -> dict[str, object]:
    try:
        account, client_id, _, _ = broker_accounts.get_account_credentials(account_id)
        token = broker_accounts.get_access_token(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    connected = False
    if token and account.broker == "fyers":
        try:
            BrokerManager.validate_fyers_token(client_id, token)
            connected = True
            broker_accounts.set_status(account_id, "connected")
        except Exception:
            broker_accounts.set_status(account_id, "expired")
    elif token and account.broker == "dhan":
        try:
            BrokerManager.validate_dhan_token(client_id, token)
            connected = True
            broker_accounts.set_status(account_id, "connected")
        except Exception:
            broker_accounts.set_status(account_id, "expired")

    return {"id": account.id, "broker": account.broker, "status": "connected" if connected else account.status}


def _broker_account_provider(account_id: int):
    try:
        return BrokerManager.account_provider(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/broker/accounts/{account_id}/orders")
def broker_account_place_order(account_id: int, request: OrderRequest) -> dict[str, object]:
    if request.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero.")
    if request.order_type == "LIMIT" and (request.price is None or request.price <= 0):
        raise HTTPException(status_code=400, detail="A positive price is required for LIMIT orders.")
    if request.order_type in {"STOP_LOSS", "STOP_LOSS_MARKET"} and (request.trigger_price is None or request.trigger_price <= 0):
        raise HTTPException(status_code=400, detail="A positive trigger price is required for stop orders.")

    provider = _broker_account_provider(account_id)
    try:
        data = provider.place_order(request.model_dump())
        return {"account_id": account_id, "broker": provider.broker, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        broker_accounts.set_status(account_id, "error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/api/broker/accounts/{account_id}/orders/{order_id}")
def broker_account_modify_order(
    account_id: int,
    order_id: str,
    request: OrderModifyRequest,
) -> dict[str, object]:
    if request.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero.")
    if request.order_type == "LIMIT" and (request.price is None or request.price <= 0):
        raise HTTPException(status_code=400, detail="A positive price is required for LIMIT orders.")
    if request.order_type in {"STOP_LOSS", "STOP_LOSS_MARKET"} and (request.trigger_price is None or request.trigger_price <= 0):
        raise HTTPException(status_code=400, detail="A positive trigger price is required for stop orders.")

    provider = _broker_account_provider(account_id)
    try:
        data = provider.modify_order(order_id, request.model_dump())
        return {"account_id": account_id, "broker": provider.broker, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        broker_accounts.set_status(account_id, "error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.delete("/api/broker/accounts/{account_id}/orders/{order_id}")
def broker_account_cancel_order(account_id: int, order_id: str) -> dict[str, object]:
    provider = _broker_account_provider(account_id)
    try:
        data = provider.cancel_order(order_id)
        return {"account_id": account_id, "broker": provider.broker, "data": data}
    except Exception as exc:
        broker_accounts.set_status(account_id, "error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _normalize_funds(provider: object, raw: object) -> dict[str, object]:
    """Normalize broker-specific fund responses into one UI-facing schema."""
    payload = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    if not isinstance(payload, dict):
        raise ValueError("Broker returned an invalid funds response.")

    def number(*keys: str) -> float | None:
        for key in keys:
            value = payload.get(key)
            try:
                result = float(value)
            except (TypeError, ValueError):
                continue
            if result == result:
                return result
        return None

    broker = str(getattr(provider, "broker", "")).lower()
    if broker == "dhan":
        return {
            "available": number("availabelBalance", "availableBalance"),
            "sod_limit": number("sodLimit"),
            "utilized": number("utilizedAmount"),
            "collateral": number("collateralAmount"),
            "receivable": number("receiveableAmount", "receivableAmount"),
            "withdrawable": number("withdrawableBalance"),
            "currency": "INR",
            "broker": "dhan",
        }

    # FYERS commonly returns fund_limit as a list of account-level limits.
    limits = payload.get("fund_limit")
    if isinstance(limits, list):
        merged: dict[str, object] = {}
        for item in limits:
            if isinstance(item, dict):
                merged.update(item)
        payload = {**payload, **merged}

    return {
        "available": number("avail_cash", "availableBalance", "available"),
        "sod_limit": number("fund_limit", "fund_limit_total", "sodLimit"),
        "utilized": number("utilized_amount", "utilizedAmount", "utilized"),
        "collateral": number("collateral", "collateralAmount"),
        "receivable": number("receivable", "receiveableAmount"),
        "withdrawable": number("withdrawable_balance", "withdrawableBalance"),
        "currency": "INR",
        "broker": "fyers",
    }


@app.get("/api/broker/accounts/{account_id}/funds")
def broker_account_funds(account_id: int) -> dict[str, object]:
    provider = _broker_account_provider(account_id)
    try:
        return {
            "account_id": account_id,
            "broker": provider.broker,
            "data": _normalize_funds(provider, provider.get_funds()),
        }
    except Exception as exc:
        broker_accounts.set_status(account_id, "error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/broker/accounts/{account_id}/positions")
def broker_account_positions(account_id: int) -> dict[str, object]:
    provider = _broker_account_provider(account_id)
    try:
        from app.providers.accounts import normalize_position
        raw = provider.get_positions()
        items = raw.get("data") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            items = []
        positions = [
            normalize_position(provider, item)
            for item in items
            if isinstance(item, dict)
        ]
        return {
            "account_id": account_id,
            "broker": provider.broker,
            "data": positions,
        }
    except Exception as exc:
        broker_accounts.set_status(account_id, "error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/broker/accounts/{account_id}/orders")
def broker_account_orders(account_id: int) -> dict[str, object]:
    provider = _broker_account_provider(account_id)
    try:
        from app.providers.accounts import normalize_order, _normalize_broker_items
        items = _normalize_broker_items(provider.get_orders(), "orderBook", "orders")
        return {
            "account_id": account_id,
            "broker": provider.broker,
            "data": [normalize_order(provider, item) for item in items],
        }
    except Exception as exc:
        broker_accounts.set_status(account_id, "error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/broker/accounts/{account_id}/trades")
def broker_account_trades(account_id: int) -> dict[str, object]:
    provider = _broker_account_provider(account_id)
    try:
        from app.providers.accounts import normalize_trade, _normalize_broker_items
        items = _normalize_broker_items(provider.get_trades(), "tradeBook", "trades")
        return {
            "account_id": account_id,
            "broker": provider.broker,
            "data": [normalize_trade(provider, item) for item in items],
        }
    except Exception as exc:
        broker_accounts.set_status(account_id, "error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/broker/accounts/{account_id}/holdings")
def broker_account_holdings(account_id: int) -> dict[str, object]:
    provider = _broker_account_provider(account_id)
    try:
        from app.providers.accounts import normalize_holding, _normalize_broker_items
        items = _normalize_broker_items(provider.get_holdings(), "holdings", "data")
        return {
            "account_id": account_id,
            "broker": provider.broker,
            "data": [normalize_holding(provider, item) for item in items],
        }
    except Exception as exc:
        broker_accounts.set_status(account_id, "error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.delete("/api/broker/accounts/{account_id}")
def broker_accounts_delete(account_id: int) -> dict[str, bool]:
    if not broker_accounts.delete_account(account_id):
        raise HTTPException(status_code=404, detail="Broker account not found.")
    return {"deleted": True}


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


@app.get("/api/dev/status")
def dev_status() -> dict[str, object]:
    process = dev_control.status()
    configured = bool(FYERS_CLIENT_ID and FYERS_SECRET_KEY)
    connected = _fyers_session_valid() if configured else False
    return {
        **process,
        "fyers_configured": configured,
        "fyers_connected": connected,
        "web_url": PIPSGOX_WEB_URL,
        "api_url": _codespace_forwarded_url(8000),
    }


@app.get("/api/dev/logs/{name}")
def dev_logs(name: str, lines: int = Query(default=80, ge=1, le=200)) -> dict[str, str]:
    if name not in {"backend", "frontend"}:
        raise HTTPException(status_code=400, detail="Log name must be backend or frontend.")
    return {"name": name, "content": dev_control.tail_log(name, lines)}


@app.post("/api/dev/start")
def dev_start() -> dict[str, str]:
    dev_control.start()
    return {"status": "starting"}


@app.post("/api/dev/stop")
def dev_stop() -> dict[str, str]:
    dev_control.stop()
    return {"status": "stopping"}


@app.post("/api/dev/restart")
def dev_restart() -> dict[str, str]:
    dev_control.restart()
    return {"status": "restarting"}


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


def _aggregate_daily_candles(candles: list[Candle], period: str) -> list[Candle]:
    import datetime as _dt
    groups: dict[tuple[int, int], list[Candle]] = {}
    for candle in candles:
        dt = _dt.datetime.fromtimestamp(candle.time, tz=_dt.timezone.utc)
        key = (dt.year, dt.isocalendar().week) if period == "W" else (dt.year, dt.month)
        groups.setdefault(key, []).append(candle)

    result: list[Candle] = []
    for items in groups.values():
        items.sort(key=lambda item: item.time)
        result.append(Candle(
            time=items[0].time,
            open=items[0].open,
            high=max(item.high for item in items),
            low=min(item.low for item in items),
            close=items[-1].close,
            volume=sum(item.volume for item in items),
        ))
    return sorted(result, key=lambda item: item.time)


def _aggregate_intraday_candles(candles: list[Candle], minutes: int) -> list[Candle]:
    if minutes <= 1:
        return candles
    bucket_seconds = minutes * 60
    groups: dict[int, list[Candle]] = {}
    for candle in candles:
        bucket = candle.time - (candle.time % bucket_seconds)
        groups.setdefault(bucket, []).append(candle)

    result: list[Candle] = []
    for bucket, items in groups.items():
        items.sort(key=lambda item: item.time)
        result.append(Candle(
            time=bucket,
            open=items[0].open,
            high=max(item.high for item in items),
            low=min(item.low for item in items),
            close=items[-1].close,
            volume=sum(item.volume for item in items),
        ))
    return sorted(result, key=lambda item: item.time)


def _dhan_history_to_candles(payload: dict[str, object]) -> list[Candle]:
    timestamps = payload.get("timestamp") or payload.get("timestamps") or []
    opens = payload.get("open") or []
    highs = payload.get("high") or []
    lows = payload.get("low") or []
    closes = payload.get("close") or []
    volumes = payload.get("volume") or []

    if not all(isinstance(value, list) for value in (timestamps, opens, highs, lows, closes, volumes)):
        raise ValueError("Dhan historical response has an invalid candle structure.")

    count = min(len(timestamps), len(opens), len(highs), len(lows), len(closes))
    candles: list[Candle] = []
    for index in range(count):
        try:
            timestamp = int(float(timestamps[index]))
            if timestamp > 10_000_000_000:
                timestamp //= 1000
            candles.append(Candle(
                time=timestamp,
                open=float(opens[index]),
                high=float(highs[index]),
                low=float(lows[index]),
                close=float(closes[index]),
                volume=float(volumes[index]) if index < len(volumes) else 0.0,
            ))
        except (TypeError, ValueError):
            continue
    return candles


def _dhan_history(
    account_id: int,
    symbol: str,
    timeframe: Timeframe,
    limit: int,
    from_date: date | None,
    to_date: date | None,
) -> list[Candle]:
    account, client_id, _, access_token = broker_accounts.get_account_credentials(account_id)
    if account.broker != "dhan":
        raise ValueError("Selected account is not a Dhan account.")
    if not access_token:
        raise HTTPException(status_code=409, detail="Selected Dhan account is not connected.")

    from app.providers.accounts import DhanAccountProvider
    dhan = DhanAccountProvider(client_id, access_token)
    instrument = dhan.resolve_instrument(symbol)
    if not instrument or not instrument.get("security_id"):
        raise ValueError(f"Dhan instrument not found for {symbol}.")

    import datetime as _dt
    end = to_date or _dt.date.today()
    if from_date:
        start = from_date
    elif timeframe == "D":
        start = end - _dt.timedelta(days=max(limit * 2, 30))
    else:
        start = end - _dt.timedelta(days=min(max(limit * 2, 10), 90))

    if timeframe in {"D", "W", "M"}:
        payload = dhan.get_history(
            instrument["security_id"],
            instrument["exchange_segment"],
            instrument["instrument"],
            "D",
            start.isoformat(),
            end.isoformat(),
        )
        candles = _dhan_history_to_candles(payload)
        if timeframe == "W":
            return _aggregate_daily_candles(candles, "W")[-limit:]
        if timeframe == "M":
            return _aggregate_daily_candles(candles, "M")[-limit:]
        return candles[-limit:]

    if timeframe == "30m":
        base_timeframe = "15m"
    elif timeframe == "3m":
        base_timeframe = "1m"
    else:
        base_timeframe = timeframe

    payload = dhan.get_history(
        instrument["security_id"],
        instrument["exchange_segment"],
        instrument["instrument"],
        base_timeframe,
        start.isoformat() + " 09:15:00",
        end.isoformat() + " 15:30:00",
    )
    candles = _dhan_history_to_candles(payload)
    if timeframe in {"30m", "3m"}:
        candles = _aggregate_intraday_candles(candles, int(timeframe[:-1]))
    return candles[-limit:]


def _market_data_provider_for_account(account_id: int | None):
    if account_id is None:
        return provider
    try:
        account, client_id, _, access_token = broker_accounts.get_account_credentials(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if account.broker != "fyers":
        raise HTTPException(
            status_code=501,
            detail="Dhan market-data routing is not enabled yet.",
        )
    if not access_token:
        raise HTTPException(status_code=409, detail="Selected broker account is not connected.")
    return FyersMarketDataProvider(client_id=client_id, access_token=access_token)


@app.get("/api/history", response_model=list[Candle])
def history(
    symbol: str = Query(default="BHARTIARTL", min_length=1, max_length=40),
    timeframe: Timeframe = "D",
    limit: int = Query(default=800, ge=50, le=2000),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    account_id: int | None = Query(default=None, ge=1),
) -> list[Candle]:
    try:
        if account_id is not None:
            account, _, _, _ = broker_accounts.get_account_credentials(account_id)
            if account.broker == "dhan":
                return _dhan_history(account_id, symbol, timeframe, limit, from_date, to_date)

        selected_provider = _market_data_provider_for_account(account_id)

        api_symbol = resolve_api_symbol(symbol)
        if not api_symbol:
            raise unresolved_symbol_error(symbol)
        try:
            candles = selected_provider.get_history(
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
                    candles = selected_provider.get_history(
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


def _dhan_quote(account_id: int, symbol: str) -> Quote:
    account, client_id, _, access_token = broker_accounts.get_account_credentials(account_id)
    if account.broker != "dhan":
        raise ValueError("Selected account is not a Dhan account.")
    if not access_token:
        raise HTTPException(status_code=409, detail="Selected Dhan account is not connected.")

    from app.providers.accounts import DhanAccountProvider
    dhan = DhanAccountProvider(client_id, access_token)
    instrument = dhan.resolve_instrument(symbol)
    if not instrument or not instrument.get("security_id"):
        raise ValueError(f"Dhan instrument not found for {symbol}.")
    payload = dhan.get_ltp({"NSE_EQ": [int(instrument["security_id"])]})
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    segment = data.get(instrument["exchange_segment"], {}) if isinstance(data, dict) else {}
    item = segment.get(instrument["security_id"], {}) if isinstance(segment, dict) else {}
    if not isinstance(item, dict) or item.get("last_price") is None:
        raise ValueError(f"Dhan returned no LTP for {symbol}.")
    return Quote(
        symbol=symbol.upper(),
        exchange="NSE",
        last=float(item["last_price"]),
        change=0.0,
        change_percent=0.0,
        source="DhanHQ API V2",
    )


@app.get("/api/quote", response_model=Quote)
def quote(
    symbol: str = Query(default="BHARTIARTL", min_length=1, max_length=40),
    account_id: int | None = Query(default=None, ge=1),
) -> Quote:
    try:
        original = symbol.strip().upper()
        if account_id is not None:
            account, _, _, _ = broker_accounts.get_account_credentials(account_id)
            if account.broker == "dhan":
                return _dhan_quote(account_id, original)
        selected_provider = _market_data_provider_for_account(account_id)
        api_symbol = resolve_api_symbol(original)
        if not api_symbol:
            raise unresolved_symbol_error(original)
        try:
            result = selected_provider.get_quote(api_symbol)
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
    if auth.get_user(websocket.cookies.get(auth.SESSION_COOKIE)) is None:
        await websocket.close(code=1008, reason="Authentication required.")
        return
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
    account_id: int | None = Query(default=None, ge=1),
) -> list[Quote]:
    requested = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    selected_provider = _market_data_provider_for_account(account_id)
    return get_quotes_for_symbols(requested, selected_provider)


@app.post("/api/quotes", response_model=list[Quote])
def quotes_post(request: QuotesRequest) -> list[Quote]:
    requested = [item.strip().upper() for item in request.symbols if item.strip()]
    return get_quotes_for_symbols(requested, provider)


def get_quotes_for_symbols(requested: list[str], selected_provider=None) -> list[Quote]:
    """Fetch watchlist quotes across the exact FYERS EQ/BE series.

    The PIPSGOX watchlist stores only the ticker, such as LOTUSDEV. Some NSE
    instruments are available to FYERS in BE instead of EQ, so asking only for
    NSE:<ticker>-EQ can silently produce no quote.
    """
    selected_provider = selected_provider or provider
    if not requested:
        return []

    if hasattr(selected_provider, "resolve_instrument") and hasattr(selected_provider, "get_ltp"):
        instruments: list[tuple[str, str, int]] = []
        for clean in requested:
            try:
                resolved = selected_provider.resolve_instrument(clean)
                if resolved and resolved.get("security_id"):
                    instruments.append((clean, str(resolved["exchange_segment"]), int(resolved["security_id"])))
            except (TypeError, ValueError):
                continue

        grouped: dict[str, list[int]] = {}
        for _, segment, security_id in instruments:
            grouped.setdefault(segment, []).append(security_id)

        payload = selected_provider.get_ltp(grouped)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        output: list[Quote] = []
        for clean, segment, security_id in instruments:
            segment_data = data.get(segment, {}) if isinstance(data, dict) else {}
            item = segment_data.get(str(security_id), {}) if isinstance(segment_data, dict) else {}
            if not isinstance(item, dict) or item.get("last_price") is None:
                continue
            output.append(Quote(
                symbol=clean,
                exchange="NSE",
                last=float(item["last_price"]),
                change=0.0,
                change_percent=0.0,
                source="DhanHQ API V2",
            ))
        return output
    if len(requested) > 1000:
        raise HTTPException(
            status_code=400,
            detail="A maximum of 1000 symbols can be requested at once.",
        )

    original_candidates: dict[str, list[str]] = {}
    all_candidates: list[str] = []

    for original in requested:
        clean = original.strip().upper()
        if not clean:
            continue

        candidates = _master_symbol_candidates(clean)
        if not candidates:
            api_symbol = resolve_api_symbol(clean)
            candidates = [api_symbol] if api_symbol else []

        # Only request the supported NSE equity series for ordinary equities.
        # Keep index symbols intact.
        filtered = [
            candidate
            for candidate in candidates
            if candidate.rsplit("-", 1)[-1].upper() in {"EQ", "BE", "INDEX"}
        ]
        if filtered:
            candidates = list(dict.fromkeys(filtered))

        if candidates:
            original_candidates[clean] = candidates
            all_candidates.extend(candidates)

    all_candidates = list(dict.fromkeys(all_candidates))
    quote_by_api: dict[str, Quote] = {}

    # FYERS Quotes supports batches of up to 50 symbols.
    for start in range(0, len(all_candidates), 50):
        chunk = all_candidates[start:start + 50]
        if not chunk:
            continue

        try:
            batch_results = selected_provider.get_quotes(chunk)
        except ValueError:
            # A single invalid/restricted symbol must not hide valid symbols
            # in the same batch.
            batch_results = []
            for candidate in chunk:
                try:
                    batch_results.extend(provider.get_quotes([candidate]))
                except ValueError:
                    continue

        for item in batch_results:
            quote_by_api[item.symbol.upper()] = item

    output: list[Quote] = []
    for original in requested:
        clean = original.strip().upper()
        if not clean:
            continue

        # _master_symbol_candidates() is ordered EQ -> BE. The first returned
        # candidate is therefore the preferred working series.
        for candidate in original_candidates.get(clean, []):
            item = quote_by_api.get(candidate.upper())
            if item is None:
                continue

            # Keep the user's symbol-only watchlist representation in the UI.
            output.append(to_quote(replace(item, symbol=clean)))
            break

    return output
