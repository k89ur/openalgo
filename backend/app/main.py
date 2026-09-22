from __future__ import annotations

import hashlib
import os
import secrets
from typing import Literal
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.providers.fyers import FyersMarketDataProvider

app = FastAPI(title="PIPSGOX API", version="0.5.0")

FYERS_REDIRECT_URI = os.getenv(
    "FYERS_REDIRECT_URI",
    "https://studious-space-system-5vxr9wq4qgwphv5r5-8000.app.github.dev/auth/fyers/callback",
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


provider = FyersMarketDataProvider()


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
def fyers_callback(auth_code: str | None = None, state: str | None = None) -> HTMLResponse:
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

    return HTMLResponse(
        """
        <html><body style="font-family:Arial;padding:40px">
        <h2>PIPSGOX — FYERS connected</h2>
        <p>Authentication completed successfully. The FYERS access token is now active for this backend session.</p>
        <p>You can close this tab and return to PIPSGOX.</p>
        </body></html>
        """
    )


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


@app.get("/api/quotes", response_model=list[Quote])
def quotes(
    symbols: str = Query(..., min_length=1, max_length=4000),
) -> list[Quote]:
    requested = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    if not requested:
        return []

    try:
        results = []
        for start in range(0, len(requested), 50):
            results.extend(provider.get_quotes(requested[start:start + 50]))
        return [to_quote(item) for item in results]
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
