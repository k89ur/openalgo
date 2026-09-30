from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, timedelta

import requests

BASE_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date}_F_0000.csv.zip"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/153 Safari/537.36",
    "Accept": "application/zip,text/csv,*/*",
    "Referer": "https://www.nseindia.com/",
}


class NSEBhavcopyError(RuntimeError):
    pass


def _parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def fetch_latest(max_lookback_days: int = 7) -> tuple[date, dict[str, dict[str, float]]]:
    session = requests.Session()
    last_error = "NSE bhavcopy unavailable."
    today = date.today()

    for offset in range(max_lookback_days + 1):
        trading_date = today - timedelta(days=offset)
        url = BASE_URL.format(date=trading_date.strftime("%Y%m%d"))
        try:
            response = session.get(url, headers=HEADERS, timeout=30)
            if response.status_code != 200 or not response.content:
                last_error = f"NSE bhavcopy HTTP {response.status_code} for {trading_date.isoformat()}."
                continue

            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                if not names:
                    raise NSEBhavcopyError("NSE bhavcopy ZIP contains no CSV file.")
                with archive.open(names[0]) as raw:
                    text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                    rows = csv.DictReader(text)
                    result: dict[str, dict[str, float]] = {}
                    for row in rows:
                        symbol = str(row.get("TckrSymb") or "").strip().upper()
                        series = str(row.get("SctySrs") or "").strip().upper()
                        if not symbol or series not in {"EQ", "BE"}:
                            continue

                        open_price = _parse_number(row.get("OpnPric"))
                        high = _parse_number(row.get("HghPric"))
                        low = _parse_number(row.get("LwPric"))
                        close = _parse_number(row.get("ClsPric"))
                        previous_close = _parse_number(row.get("PrvsClsgPric"))
                        if None in (open_price, high, low, close, previous_close):
                            continue

                        if symbol not in result or series == "EQ":
                            result[symbol] = {
                                "open": open_price,
                                "high": high,
                                "low": low,
                                "close": close,
                                "previous_close": previous_close,
                            }

                    if result:
                        return trading_date, result

            last_error = f"NSE bhavcopy for {trading_date.isoformat()} contained no equity rows."
        except Exception as exc:
            last_error = f"{trading_date.isoformat()}: {exc}"

    raise NSEBhavcopyError(last_error)
