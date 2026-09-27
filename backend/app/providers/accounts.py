from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import requests
from fyers_apiv3 import fyersModel


class AccountBrokerProvider(Protocol):
    broker: str

    def validate(self) -> None:
        ...

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
