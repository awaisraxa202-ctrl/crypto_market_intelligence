# Crypto Market Intelligence v4.2 — Enhanced Edition

> **Built on v4.1 Merged Edition** with real-world enhancements, bug fixes, and professional risk metrics.

## What This System Is

A daily research dashboard that analyzes **9 major cryptocurrencies** using real market data, **35+ technical indicators**, **10 strategy backtests**, **Monte Carlo simulation**, **cross-asset correlation analysis**, and plain-English narrative explanations.

## What's New in v4.2

### 🔧 Bug Fixes
- **Fixed EMA 12/26 calculation** — MACD now computes correctly (was broken in v4.1)
- **Fixed yfinance multi-index columns** — handles newer yfinance API format
- **Fixed empty DataFrame crashes** — robust guards throughout the pipeline
- **Added retry logic with exponential backoff** — all APIs now auto-retry on 429/rate-limit

### 🆕 New Features

| Feature | Description |
|---------|-------------|
| **RSI Divergence** | Detects bullish/bearish divergences between price and RSI |
| **OBV Divergence** | Smart money proxy — volume accumulation vs price |
| **Support & Resistance** | Swing high/low levels computed from recent price action |
| **Whale Activity Proxy** | Volume z-score spikes flag unusual institutional activity |
| **VaR (95%)** | Value at Risk — "worst expected daily loss" metric |
| **Sortino Ratio** | Risk-adjusted return using only downside deviation |
| **Calmar Ratio** | Annualized return / max drawdown |
| **Max Consecutive Wins/Losses** | Streak analysis for strategy robustness |
| **Correlation Matrix** | Cross-asset correlations for portfolio construction |
| **Altcoin Season Index** | 0-100 score: is it Bitcoin season or altcoin season? |
| **Market Breadth** | Advance/decline proxy across all 9 assets |
| **Funding Rate Heatmap** | All assets' funding rates in one view |
| **Volatility Forecast** | GARCH-like EWMA 5-day and 20-day volatility projections |
| **6-Factor Pattern Matching** | RSI, price vs SMA, volatility, MACD, BB position, ATR |

### 📊 Data Sources (11 free APIs)
- **Binance** — OHLCV, funding rates, open interest, long/short ratios
- **Yahoo Finance** — SPY, DXY, VIX, TNX, GLD macro proxies
- **Fear & Greed Index** — Market sentiment (contrarian signal)
- **CoinGecko** — Market cap, dominance, ATH data
- **Deribit** — Options put/call ratio, implied volatility
- **Etherscan** — Gas prices (network congestion proxy)
- **Beaconchain** — ETH validator count & staking data
- **FRED** — CPI and Fed Funds Rate

## Supported Assets (9)

| Code | Name | Options | On-Chain |
|------|------|---------|----------|
| BTC | Bitcoin | Deribit | — |
| ETH | Ethereum | Deribit | Etherscan, Beaconchain |
| SOL | Solana | Deribit | — |
| BNB | BNB | — | — |
| XRP | XRP | — | — |
| ADA | Cardano | — | — |
| DOGE | Dogecoin | — | — |
| LINK | Chainlink | — | — |
| AVAX | Avalanche | — | — |

## The 12 Sub-Signals

| # | Signal | Weight | Description |
|---|--------|--------|-------------|
| 1 | Trend | High | Price vs SMA50 & SMA200 (Golden/Death Cross) |
| 2 | Momentum | High | RSI + MACD histogram direction |
| 3 | Volatility | Medium | ATR% and annualized volatility |
| 4 | Sentiment | Medium | Fear & Greed Index (contrarian) |
| 5 | Funding Rate | Medium | Binance futures funding (contrarian) |
| 6 | Volume | Low | Volume vs 20-day average |
| 7 | Drawdown | Low | Distance from all-time high |
| 8 | Stoch RSI | Low | More sensitive momentum indicator |
| 9 | Williams %R | Low | Overbought/oversold oscillator |
| 10 | PI Cycle Top | High | 111 SMA vs 350 SMA x2 (BTC top detector) |
| 11 | RSI Divergence | Medium | Price-RSI divergence detection |
| 12 | OBV Divergence | Medium | Volume-price divergence (smart money) |

## Signal Levels

| Score | Signal | Action |
|-------|--------|--------|
| +0.5 to +1.0 | STRONG LONG | Multiple factors align bullish |
| +0.2 to +0.5 | LONG | Conditions favor upside |
| -0.2 to +0.2 | NO TRADE | Mixed signals. Stay in cash. |
| -0.5 to -0.2 | SHORT | Conditions favor downside |
| -1.0 to -0.5 | STRONG SHORT | Multiple bearish factors align |

## 10 Pre-Validated Strategies

Each backtested with 0.2% fees (0.1% taker + 0.1% slippage):

