from __future__ import annotations

import os
from datetime import date, datetime, timezone

import requests

from app.providers.fundamentals import FundamentalPoint


class FmpFundamentalsProvider:
    """Optional historical fundamentals provider.

    FMP is intentionally separate from the broker market-data provider.
    PIPSGOX uses reported-period P/E observations rather than fabricating a
    daily P/E from today's EPS and historical prices.
    """

    name = "financial_modeling_prep"

    def __init__(self) -> None:
        self.api_key = os.getenv("FMP_API_KEY", "").strip()
        self.base_url = os.getenv(
            "FMP_BASE_URL",
            "https://financialmodelingprep.com/api/v3",
        ).strip().rstrip("/")
        self.symbol_suffix = os.getenv("FMP_SYMBOL_SUFFIX", ".NS").strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _map_symbol(self, symbol: str) -> str:
        clean = symbol.strip().upper()
        if not clean:
            raise ValueError("A symbol is required for historical P/E.")
        if "." in clean or ":" in clean:
            return clean
        return f"{clean}{self.symbol_suffix}"

    @staticmethod
    def _epoch_seconds(value: str) -> int:
        parsed = date.fromisoformat(value[:10])
        return int(datetime(
            parsed.year,
            parsed.month,
            parsed.day,
            tzinfo=timezone.utc,
        ).timestamp())

    def get_historical_pe(self, symbol: str, limit: int = 40) -> list[FundamentalPoint]:
        if not self.configured:
            raise ValueError(
                "Historical P/E is not configured. Set FMP_API_KEY to enable it."
            )

        if limit < 1 or limit > 200:
            raise ValueError("Historical P/E limit must be between 1 and 200.")

        mapped = self._map_symbol(symbol)
        response = requests.get(
            f"{self.base_url}/ratios/{mapped}",
            params={
                "period": "quarter",
                "limit": str(limit),
                "apikey": self.api_key,
            },
            timeout=20,
        )
        if not response.ok:
            raise ValueError(
                f"Fundamentals provider returned HTTP {response.status_code}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("Fundamentals provider returned invalid JSON.") from exc

        if not isinstance(payload, list):
            raise ValueError("Fundamentals provider returned an invalid ratios payload.")

        points: list[FundamentalPoint] = []
        for item in payload:
            if not isinstance(item, dict):
                continue

            raw_date = item.get("date")
            raw_pe = item.get("priceEarningsRatio")
            if raw_date is None or raw_pe is None:
                continue

            try:
                value = float(raw_pe)
            except (TypeError, ValueError):
                continue

            if not value > 0:
                continue

            try:
                timestamp = self._epoch_seconds(str(raw_date))
            except ValueError:
                continue

            points.append(FundamentalPoint(time=timestamp, value=value))

        unique = {point.time: point for point in points}
        return sorted(unique.values(), key=lambda point: point.time)
