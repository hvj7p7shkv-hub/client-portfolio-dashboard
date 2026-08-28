# Client Portfolio Dashboard

Full GitHub Pages demo version.

This copy includes quantities, average buy prices, invested value, current value, and rupee profit/loss. Use this only for a short client demo on a public GitHub Pages URL. Move to a login-gated host before long-term use.

## Online Refresh

The dashboard refreshes through GitHub Actions.

- Manual refresh: GitHub repo > Actions > Refresh portfolio dashboard > Run workflow.
- Scheduled refresh: every 30 minutes from 09:30 to 15:30 IST, Monday to Friday.
- Data source: `data/holdings.csv`.
- Live prices: Yahoo Finance via the `yfinance` Python package.
- Technical layer: relative strength versus Nifty Midcap, RSI 14, 50DMA, 200DMA, 52-week-high gap, and Point & Figure structure.
- If a symbol cannot be downloaded, the script keeps the last CSV value for that stock.
- If technical data cannot be downloaded, the dashboard marks that stock as `Not downloaded` instead of failing the whole page.

After the workflow commits the refreshed `index.html`, GitHub Pages should update the public link automatically.
