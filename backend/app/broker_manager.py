from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import requests


@dataclass(frozen=True)
class BrokerAuthStart:
    broker: str
    account_id: int
    authorization_url: str


class BrokerManager:
    """Broker authentication facade.

    Each stored account owns its own broker credentials and resulting access token.
    Market-data/order adapters can be layered on top of this manager without
    changing the account storage model.
    """

    @staticmethod
    def start_fyers(account_id: int, client_id: str, redirect_uri: str, state: str) -> BrokerAuthStart:
        if not client_id.strip():
            raise ValueError("FYERS Client ID is required for this account.")

        params = {
            "client_id": client_id.strip(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        url = "https://api-t1.fyers.in/api/v3/generate-authcode?" + urlencode(params)
        return BrokerAuthStart("fyers", account_id, url)

    @staticmethod
    def exchange_fyers_code(
        client_id: str,
        api_secret: str,
        auth_code: str,
    ) -> str:
        import hashlib

        app_id_hash = hashlib.sha256(
            f"{client_id}:{api_secret}".encode("utf-8")
        ).hexdigest()

        response = requests.post(
            "https://api-t1.fyers.in/api/v3/validate-authcode",
            json={
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash,
                "code": auth_code,
            },
            timeout=20,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"FYERS returned a non-JSON response ({response.status_code})."
            ) from exc

        if not response.ok or payload.get("s") == "error" or not payload.get("access_token"):
            raise RuntimeError(
                payload.get("message")
                or f"FYERS token exchange failed ({response.status_code})."
            )
        return str(payload["access_token"])

    @staticmethod
    def validate_fyers_token(client_id: str, access_token: str) -> None:
        from fyers_apiv3 import fyersModel

        client = fyersModel.FyersModel(
            client_id=client_id,
            token=access_token,
            is_async=False,
            log_path="",
        )
        payload = client.get_profile()
        if not isinstance(payload, dict) or payload.get("s") == "error":
            raise RuntimeError(
                str(payload.get("message") if isinstance(payload, dict) else "FYERS profile validation failed.")
            )

    @staticmethod
    def start_dhan(account_id: int, client_id: str, api_key: str, api_secret: str) -> BrokerAuthStart:
        if not client_id.strip():
            raise ValueError("Dhan Client ID is required for this account.")
        if not api_key.strip() or not api_secret.strip():
            raise ValueError("Dhan API key and secret are required.")

        response = requests.post(
            "https://auth.dhan.co/app/generate-consent",
            headers={
                "app_id": api_key.strip(),
                "app_secret": api_secret.strip(),
            },
            params={"client_id": client_id.strip()},
            timeout=20,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Dhan returned a non-JSON consent response.") from exc

        consent_id = str(payload.get("consentAppId") or "").strip()
        if not response.ok or not consent_id:
            raise RuntimeError(
                str(payload.get("message") or payload.get("errorMessage") or "Dhan consent generation failed.")
            )

        url = "https://auth.dhan.co/login/consentApp-login?" + urlencode(
            {"consentAppId": consent_id}
        )
        return BrokerAuthStart("dhan", account_id, url)

    @staticmethod
    def exchange_dhan_token(
        api_key: str,
        api_secret: str,
        token_id: str,
    ) -> str:
        response = requests.post(
            "https://auth.dhan.co/app/consumeApp-consent",
            params={"tokenId": token_id},
            headers={
                "app_id": api_key.strip(),
                "app_secret": api_secret.strip(),
            },
            timeout=20,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Dhan returned a non-JSON token response.") from exc

        token = str(payload.get("accessToken") or "").strip()
        if not response.ok or not token:
            raise RuntimeError(
                str(payload.get("message") or payload.get("errorMessage") or "Dhan token exchange failed.")
            )
        return token

    @staticmethod
    def validate_dhan_token(client_id: str, access_token: str) -> None:
        response = requests.get(
            "https://api.dhan.co/v2/profile",
            headers={
                "access-token": access_token,
                "client-id": client_id,
            },
            timeout=20,
        )
        if not response.ok:
            raise RuntimeError(
                f"Dhan profile validation failed ({response.status_code})."
            )
        payload = response.json()
        if not isinstance(payload, dict) or not payload.get("dhanClientId"):
            raise RuntimeError("Dhan profile validation returned an invalid response.")
