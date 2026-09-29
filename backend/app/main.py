from __future__ import annotations

import asyncio
from datetime import date, timedelta
import hashlib
import logging
import hmac
import json
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
from app import auth, broker_accounts, security_audit, diagnostics
from app.broker_manager import BrokerManager

load_dotenv()

app = FastAPI(title="PIPSGOX API", version="0.5.0")
logger = logging.getLogger("pipsgox.market_data")

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
# PIPSGOX is currently a single-owner installation. Broker configuration must
# not survive after the owner has been removed.
if not auth.has_user():
    broker_accounts.reset_all()
diagnostics.initialize()

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
    f"{_codespace_forwarded_url(8000)}/auth/broker/fyers/callback",
).strip()


# Dhan OAuth state is persisted in broker_accounts.broker_oauth_states so the
# callback survives backend restarts during the external login flow.
_LOGIN_ATTEMPTS: dict[str, tuple[int, float]] = {}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 300
_order_idempotency_lock = threading.Lock()

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


def _diagnostic_account_id(request: Request) -> int | None:
    raw = request.query_params.get("account_id")
    if raw:
        try:
            return int(raw)
        except ValueError:
            return None
    parts = [part for part in request.url.path.split("/") if part]
    for index, part in enumerate(parts[:-1]):
        if part == "accounts" and parts[index + 1].isdigit():
            return int(parts[index + 1])
    return None


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
    if (
        path.startswith("/api/")
        and not path.startswith("/api/auth/")
        and not path.startswith("/api/diagnostics/")
        and 200 <= response.status_code < 400
    ):
        parsed_account_id = _diagnostic_account_id(request)
        diagnostics.resolve_service(service=path, account_id=parsed_account_id)

    if (
        path.startswith("/api/")
        and not path.startswith("/api/auth/")
        and not path.startswith("/api/diagnostics/")
        and response.status_code >= 400
    ):
        parsed_account_id = _diagnostic_account_id(request)
        detail = ""
        try:
            body = getattr(response, "body", b"")
            if body:
                payload = json.loads(body.decode("utf-8"))
                detail = str(payload.get("detail") or payload.get("message") or "") if isinstance(payload, dict) else ""
        except Exception:
            detail = ""
        diagnostics.record(
            severity="ERROR" if response.status_code >= 500 or response.status_code in {409, 422} else "WARNING",
            category="HTTP",
            component="API",
            service=path,
            error_code=f"PIP-API-HTTP-{response.status_code}",
            message=detail or f"{request.method} {path} returned HTTP {response.status_code}.",
            account_id=parsed_account_id,
            http_status=response.status_code,
            technical_detail=detail,
        )
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
    account_id: int | None = None
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
    account_id: int | None = None
    requests: list[PipscriptDataRequest] = []


class SymbolSearchResult(BaseModel):
    symbol: str
    name: str
    exchange: str
    api_symbol: str


provider = FyersMarketDataProvider(client_id="", access_token="")

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
    """Account-bound FYERS data socket for one PIPSGOX browser session."""

    def __init__(self, market_provider: FyersMarketDataProvider) -> None:
        self.provider = market_provider
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
        if not self.provider.configured:
            return
        with self._connect_lock:
            if self._socket is not None:
                return

            token = f"{self.provider.client_id}:{self.provider.access_token}"

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
    raise HTTPException(
        status_code=410,
        detail="Legacy FYERS login is disabled. Add and connect the broker from Account.",
    )


@app.get("/auth/fyers/callback")
def fyers_callback() -> RedirectResponse:
    raise HTTPException(
        status_code=410,
        detail="Legacy FYERS callback is disabled. Add and connect the broker from Account.",
    )


def _fyers_session_valid() -> bool:
    try:
        return any(
            account.broker == "fyers" and account.status == "connected"
            for account in broker_accounts.list_accounts()
        )
    except Exception:
        return False


