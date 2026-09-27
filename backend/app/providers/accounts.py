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

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        ...

    def modify_order(self, order_id: str, order: dict[str, Any]) -> dict[str, Any]:
        ...

    def cancel_order(self, order_id: str) -> dict[str, Any]:
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

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        fyers_order_types = {
            "LIMIT": 1,
            "MARKET": 2,
            "STOP_LOSS_MARKET": 3,
            "STOP_LOSS": 4,
        }
        try:
            fyers_type = fyers_order_types[order["order_type"]]
        except KeyError as exc:
            raise ValueError(f"Unsupported FYERS order type: {order['order_type']}") from exc

        fyers_products = {
            "CNC": "CNC",
            "INTRADAY": "INTRADAY",
            "MARGIN": "MARGIN",
            "MTF": "MARGIN",
        }
        fyers_product = fyers_products.get(order["product_type"])
        if not fyers_product:
            raise ValueError(f"Unsupported FYERS product type: {order['product_type']}")

        payload = {
            "symbol": order["symbol"],
            "qty": int(order["quantity"]),
            "type": fyers_type,
            "side": 1 if order["side"] == "BUY" else -1,
            "productType": fyers_product,
            "limitPrice": float(order.get("price") or 0),
            "stopPrice": float(order.get("trigger_price") or 0),
            "validity": order.get("validity", "DAY"),
            "disclosedQty": int(order.get("disclosed_quantity") or 0),
            "offlineOrder": bool(order.get("after_market_order", False)),
        }
        return self._check(self.client.place_order(payload), "place order")

    def modify_order(self, order_id: str, order: dict[str, Any]) -> dict[str, Any]:
        fyers_order_types = {
            "LIMIT": 1,
            "MARKET": 2,
            "STOP_LOSS_MARKET": 3,
            "STOP_LOSS": 4,
        }
        try:
            fyers_type = fyers_order_types[order["order_type"]]
        except KeyError as exc:
            raise ValueError(f"Unsupported FYERS order type: {order['order_type']}") from exc

        payload = {
            "id": order_id,
            "qty": int(order["quantity"]),
            "type": fyers_type,
            "limitPrice": float(order.get("price") or 0),
            "stopPrice": float(order.get("trigger_price") or 0),
            "validity": order.get("validity", "DAY"),
            "disclosedQty": int(order.get("disclosed_quantity") or 0),
        }
        return self._check(self.client.modify_order(payload), "modify order")

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self._check(self.client.cancel_order({"id": order_id}), "cancel order")


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
            "client-id": self.client_id.strip(),
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

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        resolved = self.resolve_instrument(order["symbol"])
        if not resolved or not resolved.get("security_id"):
            raise ValueError(f"Dhan instrument not found for {order['symbol']}.")
        payload = {
            "dhanClientId": self.client_id.strip(),
            "correlationId": str(order.get("correlation_id") or "")[:30],
            "transactionType": order["side"],
            "exchangeSegment": resolved["exchange_segment"],
            "productType": order["product_type"],
            "orderType": order["order_type"],
            "validity": order.get("validity", "DAY"),
            "securityId": resolved["security_id"],
            "quantity": int(order["quantity"]),
            "disclosedQuantity": int(order.get("disclosed_quantity") or 0),
            "price": float(order.get("price") or 0),
            "triggerPrice": float(order.get("trigger_price") or 0),
            "afterMarketOrder": bool(order.get("after_market_order", False)),
        }
        return self._request("POST", "/orders", "place order", json=payload)

    def modify_order(self, order_id: str, order: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "dhanClientId": self.client_id.strip(),
            "orderId": order_id,
            "orderType": order["order_type"],
            "quantity": int(order["quantity"]),
            "price": float(order.get("price") or 0),
            "triggerPrice": float(order.get("trigger_price") or 0),
            "validity": order.get("validity", "DAY"),
        }
        return self._request("PUT", f"/orders/{order_id}", "modify order", json=payload)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/orders/{order_id}", "cancel order")


def normalize_position(provider: object, item: dict[str, Any]) -> dict[str, Any]:
    broker = str(getattr(provider, "broker", "")).lower()
    if broker == "dhan":
        buy_qty = int(float(item.get("buyQty") or 0))
        sell_qty = int(float(item.get("sellQty") or 0))
        net_qty = int(float(item.get("netQty") or buy_qty - sell_qty))
        buy_avg = float(item.get("buyAvg") or 0)
        sell_avg = float(item.get("sellAvg") or 0)
        realized = float(item.get("realizedProfit") or 0)
        unrealized = float(item.get("unrealizedProfit") or 0)
        return {
            "symbol": str(item.get("tradingSymbol") or ""),
            "security_id": str(item.get("securityId") or ""),
            "exchange": str(item.get("exchangeSegment") or ""),
            "product": str(item.get("productType") or ""),
            "side": str(item.get("positionType") or ("LONG" if net_qty > 0 else "SHORT" if net_qty < 0 else "CLOSED")),
            "quantity": net_qty,
            "buy_quantity": buy_qty,
            "sell_quantity": sell_qty,
            "buy_avg": buy_avg,
            "sell_avg": sell_avg,
            "cost_price": float(item.get("costPrice") or 0),
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "pnl": realized + unrealized,
            "expiry": item.get("drvExpiryDate"),
            "option_type": item.get("drvOptionType"),
            "strike": float(item.get("drvStrikePrice") or 0),
        }

    return {
        "symbol": str(item.get("symbol") or item.get("tradingSymbol") or ""),
        "security_id": str(item.get("symbol") or ""),
        "exchange": str(item.get("exchange") or item.get("exchangeSegment") or ""),
        "product": str(item.get("productType") or ""),
        "side": str(item.get("side") or ("LONG" if float(item.get("netQty") or 0) > 0 else "SHORT" if float(item.get("netQty") or 0) < 0 else "CLOSED")),
        "quantity": int(float(item.get("netQty") or item.get("qty") or 0)),
        "buy_quantity": int(float(item.get("buyQty") or 0)),
        "sell_quantity": int(float(item.get("sellQty") or 0)),
        "buy_avg": float(item.get("buyAvg") or item.get("avgPrice") or 0),
        "sell_avg": float(item.get("sellAvg") or 0),
        "cost_price": float(item.get("costPrice") or 0),
        "realized_pnl": float(item.get("realized_profit") or item.get("realizedProfit") or 0),
        "unrealized_pnl": float(item.get("unrealized_profit") or item.get("unrealizedProfit") or item.get("pl") or 0),
        "pnl": float(item.get("pl_total") or item.get("pl") or item.get("realized_profit") or 0) ,
        "expiry": item.get("drvExpiryDate"),
        "option_type": item.get("drvOptionType"),
        "strike": float(item.get("drvStrikePrice") or 0),
    }


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
