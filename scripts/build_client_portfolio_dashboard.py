#!/usr/bin/env python3
"""
Build client portfolio dashboards from a broker holdings CSV.

Outputs:
- local/index.html: full advisor view with values.
- client_safe/index.html: online-ready view without quantities, buy prices, or rupee values.
- client_action_queue.csv: coordination checklist generated from the snapshot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import re
from pathlib import Path

import pandas as pd


ROOT = Path("/Users/anshumanomjhunjhunwala/Documents/Codex/2026-07-17/making-a-lightweight")
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "client_portfolio_dashboard"

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
    "RS vs 50D %",
    "RS 3M %",
    "RSI 14",
    "50DMA Distance %",
    "200DMA Distance %",
    "52W High Distance %",
    "Technical Score",
]

TECHNICAL_FIELDS = [
    "Technical Status",
    "Technical Score",
    "Technical Note",
    "RS Trend",
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
    "Technical Downloaded",
    "Technical Error",
]


# Names the advisor client likes and would consider adding. Purely a watch
# list — these are not holdings and are never counted in portfolio value,
# weights, P&L, or any of the summary metrics.
SUGGESTED_ADDS = [
    "VSSL",
    "Arvind Ltd",
    "Welspun Enterprises",
    "Fineotex Chemical",
    "BLS E-Services",
    "IG Petrochemicals",
    "Sona BLW Precision Forgings",
    "Grasim",
    "Bosch",
    "Nykaa",
    "Tamilnad Mercantile Bank",
    "Lumax Auto Technologies",
]

# From the same "stocks I like" list but already held, so shown only as a note.
SUGGESTED_ADDS_ALREADY_HELD = [
    "BHEL",
    "ICICI Bank",
    "Bajaj Auto",
    "Oracle Financial Services (OFSS)",
    "Aurobindo Pharma",
]

# --- "Ask about this dashboard" assistant -------------------------------------
# A floating panel wired to the Investezee AI worker's ADVISOR endpoint
# (advisor-ai.investezee.com). This is a private client dashboard, so the
# assistant may give buy / sell / hold / sizing views. It is handed a
# technical + P&L slice of the dashboard (percentages and readings, plus the
# dashboard's own bucket / discussion notes) — no rupee amounts, quantities,
# or cost basis. Self-contained: its own <style>, endpoint hard-coded.
# EXPERIMENTAL — running it to see how the model does before settling on an approach.
AI_ENDPOINT = "https://advisor-ai.investezee.com/"

AI_TECHNICAL_FIELDS = [
    "Technical Status", "Technical Note", "RS vs 50D %", "RS 3M %", "RSI 14",
    "P&F Signal", "Above 50DMA", "Above 200DMA", "200DMA Distance %",
    "52W High Distance %",
]


def ai_context(data: pd.DataFrame) -> dict[str, object]:
    """A compact technical + P&L view of the dashboard for the assistant.

    Percentages, technical readings and the dashboard's own bucket / discussion
    notes — no rupee amounts, quantities, or cost basis. Capped at the 45
    largest positions so the slice stays inside the worker's context limit."""
    tech = [f for f in AI_TECHNICAL_FIELDS if f in data.columns]
    cols = [c for c in (["Symbol", "Weight %", "Profit/Loss %", "Portfolio Bucket",
                         "Coordination Priority", "Suggested Discussion"] + tech)
            if c in data.columns]
    ranked = data.sort_values("Weight %", ascending=False) if "Weight %" in data.columns else data
    frame = ranked[cols].head(45).where(pd.notna(ranked[cols].head(45)), None)
    holdings = json.loads(frame.to_json(orient="records"))
    holdings = [
        {k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}
        for row in holdings
    ]
    total = float(data["Current Value"].sum())
    invested = float(data["Invested Value"].sum())
    above50 = above200 = None
    if "Above 50DMA" in data.columns and len(data):
        above50 = round(float((data["Above 50DMA"].astype(str) == "True").mean() * 100), 1)
    if "Above 200DMA" in data.columns and len(data):
        above200 = round(float((data["Above 200DMA"].astype(str) == "True").mean() * 100), 1)
    return {
        "what": "Private client portfolio dashboard — point-in-time technical + P&L snapshot. "
                "No company fundamentals, news, or the client's wider finances / goals / risk profile.",
        "generatedAt": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "benchmark": "Nifty Midcap 50 (^NSEMDCP50)",
        "portfolio": {
            "holdings": int(len(data)),
            "returnPct": round((total / invested - 1) * 100, 2) if invested else None,
            "pctAbove50DMA": above50,
            "pctAbove200DMA": above200,
        },
        "holdings": holdings,
    }


AI_WIDGET = """
<style>
  .ai-fab{position:fixed;right:18px;bottom:18px;z-index:60;display:inline-flex;align-items:center;
  gap:8px;padding:12px 18px;border:0;border-radius:999px;background:#171916;color:#fff;
  font:inherit;font-weight:700;font-size:14px;cursor:pointer;box-shadow:0 8px 24px rgba(23,25,22,.26)}
  .ai-fab .dot{width:8px;height:8px;border-radius:50%;background:#3fd9a8;box-shadow:0 0 0 3px rgba(63,217,168,.28)}
  body.ai-open .ai-fab{opacity:0;pointer-events:none}
  .ai-dock{position:fixed;right:18px;bottom:18px;z-index:61;width:min(390px,calc(100vw - 32px));
  max-height:min(580px,calc(100vh - 32px));display:flex;flex-direction:column;background:#fffefa;
  border:1px solid #dce2d7;border-radius:14px;overflow:hidden;box-shadow:0 20px 54px rgba(0,0,0,.24);
  transform:translateY(16px) scale(.98);opacity:0;pointer-events:none;transition:transform .2s,opacity .2s}
  body.ai-open .ai-dock{transform:none;opacity:1;pointer-events:auto}
  .ai-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;
  padding:13px 15px;background:#171916;color:#fff}
  .ai-head strong{font-size:15px}
  .ai-head .ai-sub{font-size:12px;color:#b9c4c0;font-weight:500;margin-top:2px}
  .ai-x{background:transparent;border:0;color:#fff;font-size:22px;line-height:1;cursor:pointer;padding:0 2px}
  .ai-body{padding:13px 15px;overflow-y:auto}
  .ai-log{display:grid;gap:10px;margin:0 0 12px}
  .ai-log:empty{display:none}
  .ai-msg{padding:10px 13px;border-radius:10px;font-size:14px;line-height:1.5;max-width:88%;white-space:pre-wrap}
  .ai-msg.user{justify-self:end;background:#f0f1ec;border:1px solid #dce2d7}
  .ai-msg.bot{justify-self:start;background:#fff;border:1px solid #dce2d7}
  .ai-msg.err{justify-self:start;background:#f8e9e7;border:1px solid #e6c9c7;color:#b33e45}
  .ai-starters{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:10px}
  .ai-starter{border:1px solid #dce2d7;background:#fffefa;border-radius:999px;
  padding:6px 12px;font-size:12.5px;color:#171916;cursor:pointer;font-family:inherit}
  .ai-starter:hover{background:#f0f1ec}
  .ai-form{display:flex;gap:8px}
  .ai-form input{flex:1;padding:10px 12px;border:1px solid #dce2d7;border-radius:8px;
  font-family:inherit;font-size:14px;background:#fff;color:#171916}
  .ai-form button{padding:10px 18px;border:0;border-radius:8px;background:#171916;color:#fff;
  font-weight:650;font-family:inherit;cursor:pointer}
  .ai-form button:disabled{opacity:.5;cursor:default}
  @media(max-width:520px){
    .ai-dock{right:0;left:0;bottom:0;width:100%;max-height:82vh;border-radius:16px 16px 0 0}
    body.ai-open .ai-fab{display:none}
  }
</style>
<button class="ai-fab" id="ai-fab" aria-label="Ask about this dashboard">
  <span class="dot"></span>Ask about this dashboard</button>
<div class="ai-dock" id="ai-dock">
  <div class="ai-head"><div><strong>Ask about this dashboard</strong>
  <div class="ai-sub">A discussion aid for your portfolio</div></div>
  <button class="ai-x" id="ai-close" aria-label="Close">&times;</button></div>
  <div class="ai-body">
    <div class="ai-log" id="ai-log"></div>
    <div class="ai-starters" id="ai-starters">
      <button class="ai-starter" type="button">Talk me through my biggest position</button>
      <button class="ai-starter" type="button">Which holdings look weak right now?</button>
      <button class="ai-starter" type="button">Anything here I should be paying attention to?</button>
      <button class="ai-starter" type="button">Which names are lagging the midcap index?</button>
    </div>
    <form class="ai-form" id="ai-form">
      <input id="ai-input" autocomplete="off" placeholder="Ask about a holding&hellip;" maxlength="500">
      <button type="submit" id="ai-send">Ask</button></form>
    <p class="muted" style="font-size:11.5px;margin:8px 0 0">Answers are generated and can be wrong.
    A discussion aid on the technical picture only &mdash; not a formal recommendation. You and
    your advisor make the final call.</p>
  </div>
</div>
<script>
(function(){
  var fab=document.getElementById('ai-fab'),dock=document.getElementById('ai-dock'),
      form=document.getElementById('ai-form');
  if(!fab||!dock||!form) return;
  var log=document.getElementById('ai-log'),input=document.getElementById('ai-input'),
      send=document.getElementById('ai-send'),starters=document.getElementById('ai-starters');
  var context={};try{context=JSON.parse(document.getElementById('ai-context').textContent);}catch(e){}
  var endpoint="__AI_ENDPOINT__",hist=[];
  function open(v){document.body.classList.toggle('ai-open',v);if(v)setTimeout(function(){input.focus();},250);}
  fab.addEventListener('click',function(){open(!document.body.classList.contains('ai-open'));});
  document.getElementById('ai-close').addEventListener('click',function(){open(false);});
  function bubble(role,text){
    var d=document.createElement('div');
    d.className='ai-msg '+(role==='user'?'user':role==='err'?'err':'bot');
    d.textContent=text;log.appendChild(d);d.scrollIntoView({block:'nearest'});return d;
  }
  function ask(q){
    if(!q||send.disabled) return;
    if(starters) starters.style.display='none';
    bubble('user',q);hist.push({role:'user',content:q});
    input.value='';send.disabled=true;
    var wait=bubble('bot','\\u2026');
    fetch(endpoint,{method:'POST',referrerPolicy:'unsafe-url',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({context:context,messages:hist})})
    .then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});})
    .then(function(res){
      if(!res.ok||!res.j.reply){wait.remove();bubble('err',res.j.error||'The assistant is unavailable right now.');return;}
      wait.className='ai-msg bot';wait.textContent=res.j.reply;
      hist.push({role:'assistant',content:res.j.reply});
    })
    .catch(function(){wait.remove();bubble('err','Could not reach the assistant. The dashboard is unaffected.');})
    .finally(function(){send.disabled=false;input.focus();});
  }
  form.addEventListener('submit',function(e){e.preventDefault();ask((input.value||'').trim());});
  if(starters) starters.addEventListener('click',function(e){
    if(e.target.classList.contains('ai-starter')) ask((e.target.textContent||'').trim());
  });
})();
</script>
""".replace("__AI_ENDPOINT__", AI_ENDPOINT)