def _system_readiness() -> dict[str, object]:
    """Return broker-agnostic PIPSGOX server/configuration readiness."""
    user_configured = auth.has_user()
    try:
        accounts = broker_accounts.list_accounts()
    except Exception:
        accounts = []

    connected_accounts = [
        account for account in accounts
        if account.status.strip().lower() == "connected"
    ]
    broker_accounts_configured = bool(accounts)
    broker_connected = bool(connected_accounts)
    ready = user_configured and broker_connected

    return {
        "user_configured": user_configured,
        "broker_accounts_configured": broker_accounts_configured,
        "broker_connected": broker_connected,
        "connected_broker_count": len(connected_accounts),
        "broker_account_count": len(accounts),
        "ready": ready,
    }




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


def _diagnostic_check(
    checks: list[dict[str, object]],
    *,
    check_id: str,
    label: str,
    category: str,
    component: str,
    severity: str,
    operation,
    error_code: str,
    account_id: int,
    broker: str,
    symbol: str = "",
) -> None:
    started = time.monotonic()
    try:
        operation()
    except Exception as exc:
        technical = diagnostics.sanitize(f"{type(exc).__name__}: {exc}")
        checks.append({
            "id": check_id,
            "label": label,
            "category": category,
            "component": component,
            "severity": severity,
            "status": "ERROR" if severity in {"CRITICAL", "ERROR"} else "WARNING",
            "error_code": error_code,
            "message": diagnostics.sanitize(str(exc) or "Check failed."),
            "technical_detail": technical,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "symbol": symbol,
        })
        diagnostics.record(
            severity=severity,
            category=category,
            component=component,
            service=label,
            error_code=error_code,
            message=str(exc) or "Check failed.",
            account_id=account_id,
            broker=broker,
            symbol=symbol,
            technical_detail=technical,
        )
        return

    checks.append({
        "id": check_id,
        "label": label,
        "category": category,
        "component": component,
        "severity": severity,
        "status": "OK",
        "message": "Check passed.",
        "technical_detail": "",
        "duration_ms": round((time.monotonic() - started) * 1000),
        "symbol": symbol,
    })
    diagnostics.resolve(error_code=error_code, account_id=account_id, symbol=symbol)


