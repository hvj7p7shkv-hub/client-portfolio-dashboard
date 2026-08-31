#!/usr/bin/env python3
"""
Refresh the "Suggested Adds / Stocks I Like" watch list.

Reads data/watchlist.csv (Name, Yahoo Ticker, Date Added, Note) and writes
data/watchlist_status.json with, for each name, how it has behaved since the
day it entered the filter: return since added, worst drawdown since added,
and where price sits versus the 50/200-DMA key levels plus RSI / relative
strength. New additions are meant to be watched closely early, so the point
of this file is to make "has it fallen below the key levels since we added
it" visible at a glance on the dashboard.

The technical maths is reused from refresh_technical_indicators so the watch
list and the holdings table read the same way.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

import pandas as pd

from refresh_technical_indicators import (
    analyse_frame,
    close_series,
    download_frames,
    normalize_symbol,
)


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def key_level_label(above_50: object, above_200: object) -> str:
    a50 = above_50 is True
    a200 = above_200 is True
    if not a50 and not a200:
        return "Below 50 & 200 DMA"
    if not a50:
        return "Below 50 DMA"
    if not a200:
        return "Below 200 DMA"
    return "Above key levels"


def since_added(frame: pd.DataFrame, date_added: str) -> dict[str, object]:
    close = close_series(frame)
    if close.empty:
        return {}
    close = close.dropna()
    close.index = pd.to_datetime(close.index)
    try:
        cutoff = pd.Timestamp(date_added)
    except Exception:
        cutoff = None

    window = close[close.index >= cutoff] if cutoff is not None else close
    if window.empty:
        # Added date is in the future or after the last bar: nothing to track yet.
        return {
            "entryPrice": None,
            "entryDate": None,
            "lastPrice": _finite(close.iloc[-1]),
            "sinceAddedPct": None,
            "drawdownSincePct": None,
            "lowSinceAdded": None,
        }

    entry = float(window.iloc[0])
    latest = float(close.iloc[-1])
    low = float(window.min())
    return {
        "entryPrice": round(entry, 2),
        "entryDate": window.index[0].strftime("%Y-%m-%d"),
        "lastPrice": round(latest, 2),
        "sinceAddedPct": round((latest / entry - 1) * 100, 2) if entry else None,
        "drawdownSincePct": round((low / entry - 1) * 100, 2) if entry else None,
        "lowSinceAdded": round(low, 2),
    }


def build_records(input_path: Path, period: str, benchmark: str) -> list[dict[str, object]]:
    watch = pd.read_csv(input_path)
    watch = watch[watch["Name"].astype(str).str.strip() != ""].copy()
    watch["Yahoo Ticker"] = watch.apply(
        lambda row: str(row["Yahoo Ticker"]).strip()
        if str(row.get("Yahoo Ticker", "")).strip()
        else normalize_symbol(row["Name"]),
        axis=1,
    )

    symbols = watch["Yahoo Ticker"].astype(str).tolist()
    frames = download_frames(symbols + [benchmark], period)
    benchmark_close = close_series(frames.get(benchmark))
    today = dt.date.today()

    records: list[dict[str, object]] = []
    for _, row in watch.iterrows():
        name = str(row["Name"]).strip()
        ticker = str(row["Yahoo Ticker"]).strip()
        date_added = str(row["Date Added"]).strip()
        note = str(row.get("Note", "") or "").strip()

        record: dict[str, object] = {
            "name": name,
            "ticker": ticker,
            "dateAdded": date_added,
            "note": note,
            "downloaded": False,
            "error": "",
        }

        try:
            days_tracked = (today - dt.date.fromisoformat(date_added)).days
        except ValueError:
            days_tracked = None
        record["daysTracked"] = days_tracked

        frame = frames.get(ticker)
        try:
            metrics = analyse_frame(frame, benchmark_close)
        except Exception as exc:  # noqa: BLE001 - want the message on the card
            record["error"] = str(exc)
            records.append(record)
            continue

        above_50 = metrics.get("Above 50DMA")
        above_200 = metrics.get("Above 200DMA")
        record.update(
            {
                "downloaded": True,
                "technicalStatus": metrics.get("Technical Status"),
                "technicalScore": metrics.get("Technical Score"),
                "technicalNote": metrics.get("Technical Note"),
                "rsi14": _finite(metrics.get("RSI 14")),
                "rsVs50Pct": _finite(metrics.get("RS vs 50D %")),
                "rs3mPct": _finite(metrics.get("RS 3M %")),
                "pfSignal": metrics.get("P&F Signal"),
                "above50DMA": bool(above_50) if above_50 is not None else None,
                "above200DMA": bool(above_200) if above_200 is not None else None,
                "dist50DMAPct": _finite(metrics.get("50DMA Distance %")),
                "dist200DMAPct": _finite(metrics.get("200DMA Distance %")),
                "high52wDistPct": _finite(metrics.get("52W High Distance %")),
                "keyLevel": key_level_label(above_50, above_200),
                "belowKeyLevel": bool(above_50 is not True or above_200 is not True),
            }
        )
        record.update(since_added(frame, date_added))
        records.append(record)

    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the suggested-adds watch list.")
    parser.add_argument("--input", type=Path, default=Path("data/watchlist.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/watchlist_status.json"))
    parser.add_argument("--period", default="2y")
    parser.add_argument("--benchmark", default="^NSEMDCP50")
    args = parser.parse_args()

    records = build_records(args.input, args.period, args.benchmark)
    payload = {
        "generatedAt": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "benchmark": args.benchmark,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    ok = sum(1 for record in records if record.get("downloaded"))
    below = sum(1 for record in records if record.get("belowKeyLevel"))
    print(f"Watch-list names: {len(records)}")
    print(f"Technicals downloaded: {ok}")
    print(f"Below a key level: {below}")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
