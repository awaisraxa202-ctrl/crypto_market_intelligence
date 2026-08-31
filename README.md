# Market Cortex v6.0

Automated cryptocurrency market intelligence. Runs every 2 hours via GitHub Actions, pulls live data from 11+ free sources, analyses 9 assets, and publishes a static dashboard to GitHub Pages.

**Live dashboard:** https://awaisraxa202-ctrl.github.io/crypto_market_intelligence/

> **RESEARCH AND EDUCATIONAL TOOL ONLY. NOT FINANCIAL ADVICE.**
> Past performance does not predict future results. Paper trade for at least 3 months before risking real capital.

---

## What it does

Every 2 hours the pipeline:

1. Fetches OHLCV, funding rates, open interest, order books, on-chain and macro data
2. Computes ~35 technical indicators per asset
3. Scores each asset into a weighted composite signal with a conviction level
4. Confirms (or penalises) that signal across 1h / 4h / 1d timeframes
5. Filters signals failing volume or volatility confirmation
6. Builds a full trade plan — entry, stop-loss, two take-profit levels, R:R
7. Sizes positions by volatility, drawdown state and conviction
8. Backtests 10 strategies, runs Monte Carlo, computes risk metrics
9. Writes `docs/market_intelligence.json` + `docs/v6_results.json`
10. Commits, then deploys to GitHub Pages

**Assets:** BTC, ETH, SOL, BNB, XRP, ADA, DOGE, LINK, AVAX

---

## Architecture

```
crypto_market_intelligence_v60.py   Engine (~3,900 lines, 122 functions)
├── run_pipeline()                  Main 9-asset pass  -> market_intelligence.json
└── run_v6_pipeline()               BTC deep-dive pass -> v6_results.json

index.html                          Dashboard (reads both JSON files)
explainer.html                      Static plain-English guide
offline_test.py                     End-to-end test harness (mocked network)
.github/workflows/update.yml        Scheduled run + Pages deploy
```

---

## Feature status — verified, not claimed

Every entry was checked against the actual call graph and exercised in `offline_test.py`. **47/47 checks pass.**

### Signals & analysis
| Feature | Status |
|---|---|
| 35+ technical indicators | Working |
| Weighted composite scoring | Working |
| Multi-timeframe confirmation (1h/4h/1d) | Working — **adjusts conviction** |
| Regime detection + strategy switching | Working |
| False-signal filter (volume/volatility) | Working — **downgrades signals** |
| Feature attribution (per-signal contribution) | Working |
| Support/resistance (real swing levels) | Working |
| RSI + OBV divergence | Working |
| Whale activity proxy | Working |
| Seasonality (best/worst day & month) | Working |
| Monte Carlo (300 sims) | Working |
| 10-strategy backtest validation | Working |
| Walk-forward validation | Working |

### Risk & sizing
| Feature | Status |
|---|---|
| Dynamic volatility-adjusted sizing | Working |
| Drawdown protection (10% DD -> 50% size) | Working |
| Two-tier BIG/SMALL trades | Working — **gated by conviction** |
| Kelly optimal sizing | Working |
| Risk of ruin, VaR, Sortino, Calmar | Working |
| Correlation risk + breakdown detection | Working |
| Risk grading (A-F) | Working |

### Market data
| Feature | Status |
|---|---|
| Order book snapshots + imbalance | Working |
| Order book WebSocket burst | Working (8s bounded sample) |
| Funding rates (Binance + Bybit) | Working |
| Long/short ratio, open interest | Working |
| Deribit options (put/call, IV) | Working |
| Macro (Fed, DXY, VIX, liquidity) | Working |
| Economic calendar | Working (FOMC real; CPI/Jobs estimated) |
| On-chain (NVT, miner, hashrate) | Working |
| MVRV | **Proxy** — see limitations |
| ETF flow | **Proxy** — see limitations |

### Learning & tracking
| Feature | Status |
|---|---|
| Signal history + win/loss tracking | Working |
| Outcome recording on SL/TP hit | Working |
| Online learning (threshold adaptation) | Working — **dormant until 11 closed trades** |
| Post-mortem on losses | Working (generic template) |
| Portfolio simulator | Working — real historical walk |
| Trade ranking | Working |
| **Persistent paper account** | Working — survives between runs |
| **DCA / averaging down** | Working — up to 3 tranches |
| **Partial exit at TP1** | Working — 50% off, stop to breakeven |
| **Correlation exposure cap** | Working — 40% max per correlated bloc |

### Paper trading account

`docs/paper_account.json` persists across runs. Each cycle it marks positions to
market, then applies exits before entries:

