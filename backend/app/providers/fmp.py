from __future__ import annotations

import os
from datetime import date, datetime, timezone

import requests

from app.providers.fundamentals import HistoricalEpsPoint


class FmpFundamentalsProvider:
    """Historical earnings provider used to build a calculated P/E series.

    PIPSGOX fetches reported quarterly EPS and builds TTM EPS locally.
    The filing date is used as the effective date so the chart does not
    apply a result before it was publicly reported.
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

    def get_historical_eps(self, symbol: str, limit: int = 40) -> list[HistoricalEpsPoint]:
        if not self.configured:
            raise ValueError(
                "Historical P/E is not configured. Set FMP_API_KEY to enable it."
            )

        if limit < 4 or limit > 200:
            raise ValueError("Historical EPS limit must be between 4 and 200 quarters.")

        mapped = self._map_symbol(symbol)
        response = requests.get(
            f"{self.base_url}/income-statement/{mapped}",
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
            raise ValueError("Fundamentals provider returned an invalid income statement payload.")

        quarterly: list[tuple[int, float]] = []
        seen_dates: set[int] = set()

        for item in payload:
            if not isinstance(item, dict):
                continue

            # Use the filing/acceptance date when available. This is the date
            # from which the reported EPS is allowed to affect the chart.
            raw_effective = item.get("fillingDate") or item.get("acceptedDate") or item.get("date")
            if raw_effective is None:
                continue

            raw_eps = item.get("epsdiluted")
            if raw_eps is None:
                raw_eps = item.get("eps")

            try:
                effective_time = self._epoch_seconds(str(raw_effective))
                eps = float(raw_eps)
            except (TypeError, ValueError):
                continue

            if not (effective_time > 0 and eps == eps):
                continue

            if effective_time in seen_dates:
                continue

            seen_dates.add(effective_time)
            quarterly.append((effective_time, eps))

        quarterly.sort(key=lambda item: item[0])

        # Build TTM EPS from the four most recently reported quarterly EPS
        # values. Negative/zero TTM EPS is retained because the frontend will
        # correctly leave P/E undefined when earnings are non-positive.
        result: list[HistoricalEpsPoint] = []
        for index in range(len(quarterly)):
            if index < 3:
                continue
            ttm_eps = sum(value for _, value in quarterly[index - 3:index + 1])
            if not (ttm_eps == ttm_eps):
                continue
            result.append(
                HistoricalEpsPoint(
                    time=quarterly[index][0],
                    ttm_eps=ttm_eps,
                )
            )

        return result
