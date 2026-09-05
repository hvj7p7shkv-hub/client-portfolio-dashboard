#!/usr/bin/env python3
"""One-off backfill of data/eod_history.csv for the period before the daily
recorder started: 4 Aug 2026 (when the OLD 89-holding basket's composition was
set, confirmed unchanged every day through 4 Sep 2026) through 4 Sep 2026 --
priced with real historical closes, so it's not hypothetical for that window.
5 Sep 2026 onward uses the NEW (Shobhit) basket, which is already recorded.

This does NOT try to price the new basket backward before the cutover, and
does NOT try to price the old basket forward past the cutover -- each basket
is only valued over the window it was actually held.
"""
import subprocess
import io
import pandas as pd
import yfinance as yf

OLD_BASKET_COMMIT = "996fd57"   # last commit with the old 89-holding basket, pre-swap
START = "2026-08-04"
CUTOVER_LAST_DAY = "2026-09-04"   # last day the old basket was held (Friday)


def old_basket():
    raw = subprocess.run(["git", "show", f"{OLD_BASKET_COMMIT}:data/holdings.csv"],
                         capture_output=True, text=True).stdout
    df = pd.read_csv(io.StringIO(raw))
    df = df[df.Name.astype(str).str.strip().str.lower() != "total"].copy()
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    return df[["Name", "Quantity", "Yahoo Ticker"]].dropna(subset=["Yahoo Ticker"])


def daily_value(basket: pd.DataFrame, start: str, end: str) -> pd.Series:
    tickers = basket["Yahoo Ticker"].tolist()
    px = yf.download(tickers, start=start, end=pd.Timestamp(end) + pd.Timedelta(days=1),
                     progress=False, auto_adjust=False)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame(tickers[0])
    px = px.ffill()
    qty = basket.set_index("Yahoo Ticker")["Quantity"]
    missing = [t for t in tickers if t not in px.columns]
    if missing:
        print(f"  no price history for: {missing}")
    val = (px[[t for t in tickers if t in px.columns]] * qty.reindex(px.columns)).sum(axis=1)
    return val


def bench_close(ticker: str, start: str, end: str) -> pd.Series:
    h = yf.download(ticker, start=start, end=pd.Timestamp(end) + pd.Timedelta(days=1),
                    progress=False, auto_adjust=False)["Close"]
    return h.iloc[:, 0] if isinstance(h, pd.DataFrame) else h


def main():
    old = old_basket()
    print(f"old basket: {len(old)} names, {old.Quantity.sum():.0f} total shares")
    old_val = daily_value(old, START, CUTOVER_LAST_DAY)
    n50 = bench_close("^NSEI", START, CUTOVER_LAST_DAY)
    nmc = bench_close("^NSEMDCP50", START, CUTOVER_LAST_DAY)

    rows = []
    for d in old_val.index:
        ds = d.date().isoformat()
        rows.append(dict(date=ds, portfolio_invested=None, portfolio_value=round(float(old_val[d]), 2),
                         nifty50_close=float(n50.get(d, float("nan"))) if d in n50.index else None,
                         niftymidcap50_close=float(nmc.get(d, float("nan"))) if d in nmc.index else None,
                         basket="old_89"))
    hist_new = pd.read_csv("data/eod_history.csv")
    hist_new["basket"] = "new_92"
    backfill = pd.DataFrame(rows)

    combined = pd.concat([backfill, hist_new], ignore_index=True, sort=False)
    combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)

    base = combined.iloc[0]
    combined["portfolio_index"] = (combined["portfolio_value"] / base["portfolio_value"] * 100).round(2)
    for label, col in [("nifty50", "nifty50_close"), ("niftymidcap50", "niftymidcap50_close")]:
        if pd.notna(base.get(col)) and base[col]:
            combined[f"{label}_index"] = (combined[col] / base[col] * 100).round(2)

    combined.to_csv("data/eod_history.csv", index=False)
    print(combined.to_string(index=False))
    print(f"\n{len(combined)} days written -> data/eod_history.csv  ({combined.iloc[0].date} -> {combined.iloc[-1].date})")


if __name__ == "__main__":
    main()
