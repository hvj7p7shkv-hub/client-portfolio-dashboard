#!/usr/bin/env python3
"""Append (or update) today's end-of-day snapshot of the portfolio's value,
alongside Nifty 50 and Nifty Midcap 50 closes, to data/eod_history.csv.

Idempotent by date: if today's row already exists (an intraday run earlier in
the day), it is replaced rather than duplicated -- so the value recorded for
a given day is always the LAST refresh of that day, which (given the workflow's
schedule ending ~16:17 IST, after the 15:30 close) is effectively the EOD mark.

Also computes each series indexed to 100 at the first recorded date, so the
portfolio's growth can be read directly against the two benchmarks.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

BENCHMARKS = {"nifty50": "^NSEI", "niftymidcap50": "^NSEMDCP50"}


def clean_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "").str.replace("%", "").str.replace("+", ""),
        errors="coerce",
    )


def latest_close(ticker: str) -> float | None:
    if yf is None:
        return None
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            return None
        return float(hist["Close"].dropna().iloc[-1])
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdings", default="data/holdings.csv")
    ap.add_argument("--output", default="data/eod_history.csv")
    ap.add_argument("--date", default=None, help="override the record date (YYYY-MM-DD), default: today IST")
    args = ap.parse_args()

    today = args.date or dt.datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()

    holdings = pd.read_csv(args.holdings)
    invested = clean_number(holdings["Invested Value"]).sum()
    current = clean_number(holdings["Current Value"]).sum()

    row = {"date": today, "portfolio_invested": round(invested, 2), "portfolio_value": round(current, 2),
           "basket": "new_92"}
    for label, ticker in BENCHMARKS.items():
        row[f"{label}_close"] = latest_close(ticker)

    out_path = Path(args.output)
    if out_path.exists():
        hist = pd.read_csv(out_path)
        hist = hist[hist["date"] != today]
        hist = pd.concat([hist, pd.DataFrame([row])], ignore_index=True)
    else:
        hist = pd.DataFrame([row])
    hist = hist.sort_values("date").reset_index(drop=True)

    # indexed-to-100 columns for direct comparison
    base = hist.iloc[0]
    hist["portfolio_index"] = (hist["portfolio_value"] / base["portfolio_value"] * 100).round(2)
    for label in BENCHMARKS:
        col = f"{label}_close"
        if pd.notna(base.get(col)) and base[col]:
            hist[f"{label}_index"] = (hist[col] / base[col] * 100).round(2)

    hist.to_csv(out_path, index=False)
    print(f"recorded {today}: portfolio value {current:,.0f} (index {hist.iloc[-1]['portfolio_index']:.2f}), "
          f"{len(hist)} days of history -> {out_path}")


if __name__ == "__main__":
    main()