CHART_DIR_NAME = "watchlist_charts"


def load_watchlist_charts(source: Path) -> dict[str, list[dict[str, object]]]:
    """Read data/watchlist_charts.csv -> {TICKER: [{date, src, caption}, ...]}.

    Each row is an annotated chart image (saved under data/watchlist_charts/)
    the advisor wants pinned to a watch-list name so a running visual record
    builds up over time. Rows are returned oldest-first. A row whose image
    file is missing on disk is kept but flagged so it shows as pending.
    """
    charts_csv = source.parent / "watchlist_charts.csv"
    by_ticker: dict[str, list[dict[str, object]]] = {}
    if not charts_csv.exists():
        return by_ticker
    try:
        frame = pd.read_csv(charts_csv, dtype=str).fillna("")
    except (ValueError, OSError):
        return by_ticker
    for _, row in frame.iterrows():
        ticker = str(row.get("Ticker", "")).strip().upper()
        file_name = str(row.get("File", "")).strip()
        if not ticker or not file_name:
            continue
        entry = {
            "date": str(row.get("Date", "")).strip(),
            # Dashboard HTML is published at the site root next to data/, so
            # the image URL keeps the data/ prefix.
            "src": f"data/{CHART_DIR_NAME}/{file_name}",
            "caption": str(row.get("Caption", "")).strip(),
            "missing": not (source.parent / CHART_DIR_NAME / file_name).exists(),
        }
        by_ticker.setdefault(ticker, []).append(entry)
    for entries in by_ticker.values():
        entries.sort(key=lambda item: item["date"])
    return by_ticker


def load_watchlist(source: Path) -> dict[str, object]:
    """Read data/watchlist_status.json (written by refresh_watchlist.py) and
    attach any annotated charts from data/watchlist_charts.csv.

    Falls back to the bare SUGGESTED_ADDS name list when the enriched file is
    missing, so the dashboard still builds on a fresh checkout.
    """
    charts = load_watchlist_charts(source)
    candidate = source.parent / "watchlist_status.json"
    payload: dict[str, object] | None = None
    if candidate.exists():
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("records"):
                payload = loaded
        except (ValueError, OSError):
            payload = None
    if payload is None:
        payload = {
            "generatedAt": None,
            "records": [{"name": name, "dateAdded": None} for name in SUGGESTED_ADDS],
        }

    for record in payload.get("records", []):
        ticker = str(record.get("ticker", "")).strip().upper()
        record["charts"] = charts.get(ticker, [])
    return payload


def clean_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("+", "", regex=False)
        .str.strip(),
        errors="coerce",
    )


def read_portfolio(path: Path) -> pd.DataFrame:
    data = pd.read_csv(path)
    required = {"Name", "Current Value", "Invested Value", "Profit/Loss", "Profit/Loss %"}
    missing = sorted(required - set(data.columns))
    if missing:
        raise SystemExit(f"Missing required columns: {', '.join(missing)}")
    data = data[data["Name"].astype(str).str.strip().str.lower() != "total"].copy()
    for column in NUMERIC_COLUMNS:
        if column in data.columns:
            data[column] = clean_number(data[column])
    data["Symbol"] = data["Name"].astype(str).str.strip()
    total_current = data["Current Value"].sum()
    total_invested = data["Invested Value"].sum()
    data["Weight %"] = data["Current Value"] / total_current * 100 if total_current else 0
    data["P&L Contribution %"] = data["Profit/Loss"] / total_invested * 100 if total_invested else 0
    data["Portfolio Bucket"] = data.apply(classify_bucket, axis=1)
    data["Coordination Priority"] = data.apply(priority, axis=1)
    data["Suggested Discussion"] = data.apply(suggested_discussion, axis=1)
    return data


def classify_bucket(row: pd.Series) -> str:
    pnl_pct = float(row.get("Profit/Loss %") or 0)
    weight = float(row.get("Weight %") or 0)
    today_pct = float(row.get("Todays Profit/Loss %") or 0)
    if pnl_pct <= -25:
        return "Deep loss review"
    if pnl_pct <= -10:
        return "Loss review"
    if pnl_pct >= 75:
        return "Big winner"
    if pnl_pct >= 30:
        return "Winner"
    if weight >= 2.5:
        return "Core position"
    if today_pct <= -5:
        return "Event watch"
    return "Monitor"


def priority(row: pd.Series) -> str:
    bucket = str(row.get("Portfolio Bucket") or "")
    weight = float(row.get("Weight %") or 0)
    today_pct = float(row.get("Todays Profit/Loss %") or 0)
    pnl_pct = float(row.get("Profit/Loss %") or 0)
    if bucket in {"Deep loss review", "Big winner"} or abs(today_pct) >= 6:
        return "High"
    if bucket in {"Loss review", "Winner"} or weight >= 2.5 or pnl_pct <= -8:
        return "Medium"
    return "Low"