1. SMA20 Crossover
2. SMA50 Trend
3. Golden Cross
4. RSI < 30, > 70
5. RSI + Trend Filter
6. Bollinger Bounce
7. MACD Crossover
8. Volatility Breakout
9. Stoch RSI Oversold
10. Williams %R

## Risk Metrics (NEW in v4.2)

| Metric | What It Tells You |
|--------|-------------------|
| **VaR (95%)** | "With 95% confidence, daily loss won't exceed X%" |
| **Sortino Ratio** | Return per unit of downside risk (Sharpe but better) |
| **Calmar Ratio** | Return per unit of max drawdown |
| **Max Consecutive Losses** | Worst losing streak — test your psychology |
| **Kelly Fraction** | Optimal bet size as % of capital |

## Zero-Cost Deployment

### Step 1: Create GitHub Repo
1. Go to [github.com/new](https://github.com/new)
2. Name it `crypto-market-intelligence`
3. Make it **Public**
4. Click **Create repository**

### Step 2: Upload Files
Upload these files to the repo root:
- `crypto_market_intelligence_v42.py`
- `index.html`
- `requirements.txt`
- `.github/workflows/update.yml`

### Step 3: Enable GitHub Pages
Settings → Pages → Source: GitHub Actions

### Step 4: Enable GitHub Actions
Actions tab → "I understand my workflows, go ahead and enable them"

### Step 5: (Optional) Add API Keys as Secrets
Settings → Secrets and variables → Actions → New repository secret:
- `ETHERSCAN_API_KEY` — Get free at [etherscan.io/apis](https://etherscan.io/apis)
- `BEACONCHAIN_API_KEY` — Get free at [beaconcha.in](https://beaconcha.in)
- `FRED_API_KEY` — Get free at [fred.stlouisfed.org](https://fred.stlouisfed.org)

> The system works without API keys — they just unlock extra data (gas, staking, macro).

### Live URL
```
https://YOUR_USERNAME.github.io/crypto-market-intelligence/
```

**Total cost: $0**

## Architecture

```
┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│  11 Data Sources │────▶│  Python Engine v4.2 │────▶│    Dashboard    │
├─────────────────┤     ├─────────────────────┤     ├─────────────────┤
│ Binance (prices)│     │ Fetch & Clean       │     │ index.html      │
│ Yahoo (macro)   │     │ 35+ Indicators      │     │ Expandable Cards│
│ Fear & Greed    │     │ 10 Strategy Tests   │     │ Narratives      │
│ CoinGecko       │     │ Monte Carlo         │     │ Pattern Match   │
│ Deribit (opts)  │     │ Kelly Criterion     │     │ Seasonality     │
│ Etherscan (gas) │     │ Risk Metrics (NEW)  │     │ Regime Bars     │
│ Beaconchain     │     │ Correlation Matrix  │     │ S/R Levels      │
│ FRED (CPI/Fed)  │     │ Altcoin Season Idx  │     │ Whale Alerts    │
└─────────────────┘     │ Market Breadth      │     └─────────────────┘
                        │ Funding Heatmap     │
                        │ Narrative Generation│
                        └─────────────────────┘
                                   │
                        ┌──────────┘
                        ▼
                   GitHub Actions
                   (Daily @ 06:00 UTC)
```

## What This Proves

| Skill | Evidence |
|-------|----------|
| Data Engineering | 11 APIs, multi-source pipeline, time-series alignment, SQLite |
| Feature Engineering | 35+ indicators from scratch (RSI, MACD, ATR, Bollinger, Pi Cycle, Divergence, etc.) |
| Quantitative Analysis | Walk-forward backtesting, regime detection, pattern matching, Monte Carlo |
| Risk Management | VaR, Sortino, Calmar, Kelly Criterion, drawdown tracking, volatility filters |
| Statistical Rigor | Correlation matrices, seasonality, bootstrap simulation |
| DevOps/CI-CD | GitHub Actions cron, automated deployment, secrets management |
| Intellectual Honesty | Clear disclaimers, shows strategy failures, admits limitations |

## Important Warnings

**THIS IS A RESEARCH AND EDUCATIONAL TOOL ONLY. NOT FINANCIAL ADVICE.**

- Past performance does NOT predict future results
- Markets change. You can lose money.
- The system has losing streaks.
- Crypto is extremely volatile — never invest more than you can afford to lose.
- The creators are NOT responsible for any trading losses.

## How to Use Responsibly

1. **Paper trade first** — follow signals with fake money for 3+ months
2. **Track every signal** in a journal
3. **Never risk more than 1-2%** of your capital on any single trade idea
4. **Use this as ONE input among many**
5. **If you don't understand an indicator, don't trade based on it**

## License

MIT License — free to use, modify, and deploy. No warranty provided. Use at your own risk.
