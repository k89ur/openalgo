from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import requests
from fyers_apiv3 import fyersModel


class AccountBrokerProvider(Protocol):
    broker: str

    def validate(self) -> None:
        ...

    def resolve_instrument(self, symbol: str) -> dict[str, str] | None:
        """Resolve a user ticker using Dhan's official instrument master."""
        import csv
        import io

        response = requests.get(
            "https://images.dhan.co/api-data/api-scrip-master.csv",
            timeout=30,
        )
        response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text))
        clean = symbol.strip().upper()
        for row in reader:
            ticker = str(
                row.get("SEM_TRADING_SYMBOL")
                or row.get("SM_SYMBOL_NAME")
                or row.get("SYMBOL_NAME")
                or ""
            ).strip().upper()
            if ticker != clean:
                continue
            segment = str(row.get("SEM_SEGMENT") or row.get("SEGMENT") or "").strip().upper()
            if segment in {"E", "EQUITY", "NSE_EQ"}:
                return {
                    "security_id": str(row.get("SEM_SMST_SECURITY_ID") or row.get("SECURITY_ID") or "").strip(),
                    "exchange_segment": "NSE_EQ",
                    "instrument": "EQUITY",
                }
        return None

    def get_history(self, security_id: str, exchange_segment: str, instrument: str, timeframe: str, from_date: str, to_date: str) -> dict[str, Any]:
        if timeframe == "D":
            path = "/charts/historical"
            payload = {
                "securityId": str(security_id),
                "exchangeSegment": exchange_segment,
                "instrument": instrument,
                "oi": False,
                "fromDate": from_date,
                "toDate": to_date,
            }
        else:
            interval_map = {"1m": "1", "5m": "5", "15m": "15", "30m": None, "1h": "60"}
            interval = interval_map.get(timeframe)
            if not interval:
                raise ValueError(f"Dhan does not provide a native {timeframe} chart interval through this endpoint.")
            path = "/charts/intraday"
            payload = {
                "securityId": str(security_id),
                "exchangeSegment": exchange_segment,
                "instrument": instrument,
                "interval": interval,
                "oi": False,
                "fromDate": from_date,
                "toDate": to_date,
            }
        result = self._request("POST", path, "historical data", json=payload)
        if not isinstance(result, dict):
            raise RuntimeError("Dhan historical data returned an invalid response.")
        return result

    def get_ltp(self, instruments: dict[str, list[int]]) -> dict[str, Any]:
        return self._request("POST", "/marketfeed/ltp", "market quote", json=instruments)

    def get_funds(self) -> dict[str, Any]:
        ...

    def get_positions(self) -> dict[str, Any]:
        ...

    def get_orders(self) -> dict[str, Any]:
        ...

    def get_holdings(self) -> dict[str, Any]:
        ...


@dataclass
class FyersAccountProvider:
    client_id: str
    access_token: str
    broker: str = "fyers"

    def __post_init__(self) -> None:
        if not self.client_id.strip() or not self.access_token.strip():
            raise ValueError("FYERS account is not connected.")
        self.client = fyersModel.FyersModel(
            client_id=self.client_id.strip(),
            token=self.access_token.strip(),
            is_async=False,
            log_path="",
        )

    @staticmethod
    def _check(payload: Any, operation: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RuntimeError(f"FYERS {operation} returned an invalid response.")
        if payload.get("s") == "error":
            raise RuntimeError(
                str(payload.get("message") or f"FYERS {operation} failed.")
            )
        return payload

    def validate(self) -> None:
        self._check(self.client.get_profile(), "profile")

    def get_funds(self) -> dict[str, Any]:
        return self._check(self.client.funds(), "funds")

    def get_positions(self) -> dict[str, Any]:
        return self._check(self.client.positions(), "positions")

    def get_orders(self) -> dict[str, Any]:
        return self._check(self.client.orderbook(), "orders")

    def get_holdings(self) -> dict[str, Any]:
        return self._check(self.client.holdings(), "holdings")


@dataclass
class DhanAccountProvider:
    client_id: str
    access_token: str
    broker: str = "dhan"
    base_url: str = "https://api.dhan.co/v2"

    def __post_init__(self) -> None:
        if not self.client_id.strip() or not self.access_token.strip():
            raise ValueError("Dhan account is not connected.")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "access-token": self.access_token.strip(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, operation: str, **kwargs: Any) -> Any:
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=self.headers,
            timeout=20,
            **kwargs,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Dhan {operation} returned a non-JSON response ({response.status_code})."
            ) from exc

        if not response.ok:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise RuntimeError(
                str(message or f"Dhan {operation} failed ({response.status_code}).")
            )
        return payload

    def validate(self) -> None:
        payload = self._request("GET", "/profile", "profile")
        if not isinstance(payload, dict) or payload.get("dhanClientId") != self.client_id.strip():
            raise RuntimeError("Dhan profile validation failed.")

    def get_funds(self) -> dict[str, Any]:
        payload = self._request("GET", "/fundlimit", "funds")
        return {"data": payload}

    def get_positions(self) -> dict[str, Any]:
        payload = self._request("GET", "/positions", "positions")
        return {"data": payload}

    def get_orders(self) -> dict[str, Any]:
        payload = self._request("GET", "/orders", "orders")
        return {"data": payload}

    def get_holdings(self) -> dict[str, Any]:
        payload = self._request("GET", "/holdings", "holdings")
        return {"data": payload}


def build_account_provider(
    broker: str,
    client_id: str,
    access_token: str,
) -> AccountBrokerProvider:
    broker = broker.strip().lower()
    if broker == "fyers":
        return FyersAccountProvider(client_id, access_token)
    if broker == "dhan":
        return DhanAccountProvider(client_id, access_token)
    raise ValueError(f"Unsupported broker '{broker}'.")