def suggested_discussion(row: pd.Series) -> str:
    bucket = str(row.get("Portfolio Bucket") or "")
    pnl_pct = float(row.get("Profit/Loss %") or 0)
    weight = float(row.get("Weight %") or 0)
    today_pct = float(row.get("Todays Profit/Loss %") or 0)
    if bucket == "Deep loss review":
        return "Decide whether thesis is still valid; avoid averaging without fresh reason."
    if bucket == "Loss review":
        return "Check support, results risk, and whether capital should rotate to stronger names."
    if bucket == "Big winner":
        return "Review position size and whether to protect gains with staged trimming rules."
    if bucket == "Winner":
        return "Hold if trend remains intact; discuss partial profit protection."
    if weight >= 2.5:
        return "Large allocation; confirm conviction and risk limit."
    if today_pct <= -5:
        return "Sharp daily fall; identify event reason before taking action."
    if pnl_pct < 0:
        return "Monitor; discuss only if technical structure weakens."
    return "No urgent action; keep in review list."


def money(value: object) -> str:
    try:
        return f"Rs {float(value):,.0f}"
    except Exception:
        return "-"


def pct(value: object) -> str:
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return "-"


def tone(value: object) -> str:
    try:
        number = float(value)
    except Exception:
        return "flat"
    if number > 0:
        return "positive"
    if number < 0:
        return "negative"
    return "flat"


def slug(value: object) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return cleaned or "unknown"


def records(data: pd.DataFrame, safe: bool) -> list[dict[str, object]]:
    technical_fields = [field for field in TECHNICAL_FIELDS if field in data.columns]
    fields = [
        "Symbol",
        "LTP",
        "Profit/Loss %",
        "Todays Profit/Loss %",
        "Weight %",
        "Portfolio Bucket",
        "Coordination Priority",
        "Suggested Discussion",
    ] + technical_fields
    if not safe:
        fields = [
            "Symbol",
            "Quantity",
            "Avg. Price",
            "LTP",
            "Invested Value",
            "Current Value",
            "Profit/Loss",
            "Profit/Loss %",
            "Todays Profit/Loss",
            "Todays Profit/Loss %",
            "Weight %",
            "P&L Contribution %",
            "Portfolio Bucket",
            "Coordination Priority",
            "Suggested Discussion",
        ] + technical_fields
    cleaned = data[fields].where(pd.notna(data[fields]), None)
    return json.loads(cleaned.to_json(orient="records"))


def summary(data: pd.DataFrame, safe: bool) -> dict[str, object]:
    current = float(data["Current Value"].sum())
    invested = float(data["Invested Value"].sum())
    pnl = float(data["Profit/Loss"].sum())
    today = float(data["Todays Profit/Loss"].sum()) if "Todays Profit/Loss" in data else math.nan
    high_priority = int((data["Coordination Priority"] == "High").sum())
    deep_losses = int((data["Portfolio Bucket"] == "Deep loss review").sum())
    winners = int(data["Portfolio Bucket"].isin(["Winner", "Big winner"]).sum())
    technical_status = data["Technical Status"] if "Technical Status" in data.columns else pd.Series([], dtype=str)
    technical_leaders = int(technical_status.isin(["Leader / hold", "Strong watch", "Constructive"]).sum())
    technical_risks = int(technical_status.isin(["Risk review", "Loss + weak structure"]).sum())
    rs_leaders = int((data["RS Leader"].astype(str) == "True").sum()) if "RS Leader" in data.columns else 0
    above_50 = (
        float((data["Above 50DMA"].astype(str) == "True").mean() * 100)
        if "Above 50DMA" in data.columns and len(data)
        else None
    )
    base = {
        "holdings": int(len(data)),
        "returnPct": (current / invested - 1) * 100 if invested else None,
        "todayPct": today / (current - today) * 100 if current and current != today else None,
        "highPriority": high_priority,
        "deepLosses": deep_losses,
        "winners": winners,
        "technicalLeaders": technical_leaders,
        "technicalLaggards": technical_risks,
        "technicalRisks": technical_risks,
        "rsLeaders": rs_leaders,
        "above50Pct": above_50,
    }
    if not safe:
        base.update(
            {
                "currentValue": current,
                "investedValue": invested,
                "profitLoss": pnl,
                "todayProfitLoss": today,
            }
        )
    return base


def bucket_rows(data: pd.DataFrame) -> list[dict[str, object]]:
    current = float(data["Current Value"].sum())
    rows = []
    for bucket, group in data.groupby("Portfolio Bucket", dropna=False):
        value = float(group["Current Value"].sum())
        rows.append(
            {
                "bucket": str(bucket),
                "count": int(len(group)),
                "weightPct": value / current * 100 if current else 0,
                "avgPnlPct": float(group["Profit/Loss %"].mean()) if len(group) else 0,
            }
        )
    return sorted(rows, key=lambda row: row["weightPct"], reverse=True)


def action_queue(data: pd.DataFrame) -> pd.DataFrame:
    queue = data[data["Coordination Priority"].isin(["High", "Medium"])].copy()
    priority_rank = {"High": 0, "Medium": 1, "Low": 2}
    queue["priority_rank"] = queue["Coordination Priority"].map(priority_rank).fillna(9)
    queue = queue.sort_values(["priority_rank", "Weight %", "Profit/Loss %"], ascending=[True, False, True])
    return queue[
        [
            "Symbol",
            "Coordination Priority",
            "Portfolio Bucket",
            "Weight %",
            "Profit/Loss %",
            "Todays Profit/Loss %",
            "Suggested Discussion",
        ]
    ]


def corporate_actions_section(source: Path) -> str:
    """Static, manually-curated record of corporate actions (mergers, demergers,
    stock swaps) that changed a holding's identity or share count, so the reason
    a name disappeared/changed is visible on the dashboard itself, not just in
    an offline change log."""
    path = source.parent / "corporate_actions.csv"
    if not path.exists():
        return ""
    try:
        actions = pd.read_csv(path)
    except Exception:
        return ""
    if actions.empty:
        return ""
    actions = actions.sort_values("date", ascending=False)
    cards = []
    for row in actions.itertuples():
        cards.append(f"""
            <div class="add-callout">
              <strong>{html.escape(str(row.date))} &mdash; {html.escape(str(row.stock_from))} &rarr; {html.escape(str(row.stock_to))} ({html.escape(str(row.action_type))})</strong>
              <p style="margin-top:6px">Ratio: {html.escape(str(row.ratio))}</p>
              <p style="margin-top:6px">{html.escape(str(row.details))}</p>
            </div>""")
    return f"""
        <section>
          <div class="section-head">
            <h2>Corporate Actions Affecting Portfolio</h2>
            <div class="muted">Mergers, demergers, and share swaps that changed a holding</div>
          </div>
          <div class="add-stack">{''.join(cards)}
          </div>
        </section>
"""


