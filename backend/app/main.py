from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import threading
from typing import Literal
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
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
_fyers_access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()

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


class SymbolSearchResult(BaseModel):
    symbol: str
    name: str
    exchange: str
    api_symbol: str


provider = FyersMarketDataProvider()


_symbol_master_cache: dict[str, dict] = {}
_symbol_master_date: str | None = None
_symbol_master_lock = threading.Lock()


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
                info = provider.symbol_info(symbol)
                result.add(info.api_symbol)
                self._api_to_app[info.api_symbol.upper()] = symbol
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


@app.get("/auth/fyers/callback", response_class=HTMLResponse)
def fyers_callback(
    request: Request,
    auth_code: str | None = None,
    state: str | None = None,
) -> HTMLResponse | RedirectResponse:
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

    # Return directly to the web terminal after OAuth so the normal startup
    # flow is: start PIPSGOX -> FYERS login if needed -> back to the chart.
    return RedirectResponse(url=PIPSGOX_WEB_URL, status_code=303)


@app.get("/api/fyers/status")
def fyers_status() -> dict[str, object]:
    return {
        "configured": bool(FYERS_CLIENT_ID and FYERS_SECRET_KEY),
        "connected": bool(_fyers_access_token),
        "redirect_uri": FYERS_REDIRECT_URI,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": "pipsgox",
        "data_provider": "fyers_v3",
        "configured": "true" if provider.configured else "false",
        "fyers_connected": "true" if _fyers_access_token else "false",
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
    limit: int = Query(default=260, ge=50, le=2000),
) -> list[Candle]:
    try:
        return [to_candle(item) for item in provider.get_history(symbol.upper(), timeframe, limit)]
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/quote", response_model=Quote)
def quote(
    symbol: str = Query(default="BHARTIARTL", min_length=1, max_length=40),
) -> Quote:
    try:
        return to_quote(provider.get_quote(symbol.upper()))
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

    try:
        results = []
        for start in range(0, len(requested), 50):
            results.extend(provider.get_quotes(requested[start:start + 50]))
        return [to_quote(item) for item in results]
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