def _run_startup_checks(account_id: int) -> dict[str, object]:
    checks: list[dict[str, object]] = []

    try:
        account, client_id, _, _ = broker_accounts.get_account_credentials(account_id)
        broker = account.broker.lower()
        access_token = broker_accounts.get_access_token(account_id)
    except Exception as exc:
        message = str(exc) or "Broker account could not be loaded."
        diagnostics.record(
            severity="CRITICAL",
            category="BROKER",
            component="Account",
            service="credentials",
            error_code="PIP-BROKER-ACCOUNT-001",
            message=message,
            account_id=account_id,
            technical_detail=f"{type(exc).__name__}: {exc}",
        )
        return {
            "ready": False,
            "status": "FAILED",
            "account_id": account_id,
            "broker": "",
            "account_name": "",
            "checks": [{
                "id": "broker-account",
                "label": "Broker account",
                "category": "BROKER",
                "component": "Account",
                "severity": "CRITICAL",
                "status": "ERROR",
                "error_code": "PIP-BROKER-ACCOUNT-001",
                "message": message,
                "technical_detail": f"{type(exc).__name__}: {exc}",
            }],
            "summary": diagnostics.summary(account_id),
            "checked_at": time.time(),
        }

    if not access_token:
        message = "Broker account has no active access token. Reconnect the broker account."
        diagnostics.record(
            severity="CRITICAL",
            category="BROKER",
            component="Authentication",
            service="access token",
            error_code="PIP-BROKER-AUTH-001",
            message=message,
            account_id=account_id,
            broker=broker,
        )
        checks.append({                "error_code": "PIP-BROKER-ACCOUNT-001",
                "message": message,
                "technical_detail": f"{type(exc).__name__}: {exc}",
            }],
            "summary": diagnostics.summary(account_id),
            "checked_at": time.time(),
        }

    if not access_token:
        message = "Broker account has no active access token. Reconnect the broker account."
        diagnostics.record(
            severity="CRITICAL",
            category="BROKER",
            component="Authentication",
            service="access token",
            error_code="PIP-BROKER-AUTH-001",
            message=message,
            account_id=account_id,
            broker=broker,
        )
        checks.append({
            "id": "broker-auth",
            "label": "Broker authentication",
            "category": "BROKER",
            "component": "Authentication",
            "severity": "CRITICAL",
            "status": "ERROR",
            "error_code": "PIP-BROKER-AUTH-001",
            "message": message,
            "technical_detail": "",
        })
        return {
            "ready": False,
            "status": "FAILED",
            "account_id": account_id,
            "broker": broker,
            "account_name": account.account_name,
            "checks": checks,
            "summary": diagnostics.summary(account_id),
            "checked_at": time.time(),
        }

    _diagnostic_check(
        checks, check_id="broker-session", label="Broker session",
        category="BROKER", component="Authentication", severity="CRITICAL",
        operation=lambda: BrokerManager.account_provider(account_id).validate(),
        error_code="PIP-BROKER-SESSION-001", account_id=account_id, broker=broker,
    )

    provider = None
    market_provider = None
    test_symbol = "RELIANCE"

    try:
        if broker == "dhan":
            from app.providers.accounts import DhanAccountProvider
            provider = DhanAccountProvider(client_id, access_token)
            market_provider = provider
        else:
            market_provider = _market_data_provider_for_account(account_id)
    except Exception as exc:
        _diagnostic_check(
            checks, check_id="market-provider", label="Market-data provider",
            category="MARKET_DATA", component="Provider", severity="CRITICAL",
            operation=lambda exc=exc: (_ for _ in ()).throw(exc),
            error_code="PIP-MARKET-PROVIDER-001", account_id=account_id, broker=broker,
        )

    def check_symbol() -> None:
        if broker == "dhan":
            resolved = provider.resolve_instrument(test_symbol)
            if not resolved or not resolved.get("security_id"):
                raise ValueError(f"Dhan instrument master could not resolve {test_symbol}.")
        else:
            master = _load_nse_symbol_master()
            if not master or not resolve_api_symbol(test_symbol):
                raise ValueError(f"{test_symbol} was not found in the current FYERS NSE symbol master.")

    _diagnostic_check(
        checks, check_id="market-symbol", label="Symbol master / resolution",
        category="MARKET_DATA", component="Symbol Master", severity="CRITICAL",
        operation=check_symbol, error_code="PIP-MARKET-SYMBOL-001",
        account_id=account_id, broker=broker, symbol=test_symbol,
    )

    def check_quote() -> None:
        if market_provider is None:
            raise ValueError("Market-data provider is unavailable.")
        if broker == "dhan":
            resolved = provider.resolve_instrument(test_symbol)
            if not resolved or not resolved.get("security_id"):
                raise ValueError(f"Dhan instrument master could not resolve {test_symbol}.")
            payload = provider.get_ltp({resolved["exchange_segment"]: [int(resolved["security_id"])]})
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            segment = data.get(resolved["exchange_segment"], {}) if isinstance(data, dict) else {}
            item = segment.get(str(resolved["security_id"]), {}) if isinstance(segment, dict) else {}
            if not isinstance(item, dict) or item.get("last_price") is None:
                raise ValueError(f"Dhan returned no market quote for {test_symbol}.")
        else:
            api_symbol = resolve_api_symbol(test_symbol)
            if not api_symbol:
                raise ValueError(f"{test_symbol} could not be resolved.")
            quote_result = market_provider.get_quote(api_symbol)
            if float(quote_result.last) <= 0:
                raise ValueError(f"FYERS returned no usable quote for {api_symbol}.")

    # The HTTP Quotes endpoint is a non-blocking startup signal. FYERS can
    # temporarily return 429/"Bad request" from Quotes while authenticated
    # history and the live market-data WebSocket remain healthy. Do not block
    # the entire terminal on that transient/endpoint-specific condition.
    _diagnostic_check(
        checks, check_id="market-quote", label="Market quote",
        category="MARKET_DATA", component="Quote API", severity="WARNING",
        operation=check_quote, error_code="PIP-MARKET-QUOTE-001",
        account_id=account_id, broker=broker, symbol=test_symbol,
    )

    def check_history() -> None:
        start = date.today() - timedelta(days=10)
        end = date.today()