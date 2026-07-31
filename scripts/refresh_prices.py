#!/usr/bin/env python3
"""
Refresh a portfolio CSV with latest Yahoo Finance prices.

The script keeps the broker CSV shape stable so the dashboard builder can use
it directly. If a quote cannot be downloaded, the existing CSV value is kept.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import math
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - handled at runtime
    yf = None


NUMERIC_COLUMNS = [
    "Quantity",
    "Avg. Price",
    "LTP",
    "Invested Value",
    "Current Value",
    "Profit/Loss",
    "Profit/Loss %",
    "Todays Profit/Loss",
    "Todays Profit/Loss %",
]


def clean_number(value: object) -> float:
    text = str(value).replace(",", "").replace("%", "").replace("+", "").strip()
    try:
        return float(text)
    except ValueError:
        return math.nan


def clean_numeric_columns(data: pd.DataFrame) -> pd.DataFrame:
    for column in NUMERIC_COLUMNS:
        if column in data.columns:
            data[column] = data[column].map(clean_number)
    return data


def normalize_symbol(name: object) -> str:
    symbol = str(name).strip().upper()
    symbol = re.sub(r"\s+", "", symbol)
    symbol = re.sub(r"-(EQ|BE)$", "", symbol)
    if symbol.startswith("^") or "." in symbol:
        return symbol
    return f"{symbol}.NS"


def fallback_symbols(symbol: str) -> list[str]:
    symbols = [symbol]
    if symbol.endswith(".NS"):
        symbols.append(symbol[:-3] + ".BO")
    return symbols


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame is None or frame.empty:
        return pd.Series(dtype=float)

    values: pd.Series | pd.DataFrame | None = None
    if isinstance(frame.columns, pd.MultiIndex):
        if column in frame.columns.get_level_values(0):
            values = frame.xs(column, axis=1, level=0)
        elif column in frame.columns.get_level_values(1):
            values = frame.xs(column, axis=1, level=1)
    elif column in frame.columns:
        values = frame[column]

    if values is None:
        return pd.Series(dtype=float)
    if isinstance(values, pd.DataFrame):
        for subcolumn in values.columns:
            series = pd.to_numeric(values[subcolumn], errors="coerce").dropna()
            if not series.empty:
                return series.astype(float)
        return pd.Series(dtype=float)
    return pd.to_numeric(values, errors="coerce").dropna().astype(float)


def quote_from_frame(frame: pd.DataFrame) -> dict[str, object] | None:
    if frame is None or frame.empty:
        return None
    close = numeric_series(frame, "Close")
    if close.empty:
        return None
    last = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) >= 2 else last
    price_date = close.index[-1]
    if hasattr(price_date, "date"):
        price_date = price_date.date().isoformat()
    return {"last": last, "previous": previous, "date": str(price_date)}


def extract_ticker_frame(downloaded: pd.DataFrame, ticker: str, batch_size: int) -> pd.DataFrame | None:
    if downloaded is None or downloaded.empty:
        return None
    if isinstance(downloaded.columns, pd.MultiIndex):
        level_0 = set(downloaded.columns.get_level_values(0))
        level_1 = set(downloaded.columns.get_level_values(1))
        if ticker in level_0:
            return downloaded[ticker].dropna(how="all")
        if ticker in level_1:
            return downloaded.xs(ticker, axis=1, level=1).dropna(how="all")
        return None
    if batch_size == 1:
        return downloaded.dropna(how="all")
    return None


def download_batch(tickers: list[str]) -> dict[str, dict[str, object]]:
    if yf is None or not tickers:
        return {}
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            downloaded = yf.download(
                tickers=tickers,
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
                timeout=30,
            )
    except Exception:
        return {}

    quotes: dict[str, dict[str, object]] = {}
    for ticker in tickers:
        quote = quote_from_frame(extract_ticker_frame(downloaded, ticker, len(tickers)))
        if quote:
            quotes[ticker] = quote
    return quotes


def download_single(ticker: str) -> dict[str, object] | None:
    if yf is None:
        return None
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            downloaded = yf.download(
                tickers=ticker,
                period="5d",
                interval="1d",
                auto_adjust=False,
                progress=False,
                timeout=20,
            )
    except Exception:
        return None
    return quote_from_frame(downloaded)


def download_quotes(primary_symbols: list[str]) -> dict[str, dict[str, object]]:
    quotes: dict[str, dict[str, object]] = {}
    unique = sorted(set(primary_symbols))
    batch_size = 45
    for start in range(0, len(unique), batch_size):
        quotes.update(download_batch(unique[start : start + batch_size]))

    for symbol in unique:
        if symbol in quotes:
            continue
        for fallback in fallback_symbols(symbol):
            quote = download_single(fallback)
            if quote:
                quote["downloaded_ticker"] = fallback
                quotes[symbol] = quote
                break
    return quotes


def recalculate_row(row: pd.Series, quote: dict[str, object] | None) -> pd.Series:
    if quote:
        row["LTP"] = float(quote["last"])
        row["Price Source"] = str(quote.get("downloaded_ticker") or row["Yahoo Ticker"])
        row["Price Date"] = str(quote.get("date") or "")
    else:
        row["Price Source"] = "CSV fallback"
        row["Price Date"] = ""

    quantity = clean_number(row.get("Quantity"))
    average_price = clean_number(row.get("Avg. Price"))
    ltp = clean_number(row.get("LTP"))
    previous_close = clean_number(quote.get("previous")) if quote else math.nan

    if math.isfinite(quantity) and math.isfinite(average_price) and math.isfinite(ltp):
        invested = quantity * average_price
        current = quantity * ltp
        profit_loss = current - invested
        row["Invested Value"] = invested
        row["Current Value"] = current
        row["Profit/Loss"] = profit_loss
        row["Profit/Loss %"] = profit_loss / invested * 100 if invested else math.nan
        if math.isfinite(previous_close) and previous_close:
            row["Todays Profit/Loss"] = quantity * (ltp - previous_close)
            row["Todays Profit/Loss %"] = (ltp / previous_close - 1) * 100
    return row


def update_total_row(data: pd.DataFrame, holdings_mask: pd.Series) -> pd.DataFrame:
    total_mask = ~holdings_mask
    if not total_mask.any():
        return data
    holdings = data.loc[holdings_mask]
    total_index = data.index[total_mask][0]
    invested = float(holdings["Invested Value"].sum())
    current = float(holdings["Current Value"].sum())
    pnl = float(holdings["Profit/Loss"].sum())
    today = float(holdings["Todays Profit/Loss"].sum())
    data.loc[total_index, "Invested Value"] = invested
    data.loc[total_index, "Current Value"] = current
    data.loc[total_index, "Profit/Loss"] = pnl
    data.loc[total_index, "Profit/Loss %"] = pnl / invested * 100 if invested else math.nan
    data.loc[total_index, "Todays Profit/Loss"] = today
    data.loc[total_index, "Todays Profit/Loss %"] = today / (current - today) * 100 if current != today else math.nan
    return data


def refresh(input_path: Path, output_path: Path) -> tuple[int, int]:
    data = pd.read_csv(input_path)
    holdings_mask = data["Name"].astype(str).str.strip().str.lower() != "total"
    data = clean_numeric_columns(data)
    data.loc[holdings_mask, "Yahoo Ticker"] = data.loc[holdings_mask, "Name"].map(normalize_symbol)
    for column in ("Yahoo Ticker", "Price Source", "Price Date"):
        if column not in data.columns:
            data[column] = ""
        data[column] = data[column].astype("object")
    tickers = data.loc[holdings_mask, "Yahoo Ticker"].astype(str).tolist()
    quotes = download_quotes(tickers)

    live_count = 0
    fallback_count = 0
    for index, row in data.loc[holdings_mask].iterrows():
        quote = quotes.get(str(row["Yahoo Ticker"]))
        if quote:
            live_count += 1
        else:
            fallback_count += 1
        data.loc[index] = recalculate_row(row, quote)

    data = update_total_row(data, holdings_mask)
    data["Last Refreshed"] = dt.datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="minutes")
    data.to_csv(output_path, index=False)
    return live_count, fallback_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh portfolio prices from Yahoo Finance.")
    parser.add_argument("--input", type=Path, default=Path("data/holdings.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/holdings.csv"))
    args = parser.parse_args()

    live_count, fallback_count = refresh(args.input, args.output)
    print(f"Live Yahoo quotes: {live_count}")
    print(f"CSV fallbacks: {fallback_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