def dashboard_html(data: pd.DataFrame, source: Path, safe: bool) -> str:
    title = "Client Portfolio Dashboard" if safe else "Client Portfolio Advisor Dashboard"
    mode = "Client-safe online view" if safe else "Full local advisor view"
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        "title": title,
        "mode": mode,
        "generatedAt": generated_at,
        "source": source.name,
        "safe": safe,
        "summary": summary(data, safe),
        "bucketRows": bucket_rows(data),
        "holdings": records(data, safe),
        "suggestedAddsHeld": SUGGESTED_ADDS_ALREADY_HELD,
        "watchlist": load_watchlist(source),
    }
    payload_json = json.dumps(payload, ensure_ascii=True)
    ai_ctx_json = json.dumps(ai_context(data), ensure_ascii=True).replace("<", "\\u003c")
    ai_panel = (
        f'<script type="application/json" id="ai-context">{ai_ctx_json}</script>'
        + AI_WIDGET
    )
    value_metric = "" if safe else """
      metric('Current Value', money(s.currentValue)),
      metric('Unrealized P&L', money(s.profitLoss), tone(s.profitLoss)),
    """
    value_columns = "" if safe else """
              <th>Value</th>
              <th>P&L Rs</th>
    """
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f5f6f1;
      --panel: #fffefa;
      --ink: #171916;
      --muted: #626960;
      --line: #dce2d7;
      --green: #0e7259;
      --red: #b33e45;
      --amber: #936807;
      --blue: #235b8f;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); }}
    header {{ background: #fbfcf8; border-bottom: 1px solid var(--line); }}
    .wrap {{ max-width: 1420px; margin: 0 auto; padding: 22px 24px; }}
    .topbar {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }}
    .header-actions {{ display: flex; flex-direction: column; gap: 10px; align-items: flex-end; }}
    h1 {{ margin: 0; font-size: 35px; line-height: 1.08; letter-spacing: 0; }}
    h2 {{ margin: 0; font-size: 22px; letter-spacing: 0; }}
    .muted {{ color: var(--muted); line-height: 1.45; }}
    .badge {{ border: 1px solid var(--line); border-radius: 999px; padding: 8px 12px; background: #f8faf5; color: var(--muted); white-space: nowrap; font-weight: 700; }}
    .refresh-box {{ display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: wrap; }}
    .refresh-status {{ color: var(--muted); font-size: 13px; white-space: nowrap; }}
    .refresh-button {{ min-height: 38px; border: 1px solid var(--line); border-radius: 7px; padding: 8px 12px; background: #fff; color: var(--ink); font: inherit; font-weight: 750; cursor: pointer; }}
    .refresh-button:hover {{ background: #f7faf4; }}
    .metrics {{ display: grid; grid-template-columns: repeat(6, minmax(145px, 1fr)); gap: 12px; margin-top: 18px; }}
    .metric {{ min-height: 102px; padding: 15px; background: #fff; border: 1px solid var(--line); border-radius: 8px; }}
    .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; font-weight: 760; }}
    .value {{ margin-top: 13px; font-size: 25px; font-weight: 780; line-height: 1.12; }}
    .guide-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; padding: 18px; }}
    .guide-item {{ border: 1px solid var(--line); border-radius: 8px; padding: 14px; background: #fbfcf8; min-height: 132px; }}
    .guide-item strong {{ display: block; font-size: 16px; margin-bottom: 8px; }}
    .guide-item p {{ margin: 0; color: var(--muted); line-height: 1.45; }}
    .positive {{ color: var(--green); }}
    .negative {{ color: var(--red); }}
    .flat {{ color: var(--ink); }}
    .layout {{ display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(360px, .85fr); gap: 16px; align-items: start; }}
    section {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; margin: 16px 0; overflow: hidden; }}
    .section-head {{ padding: 17px 18px; display: flex; justify-content: space-between; gap: 12px; align-items: center; border-bottom: 1px solid var(--line); }}
    .controls {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    input, select {{ min-height: 40px; border: 1px solid var(--line); border-radius: 7px; padding: 9px 11px; font: inherit; background: #fbfcf8; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 11px 13px; border-bottom: 1px solid #edf0ea; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); background: #fbfcf8; font-size: 12px; letter-spacing: .06em; text-transform: uppercase; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    tbody tr {{ cursor: pointer; }}
    tbody tr:hover, tr.selected {{ background: #f7faf4; }}
    .small-table {{ overflow: auto; max-height: 620px; }}
    .pill {{ display: inline-flex; border: 1px solid var(--line); border-radius: 999px; padding: 4px 9px; white-space: nowrap; font-size: 13px; font-weight: 700; background: #f8f9f5; }}
    .spark-cell {{ min-width: 150px; }}
    .sparkline {{ display: block; width: 148px; height: 42px; overflow: visible; }}
    .sparkline path.line {{ fill: none; stroke-width: 2.4; }}
    .sparkline path.area {{ opacity: .14; }}
    .sparkline line {{ stroke: #d9dfd4; stroke-width: 1; stroke-dasharray: 3 3; }}
    .spark-label {{ display: block; margin-top: 4px; color: var(--muted); font-size: 12px; line-height: 1.25; white-space: nowrap; }}
    .detail-chart {{ margin-top: 14px; border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfcf8; }}
    .detail-chart .sparkline {{ width: 100%; height: 86px; }}
    .priority-high {{ color: var(--red); border-color: #edc3c8; background: #fff3f4; }}
    .priority-medium {{ color: var(--amber); border-color: #e8d7aa; background: #fff9ea; }}
    .priority-low {{ color: var(--green); border-color: #bbdacc; background: #eef9f4; }}
    .bucket-deep-loss-review, .bucket-loss-review {{ color: var(--red); border-color: #edc3c8; background: #fff3f4; }}
    .bucket-big-winner, .bucket-winner {{ color: var(--green); border-color: #bbdacc; background: #eef9f4; }}
    .bucket-core-position {{ color: var(--blue); border-color: #c5d9ea; background: #edf5fc; }}
    .bucket-event-watch {{ color: var(--amber); border-color: #e8d7aa; background: #fff9ea; }}
    .bucket-monitor {{ color: var(--muted); }}
    .technical-status-leader-hold, .technical-status-strong-watch {{ color: var(--green); border-color: #bbdacc; background: #eef9f4; }}
    .technical-status-constructive {{ color: var(--blue); border-color: #c5d9ea; background: #edf5fc; }}
    .technical-status-risk-review, .technical-status-loss-weak-structure {{ color: var(--red); border-color: #edc3c8; background: #fff3f4; }}
    .technical-status-monitor, .technical-status-not-downloaded {{ color: var(--muted); }}
    .bucket-stack {{ padding: 4px 18px 18px; }}
    .bucket-row {{ display: grid; grid-template-columns: 190px 1fr 72px 92px; gap: 12px; align-items: center; padding: 10px 0; border-bottom: 1px solid #edf0ea; }}
    .bucket-row:last-child {{ border-bottom: 0; }}
    .bar {{ height: 12px; background: #edf0ea; border-radius: 999px; overflow: hidden; }}
    .bar span {{ display: block; height: 100%; background: var(--green); border-radius: inherit; }}
    .detail {{ position: sticky; top: 14px; }}
    .detail-body {{ padding: 18px; }}
    .detail-title strong {{ display: block; font-size: 28px; line-height: 1.1; }}
    .kv {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 15px; }}
    .kv div {{ min-height: 76px; border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfcf8; }}
    .kv .k {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .07em; font-weight: 760; }}
    .kv .v {{ display: block; margin-top: 8px; font-size: 19px; font-weight: 740; overflow-wrap: anywhere; }}
    .note {{ margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line); color: var(--muted); line-height: 1.5; }}
    .note strong {{ color: var(--ink); }}
    .add-stack {{ padding: 14px 18px 18px; }}
    .add-stack .add-intro {{ margin: 0 0 6px; color: var(--muted); line-height: 1.5; }}
    .add-generated {{ color: var(--muted); font-size: 12px; margin: 0 0 12px; }}
    .small-table.watchlist-scroll {{ max-height: none; overflow-x: auto; overflow-y: visible; }}
    .add-callout {{ margin-top: 12px; padding: 12px 14px; border: 1px solid #bbdacc; border-left: 4px solid var(--green); border-radius: 8px; background: #eef9f4; color: var(--ink); line-height: 1.5; }}
    .add-callout strong {{ color: var(--ink); }}
    .add-callout:empty {{ display: none; }}
    #watchlistTable td.name strong {{ display: block; }}
    #watchlistTable td.name .muted {{ font-size: 12px; }}
    #watchlistTable tr.below-key td {{ background: #fff3f4; }}
    #watchlistTable tr.below-key:hover td {{ background: #ffe9eb; }}
    .keytag {{ display: inline-flex; border-radius: 999px; padding: 3px 8px; font-size: 12px; font-weight: 750; border: 1px solid var(--line); background: #eef9f4; color: var(--green); border-color: #bbdacc; white-space: nowrap; }}
    .keytag.warn {{ background: #fff3f4; color: var(--red); border-color: #edc3c8; }}
    #watchlistTable tbody tr.wl-row {{ cursor: pointer; }}
    .chartbadge {{ display: inline-flex; align-items: center; gap: 4px; border-radius: 999px; padding: 3px 9px; font-size: 12px; font-weight: 750; border: 1px solid #c5d9ea; background: #edf5fc; color: var(--blue); white-space: nowrap; }}
    .chartbadge.none {{ color: var(--muted); background: #f8faf5; border-color: var(--line); font-weight: 600; }}
    tr.chart-row > td {{ background: #fbfcf8; padding: 0; }}
    tr.chart-row.hidden {{ display: none; }}
    .wl-charts {{ padding: 14px 16px 6px; display: grid; gap: 18px; max-width: 940px; position: sticky; left: 0; }}
    .wl-chart {{ border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: #fff; }}
    .wl-chart figcaption {{ display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; padding: 10px 13px; border-bottom: 1px solid var(--line); }}
    .wl-chart figcaption .cdate {{ font-weight: 780; font-size: 13px; }}
    .wl-chart figcaption .ctext {{ color: var(--muted); font-size: 13px; line-height: 1.45; }}
    .wl-chart img {{ display: block; width: 100%; height: auto; }}
    .wl-chart a.cfull {{ display: inline-block; padding: 8px 13px; font-size: 12px; font-weight: 700; color: var(--blue); }}
    .wl-chart.missing {{ padding: 14px; color: var(--muted); font-size: 13px; }}
    .wl-empty {{ padding: 14px 16px; color: var(--muted); font-size: 13px; }}
    @media (max-width: 1100px) {{
      .metrics {{ grid-template-columns: repeat(3, minmax(145px, 1fr)); }}
      .guide-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .layout {{ grid-template-columns: 1fr; }}
      .detail {{ position: static; }}
    }}
    @media (max-width: 760px) {{
      .wrap {{ padding: 18px 14px; }}
      .topbar, .section-head {{ display: block; }}
      .header-actions {{ align-items: flex-start; margin-top: 12px; }}
      .refresh-box {{ justify-content: flex-start; }}
      .badge {{ display: inline-flex; margin-top: 12px; }}
      h1 {{ font-size: 30px; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(130px, 1fr)); }}
      .guide-grid {{ grid-template-columns: 1fr; }}
      .controls {{ margin-top: 12px; }}
      table {{ min-width: 1240px; }}
      .bucket-row, .kv {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>{html.escape(title)}</h1>
          <div class="muted">{html.escape(mode)} · Generated {html.escape(generated_at)} · Source {html.escape(source.name)}</div>
        </div>
        <div class="header-actions">
          <div class="badge">{'Online-safe' if safe else 'Local full view'}</div>
          <div class="refresh-box">
            <button class="refresh-button" type="button" onclick="refreshPage()">Refresh Page</button>
            <span id="refreshStatus" class="refresh-status">Auto refresh in 30:00</span>
          </div>
        </div>
      </div>
      <div id="metrics" class="metrics"></div>
    </div>
  </header>

  <main class="wrap">
    <div class="layout">
      <div>
        <section>
          <div class="section-head">
            <h2>Coordination Queue</h2>
            <div class="muted">Items that need discussion before action</div>
          </div>
          <div class="small-table">
            <table id="queueTable">
              <thead>
                <tr>
                  <th>Stock</th>
                  <th>Priority</th>
                  <th>Bucket</th>
                  <th>Weight</th>
                  <th>P&L</th>
                  <th>Today</th>
                  <th>Discussion</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </section>
{corporate_actions_section(source)}
        <section>
          <div class="section-head">
            <h2>Suggested Adds / Stocks I Like</h2>
            <div class="muted">Watch list — not holdings, not in portfolio value</div>
          </div>
          <div class="add-stack">
            <p class="add-intro">Names to consider adding to this portfolio, each tracked from the day it entered the filter. Not owned and excluded from every holding count, weight, P&amp;L, and portfolio-value figure on this dashboard. Watch new additions closely early &mdash; a row is flagged red once price closes below its 50- or 200-DMA. Every name stays listed here until removed; scroll the table to the end so none of the lower rows are missed. Click a row to open its annotated charts &mdash; a running visual record that grows as points worth flagging come up.</p>
            <p class="add-generated" id="watchlistGenerated"></p>
          </div>
          <div class="small-table watchlist-scroll">
            <table id="watchlistTable">
              <thead>
                <tr>
                  <th>Stock</th>
                  <th>Added</th>
                  <th>Days</th>
                  <th>Since Added</th>
                  <th>Low Since</th>
                  <th>Key Levels</th>
                  <th>50DMA</th>
                  <th>200DMA</th>
                  <th>RSI</th>
                  <th>RS vs 50D</th>
                  <th>Status</th>
                  <th>Charts</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
          <div class="add-stack">
            <div class="add-callout">
              <strong>While a name sits on this list, exposure to it can be increased if and when the client deems fit.</strong>
              Presence here is a green light to add or scale in on the client's judgement &mdash; it is not an instruction, and nothing is bought automatically.
            </div>
            <div class="add-callout" id="suggestedHeld"></div>
          </div>
        </section>

        <section>
          <div class="section-head">
            <h2>Portfolio Buckets</h2>
            <div class="muted">Bucket weight and average P&L</div>
          </div>
          <div id="bucketRows" class="bucket-stack"></div>
        </section>

        <section>
          <div class="section-head">
            <h2>Technical Reading Guide</h2>
            <div class="muted">How to read the market-structure layer</div>
          </div>
          <div class="guide-grid">
            <div class="guide-item">
              <strong>Relative Strength</strong>
              <p>Compares the stock with Nifty Midcap. A positive RS vs 50D means the stock is leading the broader market.</p>
            </div>
            <div class="guide-item">
              <strong>RSI 14</strong>
              <p>Momentum gauge. 40-60 is often a healthy reset, above 70 can be extended, below 30 can be oversold.</p>
            </div>
            <div class="guide-item">
              <strong>Moving Averages</strong>
              <p>50DMA tracks medium-term trend. 200DMA tracks long-term structure. Above both is generally stronger.</p>
            </div>
            <div class="guide-item">
              <strong>Point &amp; Figure</strong>
              <p>Filters noise and highlights breakouts or breakdowns. It is a structure check, not a standalone trade signal.</p>
            </div>
          </div>
        </section>

        <section>
          <div class="section-head">
            <h2>Technical Leaders</h2>
            <div class="muted">Relative strength, RSI, moving averages, and P&amp;F structure</div>
          </div>
          <div class="small-table">
            <table id="technicalTable">
              <thead>
                <tr>
                  <th>Stock</th>
                  <th>Technical Status</th>
                  <th>RS vs 50D</th>
                  <th>RSI</th>
                  <th>P&amp;F</th>
                  <th>50DMA</th>
                  <th>200DMA</th>
                  <th>Score</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </section>

        <section>
          <div class="section-head">
            <h2>Technical Laggards</h2>
            <div class="muted">Weak relative strength and lower-quality technical structure</div>
          </div>
          <div class="small-table">
            <table id="laggardsTable">
              <thead>
                <tr>
                  <th>Stock</th>
                  <th>Technical Status</th>
                  <th>RS Trend</th>
                  <th>RS vs 50D</th>
                  <th>RS 3M</th>
                  <th>RSI</th>
                  <th>P&amp;F</th>
                  <th>Score</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </section>

        <section>
          <div class="section-head">
            <h2>All Holdings</h2>
            <div class="controls">
              <input id="search" placeholder="Search stock" oninput="renderHoldings()">
              <select id="priority" onchange="renderHoldings()">
                <option value="">All priorities</option>
                <option>High</option>
                <option>Medium</option>
                <option>Low</option>
              </select>
              <select id="bucket" onchange="renderHoldings()">
                <option value="">All buckets</option>
              </select>
              <select id="technicalStatus" onchange="renderHoldings()">
                <option value="">All technical statuses</option>
              </select>
            </div>
          </div>
          <div class="small-table">
            <table id="holdingsTable">
              <thead>
                <tr>
                  <th>Stock</th>
                  <th>Priority</th>
                  <th>Bucket</th>
                  <th>Technical</th>
                  <th>Weight</th>
                  {value_columns}
                  <th>P&L %</th>
                  <th>Today</th>
                  <th>RS vs 50D</th>
                  <th>RSI</th>
                  <th>LTP</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </section>
      </div>

      <section class="detail">
        <div class="section-head">
          <h2>Holding Detail</h2>
          <div class="muted">Click a holding</div>
        </div>
        <div id="detail" class="detail-body"></div>
      </section>
    </div>
  </main>

  <script>
    const DATA = {payload_json};
    const AUTO_REFRESH_MS = 30 * 60 * 1000;
    const autoRefreshStartedAt = Date.now();
    let selected = null;

    const money = value => Number.isFinite(Number(value))
      ? 'Rs ' + Number(value).toLocaleString('en-IN', {{ maximumFractionDigits: 0 }})
      : '-';
    const pct = value => Number.isFinite(Number(value)) ? Number(value).toFixed(2) + '%' : '-';
    const num = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString('en-IN', {{ maximumFractionDigits: 2 }}) : '-';
    const safe = value => value === null || value === undefined ? '-' : String(value);
    const yesNo = value => value === true || value === 'True' ? 'Yes' : value === false || value === 'False' ? 'No' : '-';
    const tone = value => Number(value) > 0 ? 'positive' : Number(value) < 0 ? 'negative' : 'flat';
    const slug = value => String(value || 'unknown').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'unknown';
    const pill = (type, value) => `<span class="pill ${{type}}-${{slug(value)}}">${{safe(value)}}</span>`;
    const technicalRank = {{
      'Leader / hold': 0,
      'Strong watch': 1,
      'Constructive': 2,
      'Monitor': 3,
      'Risk review': 4,
      'Loss + weak structure': 5,
      'Not downloaded': 6
    }};

    function isDownloaded(row) {{
      return row['Technical Downloaded'] === true || row['Technical Downloaded'] === 'True';
    }}

    function parseTrend(value) {{
      if (Array.isArray(value)) return value.map(Number).filter(Number.isFinite);
      if (!value || value === '-') return [];
      try {{
        const parsed = JSON.parse(value);
        return Array.isArray(parsed) ? parsed.map(Number).filter(Number.isFinite) : [];
      }} catch (error) {{
        return [];
      }}
    }}

    function sparkline(value, size = 'small') {{
      const values = parseTrend(value);
      if (values.length < 2) return '<span class="muted">No RS trend</span>';
      const width = size === 'large' ? 320 : 148;
      const height = size === 'large' ? 86 : 42;
      const pad = 4;
      const min = Math.min(...values);
      const max = Math.max(...values);
      const range = max - min || 1;
      const points = values.map((item, index) => {{
        const x = pad + index * ((width - pad * 2) / Math.max(1, values.length - 1));
        const y = height - pad - ((item - min) / range) * (height - pad * 2);
        return [x, y];
      }});
      const line = points.map((point, index) => `${{index ? 'L' : 'M'}}${{point[0].toFixed(1)}},${{point[1].toFixed(1)}}`).join(' ');
      const area = `${{line}} L${{points[points.length - 1][0].toFixed(1)}},${{height - pad}} L${{points[0][0].toFixed(1)}},${{height - pad}} Z`;
      const baselineValue = Math.min(max, Math.max(min, 100));
      const baselineY = height - pad - ((baselineValue - min) / range) * (height - pad * 2);
      const rising = values[values.length - 1] >= values[0];
      const color = rising ? '#0e7259' : '#b33e45';
      return `
        <svg class="sparkline" viewBox="0 0 ${{width}} ${{height}}" role="img" aria-label="Relative strength trend">
          <line x1="${{pad}}" y1="${{baselineY.toFixed(1)}}" x2="${{width - pad}}" y2="${{baselineY.toFixed(1)}}"></line>
          <path class="area" d="${{area}}" fill="${{color}}"></path>
          <path class="line" d="${{line}}" stroke="${{color}}"></path>
        </svg>
      `;
    }}

    function trendLabel(value) {{
      const values = parseTrend(value);
      if (values.length < 2) return '-';
      const change = values[values.length - 1] - values[0];
      const label = `${{change >= 0 ? '+' : ''}}${{change.toFixed(1)}} pts`;
      return `<span class="${{change >= 0 ? 'positive' : 'negative'}}">${{label}}</span>`;
    }}

    function rsTrendCell(row) {{
      return `<div class="spark-cell">${{sparkline(row['RS Trend'])}}<span class="spark-label">90D RS: ${{trendLabel(row['RS Trend'])}}</span></div>`;
    }}

    function metric(label, value, klass = '') {{
      return `<div class="metric"><div class="label">${{label}}</div><div class="value ${{klass}}">${{value}}</div></div>`;
    }}

    function renderMetrics() {{
      const s = DATA.summary;
      const cells = [
        metric('Holdings', s.holdings),
        {value_metric}
        metric('Return', pct(s.returnPct), tone(s.returnPct)),
        metric('Today', DATA.safe ? pct(s.todayPct) : money(s.todayProfitLoss), DATA.safe ? tone(s.todayPct) : tone(s.todayProfitLoss)),
        metric('High Priority', s.highPriority, s.highPriority ? 'negative' : 'positive'),
        metric('Deep Losses', s.deepLosses, s.deepLosses ? 'negative' : 'positive'),
        metric('Tech Leaders', s.technicalLeaders || 0, s.technicalLeaders ? 'positive' : 'flat'),
        metric('Tech Laggards', s.technicalLaggards || 0, s.technicalLaggards ? 'negative' : 'flat'),
        metric('RS Leaders', s.rsLeaders || 0, s.rsLeaders ? 'positive' : 'flat'),
      ];
      document.getElementById('metrics').innerHTML = cells.join('');
    }}

    function signedPct(value) {{
      if (!Number.isFinite(Number(value))) return '-';
      const n = Number(value);
      return `${{n >= 0 ? '+' : ''}}${{n.toFixed(2)}}%`;
    }}

    function dmaCell(above, distance) {{
      if (above === null || above === undefined) return '<span class="muted">-</span>';
      const cls = above ? 'positive' : 'negative';
      const gap = Number.isFinite(Number(distance)) ? ` · ${{signedPct(distance)}}` : '';
      return `<span class="${{cls}}">${{above ? 'Above' : 'Below'}}${{gap}}</span>`;
    }}

    function esc(value) {{
      return String(value === null || value === undefined ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }}

    function watchChartsHtml(row) {{
      const charts = Array.isArray(row.charts) ? row.charts : [];
      const name = esc(row.name);
      if (!charts.length) {{
        return `<div class="wl-empty">No annotated charts pinned to ${{name}} yet. Add one to <code>data/watchlist_charts.csv</code> (with the image under <code>data/watchlist_charts/</code>) and it appears here, newest last.</div>`;
      }}
      const items = charts.map(chart => {{
        if (chart.missing) {{
          return `<figure class="wl-chart missing">Image <code>${{esc(chart.src)}}</code> is listed for ${{esc(chart.date)}} but not in the repo yet.</figure>`;
        }}
        const cap = chart.caption
          ? `<span class="ctext">${{esc(chart.caption)}}</span>` : '';
        return `
          <figure class="wl-chart">
            <figcaption><span class="cdate">${{esc(chart.date)}}</span>${{cap}}</figcaption>
            <img loading="lazy" src="${{esc(chart.src)}}" alt="Annotated chart for ${{name}}, ${{esc(chart.date)}}">
            <a class="cfull" href="${{esc(chart.src)}}" target="_blank" rel="noopener">Open full size &#8599;</a>
          </figure>`;
      }}).join('');
      return `<div class="wl-charts">${{items}}</div>`;
    }}

    function toggleWatchCharts(index) {{
      const panel = document.getElementById(`wl-charts-${{index}}`);
      if (!panel) return;
      const open = panel.classList.toggle('hidden');
      const trigger = panel.previousElementSibling;
      if (trigger) trigger.setAttribute('aria-expanded', String(!open));
    }}

    function renderWatchlist() {{
      const body = document.querySelector('#watchlistTable tbody');
      if (!body) return;
      const wl = DATA.watchlist || {{}};
      const rows = wl.records || [];
      const gen = document.getElementById('watchlistGenerated');
      if (gen) gen.textContent = wl.generatedAt ? `Watch-list technicals updated ${{wl.generatedAt}}` : '';

      body.innerHTML = rows.map((row, index) => {{
        const below = row.belowKeyLevel === true;
        const keyClass = below ? 'keytag warn' : 'keytag';
        const keyText = safe(row.keyLevel || (row.downloaded ? 'Above key levels' : 'Awaiting data'));
        const since = row.sinceAddedPct;
        const low = row.drawdownSincePct;
        const status = row.downloaded
          ? pill('technical-status', row.technicalStatus || 'Not downloaded')
          : `<span class="muted">${{row.error ? 'No data' : 'Pending'}}</span>`;
        const charts = Array.isArray(row.charts) ? row.charts : [];
        const badge = charts.length
          ? `<span class="chartbadge">&#128200; ${{charts.length}}</span>`
          : `<span class="chartbadge none">&#128200; add</span>`;
        return `
        <tr class="wl-row ${{below ? 'below-key' : ''}}" onclick="toggleWatchCharts(${{index}})" aria-expanded="false">
          <td class="name"><strong>${{safe(row.name)}}</strong><span class="muted">${{safe(row.ticker || '')}}</span></td>
          <td>${{safe(row.dateAdded)}}</td>
          <td class="num">${{Number.isFinite(Number(row.daysTracked)) ? row.daysTracked : '-'}}</td>
          <td class="num ${{tone(since)}}">${{signedPct(since)}}</td>
          <td class="num ${{tone(low)}}">${{signedPct(low)}}</td>
          <td><span class="${{keyClass}}">${{keyText}}</span></td>
          <td class="num">${{dmaCell(row.above50DMA, row.dist50DMAPct)}}</td>
          <td class="num">${{dmaCell(row.above200DMA, row.dist200DMAPct)}}</td>
          <td class="num">${{num(row.rsi14)}}</td>
          <td class="num ${{tone(row.rsVs50Pct)}}">${{pct(row.rsVs50Pct)}}</td>
          <td>${{status}}</td>
          <td>${{badge}}</td>
        </tr>
        <tr class="chart-row hidden" id="wl-charts-${{index}}"><td colspan="12">${{watchChartsHtml(row)}}</td></tr>`;
      }}).join('');

      const held = document.getElementById('suggestedHeld');
      const owned = DATA.suggestedAddsHeld || [];
      if (held) {{
        held.innerHTML = owned.length
          ? `<strong>Already held from this list:</strong> ${{owned.join(', ')}} &mdash; these are tracked in the holdings tables above, so the full "stocks I like" list is covered between the two.`
          : '';
      }}
    }}

    function renderBuckets() {{
      document.getElementById('bucketRows').innerHTML = DATA.bucketRows.map(row => `
        <div class="bucket-row">
          <div>${{pill('bucket', row.bucket)}} <span class="muted">${{row.count}} holdings</span></div>
          <div class="bar"><span style="width:${{Math.max(2, Number(row.weightPct) || 0)}}%"></span></div>
          <div class="${{tone(row.weightPct)}}">${{pct(row.weightPct)}}</div>
          <div class="${{tone(row.avgPnlPct)}}">${{pct(row.avgPnlPct)}}</div>
        </div>
      `).join('');
    }}

    function safeValueCells(row) {{
      if (DATA.safe) return '';
      return `<td class="num">${{money(row['Current Value'])}}</td><td class="num ${{tone(row['Profit/Loss'])}}">${{money(row['Profit/Loss'])}}</td>`;
    }}

    function rowHtml(row) {{
      const symbol = safe(row.Symbol).replaceAll("'", "\\\\'");
      const technicalStatus = row['Technical Status'] || 'Not downloaded';
      return `
        <tr onclick="selectHolding('${{symbol}}')" class="${{selected === row.Symbol ? 'selected' : ''}}">
          <td><strong>${{safe(row.Symbol)}}</strong></td>
          <td>${{pill('priority', row['Coordination Priority'])}}</td>
          <td>${{pill('bucket', row['Portfolio Bucket'])}}</td>
          <td>${{pill('technical-status', technicalStatus)}}</td>
          <td class="num ${{tone(row['Weight %'])}}">${{pct(row['Weight %'])}}</td>
          ${{safeValueCells(row)}}
          <td class="num ${{tone(row['Profit/Loss %'])}}">${{pct(row['Profit/Loss %'])}}</td>
          <td class="num ${{tone(row['Todays Profit/Loss %'])}}">${{pct(row['Todays Profit/Loss %'])}}</td>
          <td class="num ${{tone(row['RS vs 50D %'])}}">${{pct(row['RS vs 50D %'])}}</td>
          <td class="num">${{num(row['RSI 14'])}}</td>
          <td class="num">${{num(row.LTP)}}</td>
        </tr>
      `;
    }}

    function renderTechnicalTable() {{
      const rows = DATA.holdings
        .slice()
        .sort((a, b) => (technicalRank[a['Technical Status']] ?? 9) - (technicalRank[b['Technical Status']] ?? 9)
          || Number(b['RS vs 50D %'] || -999) - Number(a['RS vs 50D %'] || -999)
          || Number(b['Weight %'] || 0) - Number(a['Weight %'] || 0))
        .slice(0, 18);
      document.querySelector('#technicalTable tbody').innerHTML = rows.map(row => `
        <tr onclick="selectHolding('${{safe(row.Symbol).replaceAll("'", "\\\\'")}}')" class="${{selected === row.Symbol ? 'selected' : ''}}">
          <td><strong>${{safe(row.Symbol)}}</strong></td>
          <td>${{pill('technical-status', row['Technical Status'] || 'Not downloaded')}}</td>
          <td class="num ${{tone(row['RS vs 50D %'])}}">${{pct(row['RS vs 50D %'])}}</td>
          <td class="num">${{num(row['RSI 14'])}}</td>
          <td>${{safe(row['P&F Signal'])}}</td>
          <td class="num ${{tone(row['50DMA Distance %'])}}">${{yesNo(row['Above 50DMA'])}} · ${{pct(row['50DMA Distance %'])}}</td>
          <td class="num ${{tone(row['200DMA Distance %'])}}">${{yesNo(row['Above 200DMA'])}} · ${{pct(row['200DMA Distance %'])}}</td>
          <td class="num">${{num(row['Technical Score'])}}</td>
        </tr>
      `).join('');
    }}

    function renderLaggardsTable() {{
      const downloaded = DATA.holdings.filter(isDownloaded);
      const rows = (downloaded.length ? downloaded : DATA.holdings)
        .slice()
        .sort((a, b) => Number(a['Technical Score'] ?? 999) - Number(b['Technical Score'] ?? 999)
          || Number(a['RS vs 50D %'] ?? 999) - Number(b['RS vs 50D %'] ?? 999)
          || Number(a['RS 3M %'] ?? 999) - Number(b['RS 3M %'] ?? 999)
          || Number(b['Weight %'] || 0) - Number(a['Weight %'] || 0))
        .slice(0, 18);
      document.querySelector('#laggardsTable tbody').innerHTML = rows.map(row => `
        <tr onclick="selectHolding('${{safe(row.Symbol).replaceAll("'", "\\\\'")}}')" class="${{selected === row.Symbol ? 'selected' : ''}}">
          <td><strong>${{safe(row.Symbol)}}</strong></td>
          <td>${{pill('technical-status', row['Technical Status'] || 'Not downloaded')}}</td>
          <td>${{rsTrendCell(row)}}</td>
          <td class="num ${{tone(row['RS vs 50D %'])}}">${{pct(row['RS vs 50D %'])}}</td>
          <td class="num ${{tone(row['RS 3M %'])}}">${{pct(row['RS 3M %'])}}</td>
          <td class="num">${{num(row['RSI 14'])}}</td>
          <td>${{safe(row['P&F Signal'])}}</td>
          <td class="num">${{num(row['Technical Score'])}}</td>
        </tr>
      `).join('');
    }}

    function renderQueue() {{
      const rank = {{ High: 0, Medium: 1, Low: 2 }};
      const rows = DATA.holdings
        .filter(row => ['High', 'Medium'].includes(row['Coordination Priority']))
        .sort((a, b) => (rank[a['Coordination Priority']] ?? 9) - (rank[b['Coordination Priority']] ?? 9)
          || Number(b['Weight %'] || 0) - Number(a['Weight %'] || 0));
      document.querySelector('#queueTable tbody').innerHTML = rows.map(row => `
        <tr onclick="selectHolding('${{safe(row.Symbol).replaceAll("'", "\\\\'")}}')" class="${{selected === row.Symbol ? 'selected' : ''}}">
          <td><strong>${{safe(row.Symbol)}}</strong></td>
          <td>${{pill('priority', row['Coordination Priority'])}}</td>
          <td>${{pill('bucket', row['Portfolio Bucket'])}}</td>
          <td class="num ${{tone(row['Weight %'])}}">${{pct(row['Weight %'])}}</td>
          <td class="num ${{tone(row['Profit/Loss %'])}}">${{pct(row['Profit/Loss %'])}}</td>
          <td class="num ${{tone(row['Todays Profit/Loss %'])}}">${{pct(row['Todays Profit/Loss %'])}}</td>
          <td>${{safe(row['Suggested Discussion'])}}</td>
        </tr>
      `).join('');
    }}

    function renderBucketFilter() {{
      const select = document.getElementById('bucket');
      const buckets = [...new Set(DATA.holdings.map(row => row['Portfolio Bucket']).filter(Boolean))].sort();
      select.innerHTML += buckets.map(bucket => `<option>${{bucket}}</option>`).join('');
    }}

    function renderTechnicalStatusFilter() {{
      const select = document.getElementById('technicalStatus');
      const statuses = [...new Set(DATA.holdings.map(row => row['Technical Status']).filter(Boolean))].sort();
      select.innerHTML += statuses.map(status => `<option>${{status}}</option>`).join('');
    }}

    function renderHoldings() {{
      const query = document.getElementById('search').value.toLowerCase();
      const priority = document.getElementById('priority').value;
      const bucket = document.getElementById('bucket').value;
      const technicalStatus = document.getElementById('technicalStatus').value;
      const rows = DATA.holdings
        .filter(row => !query || safe(row.Symbol).toLowerCase().includes(query))
        .filter(row => !priority || row['Coordination Priority'] === priority)
        .filter(row => !bucket || row['Portfolio Bucket'] === bucket)
        .filter(row => !technicalStatus || row['Technical Status'] === technicalStatus)
        .sort((a, b) => Number(b['Weight %'] || 0) - Number(a['Weight %'] || 0));
      document.querySelector('#holdingsTable tbody').innerHTML = rows.map(rowHtml).join('');
    }}

    function detailMetric(label, value, klass = '') {{
      return `<div><span class="k">${{label}}</span><span class="v ${{klass}}">${{value}}</span></div>`;
    }}

    function renderDetail(row) {{
      const valueMetrics = DATA.safe ? '' : `
        ${{detailMetric('Current Value', money(row['Current Value']))}}
        ${{detailMetric('Invested Value', money(row['Invested Value']))}}
        ${{detailMetric('P&L Amount', money(row['Profit/Loss']), tone(row['Profit/Loss']))}}
        ${{detailMetric('Quantity / Avg Price', safe(row.Quantity) + ' / ' + num(row['Avg. Price']))}}
      `;
      const technicalMetrics = `
        ${{detailMetric('Technical Status', pill('technical-status', row['Technical Status'] || 'Not downloaded'))}}
        ${{detailMetric('RS vs 50D', pct(row['RS vs 50D %']), tone(row['RS vs 50D %']))}}
        ${{detailMetric('RS 3M', pct(row['RS 3M %']), tone(row['RS 3M %']))}}
        ${{detailMetric('RSI 14', num(row['RSI 14']))}}
        ${{detailMetric('50DMA', yesNo(row['Above 50DMA']) + ' · ' + pct(row['50DMA Distance %']), tone(row['50DMA Distance %']))}}
        ${{detailMetric('200DMA', yesNo(row['Above 200DMA']) + ' · ' + pct(row['200DMA Distance %']), tone(row['200DMA Distance %']))}}
        ${{detailMetric('52W High Gap', pct(row['52W High Distance %']), tone(row['52W High Distance %']))}}
        ${{detailMetric('P&F', safe(row['P&F Signal']))}}
      `;
      const detailTrend = parseTrend(row['RS Trend']).length >= 2
        ? `<div class="detail-chart"><span class="label">Relative Strength Trend vs Nifty Midcap</span>${{sparkline(row['RS Trend'], 'large')}}<span class="spark-label">90-day change: ${{trendLabel(row['RS Trend'])}}</span></div>`
        : '';
      document.getElementById('detail').innerHTML = `
        <div class="detail-title">
          <strong>${{safe(row.Symbol)}}</strong>
          <div class="muted">${{pill('priority', row['Coordination Priority'])}} ${{pill('bucket', row['Portfolio Bucket'])}} ${{pill('technical-status', row['Technical Status'] || 'Not downloaded')}}</div>
        </div>
        <div class="kv">
          ${{detailMetric('Weight', pct(row['Weight %']), tone(row['Weight %']))}}
          ${{detailMetric('P&L %', pct(row['Profit/Loss %']), tone(row['Profit/Loss %']))}}
          ${{detailMetric('Today', pct(row['Todays Profit/Loss %']), tone(row['Todays Profit/Loss %']))}}
          ${{detailMetric('LTP', num(row.LTP))}}
          ${{valueMetrics}}
          ${{technicalMetrics}}
        </div>
        ${{detailTrend}}
        <div class="note"><strong>Discussion:</strong> ${{safe(row['Suggested Discussion'])}}</div>
        <div class="note"><strong>Technical reading:</strong> ${{safe(row['Technical Note'])}}</div>
      `;
    }}

    function selectHolding(symbol) {{
      selected = symbol;
      const row = DATA.holdings.find(item => item.Symbol === symbol) || DATA.holdings[0];
      renderDetail(row);
      renderQueue();
      renderTechnicalTable();
      renderLaggardsTable();
      renderHoldings();
    }}

    function refreshPage() {{
      const url = new URL(window.location.href);
      url.searchParams.set('refresh', String(Date.now()));
      window.location.replace(url.toString());
    }}

    function renderRefreshStatus() {{
      const remaining = Math.max(0, AUTO_REFRESH_MS - (Date.now() - autoRefreshStartedAt));
      const minutes = Math.floor(remaining / 60000);
      const seconds = Math.floor((remaining % 60000) / 1000);
      document.getElementById('refreshStatus').textContent =
        `Auto refresh in ${{String(minutes).padStart(2, '0')}}:${{String(seconds).padStart(2, '0')}}`;
    }}

    renderMetrics();
    renderWatchlist();
    renderBuckets();
    renderTechnicalTable();
    renderLaggardsTable();
    renderBucketFilter();
    renderTechnicalStatusFilter();
    selected = DATA.holdings[0]?.Symbol || null;
    renderQueue();
    renderHoldings();
    renderDetail(DATA.holdings[0] || {{}});
    renderRefreshStatus();
    setInterval(renderRefreshStatus, 1000);
    setTimeout(refreshPage, AUTO_REFRESH_MS);
  </script>
  {ai_panel}
</body>
</html>
"""


def write_action_queue(data: pd.DataFrame, path: Path) -> None:
    queue = action_queue(data).copy()
    queue["Advisor Proposed Action"] = ""
    queue["Client Decision"] = "Pending"
    queue["Follow-up Date"] = ""
    queue["Notes"] = ""
    queue.to_csv(path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build client portfolio dashboards.")
    parser.add_argument("--input", type=Path, required=True, help="Client portfolio CSV.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    data = read_portfolio(args.input)
    local_dir = args.output_dir / "local"
    safe_dir = args.output_dir / "client_safe"
    local_dir.mkdir(parents=True, exist_ok=True)
    safe_dir.mkdir(parents=True, exist_ok=True)

    local_path = local_dir / "index.html"
    safe_path = safe_dir / "index.html"
    queue_path = args.output_dir / "client_action_queue.csv"

    local_path.write_text(dashboard_html(data, args.input, safe=False), encoding="utf-8")
    safe_path.write_text(dashboard_html(data, args.input, safe=True), encoding="utf-8")
    write_action_queue(data, queue_path)

    total_current = data["Current Value"].sum()
    total_invested = data["Invested Value"].sum()
    print(f"Holdings: {len(data)}")
    print(f"Current value: {money(total_current)}")
    print(f"Return: {pct((total_current / total_invested - 1) * 100 if total_invested else None)}")
    print(f"Local dashboard: {local_path}")
    print(f"Client-safe dashboard: {safe_path}")
    print(f"Action queue: {queue_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