1. **Stop-loss** → full exit
2. **TP1** → close 50%, move stop to breakeven on the runner
3. **TP2** → close the remainder
4. **DCA** → if a position is 5%+ underwater, the signal still holds, and TP1 has
   not been hit, add a smaller tranche (0.75x the last) and re-average entry.
   Max 3 tranches. The stop re-anchors to the new average entry.
5. **New entries** → highest conviction first, capped at 5 open positions, 30% of
   cash per entry, and 40% total exposure to any correlated bloc (|ρ| ≥ 0.7).

Every closed trade feeds `track_prediction_accuracy()`, which is what eventually
wakes the self-learning engine. Fees of 0.2% are charged on every fill.

This account is a **simulation**. It places no real orders — there is no exchange
API key, no order-placement code, and no execution path anywhere in this repo.

---

## Honest limitations

These are real. Do not mistake them for working features.

**"BiLSTM" and "CNN" are not neural networks.** They are rule-based heuristics. There is no trained model file in this repository. The ensemble math is real; the two inputs it averages are formulas, not learned models.

**MVRV is a proxy.** True MVRV Z-score requires realized-cap data from a paid on-chain API. This uses price vs its 200-day mean, z-scored. Labelled `mvrv_is_proxy: true`.

**ETF flow is a proxy.** True creation/redemption flows have no free API. This uses dollar volume across IBIT/FBTC/ARKB/BITB. Labelled `metric: dollar_volume_proxy`.

**CPI and Jobs dates are estimated.** FOMC dates are the real published schedule. CPI/Jobs use the standard recurring pattern and are flagged `estimated: true`.

**Daily resolution only.** No intraday history is stored, so minute-level scalp trades cannot be backtested. The portfolio simulator walks real daily closes and exits on real SL/TP hits — but a 15-minute trade and a 3-day trade are both tracked on daily candles.

**Self-learning is dormant, not broken.** It requires 11 closed trades before adjusting anything. Until then the dashboard shows `WARMING UP — n/11`, not a fabricated accuracy figure.

**Post-mortems are templated.** Every loss returns the same explanatory text. It reports *that* a stop was hit, not a genuine root-cause analysis.

**BIG/SMALL trades are frequently invisible.** BIG requires 60% conviction, SMALL requires 40%. In quiet markets most assets sit well below both, so the badges show "gated". That is the risk gate working, not a display bug.

---

## Setup

### Requirements
```bash
pip install -r requirements.txt
```

### Optional API keys (repo -> Settings -> Secrets -> Actions)
| Secret | Enables | Required? |
|---|---|---|
| `FRED_API_KEY` | Fed funds rate, macro regime | Recommended |
| `ETHERSCAN_API_KEY` | ETH gas metrics | Optional |
| `BEACONCHAIN_API_KEY` | ETH staking data | Optional |
| `DISCORD_WEBHOOK` | Signal alerts | Optional |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Signal alerts | Optional |

Everything else runs on free, keyless endpoints. Missing keys degrade gracefully — those features return empty, they do not crash the run.

### Run locally
```bash
python crypto_market_intelligence_v60.py
```

### Run tests
```bash
python offline_test.py
```
Mocks all network calls and runs the real pipeline end to end. **Back up `docs/` first — it writes there.**

### GitHub Pages
Settings -> Pages -> Source: **GitHub Actions**. The deploy job checks out `ref: main` so it always publishes the data the run just committed.

---

## Output files

| File | Contents |
|---|---|
| `docs/market_intelligence.json` | All 9 assets, signals, trade plans, risk, rankings, learning state |
| `docs/v6_results.json` | BTC deep analytics, macro, ML, calendar, narrative |
| `docs/signal_history.json` | Every signal ever generated + outcomes |
| `docs/signal_database.json` | Open positions + prediction outcomes (learning input) |
| `docs/market_summary.txt` | Plain-text briefing |

---

## Reading the dashboard

- **Conviction** — final confidence after multi-timeframe adjustment. `MTF x1.2` means all timeframes agreed and conviction was raised; `x0.3` means they conflicted and it was cut.
- **NO TRADE is the normal state.** The system is designed to be selective. Most assets, most of the time, will not qualify.
- **Win rate is not the goal.** A 40% win rate at 2:1 R:R is profitable. Judge on profit factor and expectancy.
- **Risk of ruin above ~50%** means position sizing is too aggressive for that asset's volatility.

---

## License & disclaimer

Educational use. No warranty. The author is not liable for financial losses. Cryptocurrency trading carries substantial risk of total capital loss.
