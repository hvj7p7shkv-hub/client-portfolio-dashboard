#!/usr/bin/env python3
"""
Add technical indicators to the client portfolio CSV.

The output remains a single holdings CSV so the GitHub Pages workflow can
refresh prices, update technicals, and rebuild the dashboard in one pass.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - handled at runtime
    yf = None


TECHNICAL_COLUMNS = [
    "Technical Downloaded",
    "Technical Status",
    "Technical Score",
    "Technical Note",
    "RS vs 50D %",
    "RS 3M %",
    "RS Leader",
    "RSI 14",
    "P&F Signal",
    "Above 50DMA",
    "Above 200DMA",
    "50DMA Distance %",
    "200DMA Distance %",
    "52W High Distance %",
    "Technical Error",
]


def clean_number(value: object) -> float:
    text = str(value).replace(",", "").replace("%", "").replace("+", "").strip()
    try:
        return float(text)
    except ValueError:
        return math.nan


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


def close_series(frame: pd.DataFrame | None) -> pd.Series:
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    for column in ("Adj Close", "Close"):
        series = numeric_series(frame, column)
        if not series.empty:
            return series
    return pd.Series(dtype=float)


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = gain / loss.replace(0, math.nan)
    return 100 - (100 / (1 + rs))


def pct_return(series: pd.Series, days: int) -> float | None:
    clean = series.dropna()
    if len(clean) <= days:
        return None
    return float((clean.iloc[-1] / clean.iloc[-days - 1] - 1) * 100)


@dataclass
class PnfColumn:
    kind: str
    boxes: list[int]


def build_pnf(close: pd.Series, box_size: float, reversal: int = 3) -> list[PnfColumn]:
    prices = close.dropna()
    if prices.empty or box_size <= 0:
        return []

    def box(price: float) -> int:
        return int(math.floor(price / box_size))

    columns: list[PnfColumn] = []
    current_kind: str | None = None
    current_boxes: list[int] = []
    first_box = box(float(prices.iloc[0]))

    for price_value in prices.iloc[1:]:
        price_box = box(float(price_value))
        if current_kind is None:
            if price_box > first_box:
                current_kind = "X"
                current_boxes = list(range(first_box + 1, price_box + 1))
            elif price_box < first_box:
                current_kind = "O"
                current_boxes = list(range(first_box - 1, price_box - 1, -1))
            continue

        high_box = max(current_boxes)
        low_box = min(current_boxes)
        if current_kind == "X":
            if price_box > high_box:
                current_boxes.extend(range(high_box + 1, price_box + 1))
            elif price_box <= high_box - reversal:
                columns.append(PnfColumn("X", sorted(set(current_boxes))))
                current_kind = "O"
                current_boxes = list(range(high_box - 1, price_box - 1, -1))
        elif price_box < low_box:
            current_boxes.extend(range(low_box - 1, price_box - 1, -1))
        elif price_box >= low_box + reversal:
            columns.append(PnfColumn("O", sorted(set(current_boxes))))
            current_kind = "X"
            current_boxes = list(range(low_box + 1, price_box + 1))

    if current_kind and current_boxes:
        columns.append(PnfColumn(current_kind, sorted(set(current_boxes))))
    return columns


def pnf_signal(columns: list[PnfColumn]) -> str:
    if len(columns) < 2:
        return "Insufficient P&F structure"
    latest = columns[-1]
    previous_same = [column for column in columns[:-1] if column.kind == latest.kind]
    if latest.kind == "X" and previous_same and max(latest.boxes) > max(previous_same[-1].boxes):
        return "Bullish P&F breakout"
    if latest.kind == "O" and previous_same and min(latest.boxes) < min(previous_same[-1].boxes):
        return "Bearish P&F breakdown"
    if latest.kind == "X":
        return "Rising P&F column, no fresh breakout yet"
    return "Falling P&F column, wait for reversal"


def download_batch(tickers: list[str], period: str) -> pd.DataFrame:
    if yf is None or not tickers:
        return pd.DataFrame()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return yf.download(
                tickers=tickers,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
                timeout=45,
            )
    except Exception:
        return pd.DataFrame()


def extract_frame(downloaded: pd.DataFrame, ticker: str, batch_size: int) -> pd.DataFrame | None:
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


def download_single(ticker: str, period: str) -> pd.DataFrame | None:
    downloaded = download_batch([ticker], period)
    return extract_frame(downloaded, ticker, 1)


def download_frames(primary_symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    unique = sorted(set(primary_symbols))
    batch_size = 35
    for start in range(0, len(unique), batch_size):
        batch = unique[start : start + batch_size]
        downloaded = download_batch(batch, period)
        for ticker in batch:
            frame = extract_frame(downloaded, ticker, len(batch))
            if frame is not None and not close_series(frame).empty:
                frames[ticker] = frame

    for symbol in unique:
        if symbol in frames:
            continue
        for fallback in fallback_symbols(symbol):
            frame = download_single(fallback, period)
            if frame is not None and not close_series(frame).empty:
                frames[symbol] = frame
                break
    return frames


def relative_strength(close: pd.Series, benchmark_close: pd.Series) -> tuple[float | None, float | None, bool | None]:
    aligned = pd.concat([close.rename("stock"), benchmark_close.rename("benchmark")], axis=1).dropna()
    aligned = aligned[aligned["benchmark"] > 0]
    if len(aligned) < 60:
        return None, None, None
    ratio = aligned["stock"] / aligned["benchmark"]
    ratio_ma_50 = ratio.rolling(50).mean()
    if pd.isna(ratio_ma_50.iloc[-1]) or ratio_ma_50.iloc[-1] == 0:
        rs_vs_50 = None
    else:
        rs_vs_50 = float((ratio.iloc[-1] / ratio_ma_50.iloc[-1] - 1) * 100)
    rs_3m = pct_return(ratio, 63)
    leader = bool(rs_vs_50 is not None and rs_vs_50 > 0)
    return rs_vs_50, rs_3m, leader


def score_and_status(metrics: dict[str, object]) -> tuple[int, str, str]:
    score = 0
    reasons: list[str] = []

    rs_vs_50 = metrics.get("RS vs 50D %")
    rsi_14 = metrics.get("RSI 14")
    above_50 = metrics.get("Above 50DMA") is True
    above_200 = metrics.get("Above 200DMA") is True
    pnf = str(metrics.get("P&F Signal") or "")

    if above_50:
        score += 20
        reasons.append("price is above the 50DMA")
    if above_200:
        score += 20
        reasons.append("price is above the 200DMA")
    if isinstance(rs_vs_50, (int, float)) and math.isfinite(float(rs_vs_50)):
        if float(rs_vs_50) > 0:
            score += 25
            reasons.append("relative strength is above its 50-day average")
        elif float(rs_vs_50) < -3:
            score -= 10
            reasons.append("relative strength is below its 50-day average")
    if isinstance(rsi_14, (int, float)) and math.isfinite(float(rsi_14)):
        if 45 <= float(rsi_14) <= 70:
            score += 15
            reasons.append("RSI is in a constructive momentum zone")
        elif float(rsi_14) > 75:
            score -= 5
            reasons.append("RSI is extended")
        elif float(rsi_14) < 35:
            score -= 10
            reasons.append("RSI is weak or oversold")
    if "Bullish" in pnf:
        score += 20
        reasons.append("P&F shows a bullish breakout")
    elif "Bearish" in pnf:
        score -= 25
        reasons.append("P&F shows a bearish breakdown")

    score = max(0, min(100, score))
    if score >= 75:
        status = "Leader / hold"
    elif score >= 60:
        status = "Constructive"
    elif score <= 25 or "Bearish" in pnf:
        status = "Risk review"
    elif not above_50 and not above_200:
        status = "Loss + weak structure"
    else:
        status = "Monitor"

    note = "; ".join(reasons) if reasons else "Technical data was not strong enough to classify clearly."
    return score, status, note.capitalize()


def analyse_frame(frame: pd.DataFrame, benchmark_close: pd.Series) -> dict[str, object]:
    close = close_series(frame)
    if len(close) < 220:
        raise ValueError("Not enough daily price history")

    latest = float(close.iloc[-1])
    sma_50 = float(close.rolling(50).mean().iloc[-1])
    sma_200 = float(close.rolling(200).mean().iloc[-1])
    high_52w = float(close.tail(252).max())
    rsi_14 = float(rsi(close).iloc[-1])
    rs_vs_50, rs_3m, rs_leader = relative_strength(close, benchmark_close)
    box_size = max(latest * 0.02, 0.01)
    pnf = pnf_signal(build_pnf(close, box_size=box_size, reversal=3))

    metrics: dict[str, object] = {
        "RS vs 50D %": rs_vs_50,
        "RS 3M %": rs_3m,
        "RS Leader": rs_leader,
        "RSI 14": round(rsi_14, 2),
        "P&F Signal": pnf,
        "Above 50DMA": bool(latest > sma_50),
        "Above 200DMA": bool(latest > sma_200),
        "50DMA Distance %": (latest / sma_50 - 1) * 100 if sma_50 else None,
        "200DMA Distance %": (latest / sma_200 - 1) * 100 if sma_200 else None,
        "52W High Distance %": (latest / high_52w - 1) * 100 if high_52w else None,
    }
    score, status, note = score_and_status(metrics)
    metrics.update(
        {
            "Technical Downloaded": True,
            "Technical Status": status,
            "Technical Score": score,
            "Technical Note": note,
            "Technical Error": "",
        }
    )
    return metrics


def clear_technical_columns(data: pd.DataFrame) -> pd.DataFrame:
    for column in TECHNICAL_COLUMNS:
        if column not in data.columns:
            data[column] = None
    return data


def refresh(input_path: Path, output_path: Path, period: str, benchmark: str) -> tuple[int, int]:
    data = pd.read_csv(input_path)
    holdings_mask = data["Name"].astype(str).str.strip().str.lower() != "total"
    data = clear_technical_columns(data)
    data.loc[holdings_mask, "Yahoo Ticker"] = data.loc[holdings_mask].apply(
        lambda row: row["Yahoo Ticker"] if str(row.get("Yahoo Ticker", "")).strip() else normalize_symbol(row["Name"]),
        axis=1,
    )

    symbols = data.loc[holdings_mask, "Yahoo Ticker"].astype(str).tolist()
    frames = download_frames(symbols + [benchmark], period)
    benchmark_frame = frames.get(benchmark)
    benchmark_close = close_series(benchmark_frame)

    downloaded_count = 0
    failed_count = 0
    for index, row in data.loc[holdings_mask].iterrows():
        symbol = str(row["Yahoo Ticker"])
        frame = frames.get(symbol)
        try:
            metrics = analyse_frame(frame, benchmark_close)
            downloaded_count += 1
        except Exception as exc:
            metrics = {
                "Technical Downloaded": False,
                "Technical Status": "Not downloaded",
                "Technical Score": None,
                "Technical Note": "Technical data was not available for this stock in the latest refresh.",
                "Technical Error": str(exc),
            }
            failed_count += 1
        for key, value in metrics.items():
            data.loc[index, key] = value

    data.to_csv(output_path, index=False)
    return downloaded_count, failed_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh client portfolio technical indicators.")
    parser.add_argument("--input", type=Path, default=Path("data/holdings.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/holdings.csv"))
    parser.add_argument("--period", default="2y")
    parser.add_argument("--benchmark", default="^NSEMDCP50")
    args = parser.parse_args()

    downloaded, failed = refresh(args.input, args.output, args.period, args.benchmark)
    print(f"Technical indicators downloaded: {downloaded}")
    print(f"Technical fallbacks: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
