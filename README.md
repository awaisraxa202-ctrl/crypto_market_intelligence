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
| **Two trade types per coin** | Working — SWING (daily, weeks) + INTRADAY (4h, hours-days) |
| **Persistent paper account** | Working — survives between runs, tracks both types separately |
| **DCA / averaging down** | Working — up to 3 tranches |
| **Partial exit at TP1** | Working — 50% off, stop to breakeven |
| **Correlation exposure cap** | Working — 40% max per correlated bloc |

### Accuracy features (built on top of the two trade types)

| Feature | What it does |
|---|---|
| **4h engine backtest** | Real walk-forward backtest over ~5-6 months of history, using the IDENTICAL scoring code the live engine uses. Runs automatically every cycle for BTC/ETH. No lookahead bias. |
| **TP1 = success** | Hitting the first take-profit is booked as a successful trade everywhere — signal history, paper account, dashboard labels. Running to TP2 is a bonus, not a requirement. |
| **Time-based exits** | A trade still open at 3x its expected duration closes automatically — a stale thesis shouldn't sit tying up capital until it randomly hits stop or target. |
| **Trailing stop** | After TP1, the stop ratchets behind price instead of sitting at a flat breakeven — locks in more of a winning run. |
| **Real order flow (CVD)** | Cumulative Volume Delta from actual executed trades (Binance aggTrades), not order-book snapshots. Adjusts intraday conviction up or down based on genuine buy/sell pressure. |
| **Liquidation cluster estimate** | An honest proxy (round price levels + funding-rate skew) since a real liquidation feed needs a paid API. Labelled as an estimate everywhere it appears. |
| **Regime-conditional weights** | Signal weights (trend/momentum/volatility/etc) now actually shift with detected regime — trend-following weighted up in strong trends, down in chop — instead of being one static number regardless of market condition. |
| **Walk-forward feedback** | Each asset's out-of-sample backtest result is saved and used to nudge that asset's trend/momentum weight on the NEXT cycle — genuine learning across runs, not a number computed and discarded. |
| **Slippage** | Modeled on every simulated fill (0.08%), so paper results aren't more optimistic than live trading would be. |

### Two trade types

Every coin gets two independent trades on two timeframes:

| | SWING | INTRADAY |
|---|---|---|
| Timeframe | Daily candles | 4-hour candles |
| Targets | ATR multiples (often 10-25% away) | Nearest real 4h support/resistance (typically 1-4%) |
| Typical BTC move | $10k+ | $1-3k |
| Resolves in | Weeks | Hours to a few days |
| Purpose | Capture large moves | Generate frequent outcomes so the learning engine has data |
| Conviction gate | 40% / 60% | 35% |

Both run in the paper account as separate positions (a swing LONG and a 4h SHORT can
coexist on the same coin) with separate win-rate and P&L statistics, so the system's
accuracy on fast trades and slow trades is measured independently.

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

**Scheduled runs drift.** GitHub delays cron-triggered Actions on low-traffic repos, sometimes by hours. The main pipeline can't fix this from inside the workflow file — it's a GitHub platform behavior. See "Free frequent position monitoring" below for the workaround that matters (catching stop-loss/take-profit in between full runs).

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

### Free frequent position monitoring (optional)

GitHub's own cron scheduler delays scheduled Actions on low-traffic repos —
sometimes by hours. That's fine for the full analysis (it runs on daily data
anyway), but it means a stop-loss or take-profit on the paper account could sit
unnoticed for hours. `position-monitor.yml` fixes this for free:

1. It only checks **existing open positions** against live price — fast, cheap,
   no full indicator run. It does not open new trades or DCA.
2. It's triggered by `workflow_dispatch`, not GitHub's cron, because an external
   trigger doesn't suffer the same scheduling delay.
3. Set up a free trigger at **cron-job.org**:
   - Create a GitHub Personal Access Token: GitHub -> Settings -> Developer
     settings -> Fine-grained tokens -> generate one scoped to this repo only,
     with **Actions: Read and write** permission.
   - At cron-job.org, create a free account and a new cron job:
     - URL: `https://api.github.com/repos/awaisraxa202-ctrl/crypto_market_intelligence/actions/workflows/position-monitor.yml/dispatches`
     - Method: `POST`
     - Headers: `Authorization: Bearer YOUR_TOKEN`, `Accept: application/vnd.github+json`
     - Body: `{"ref":"main"}`
     - Schedule: every 10-15 minutes
4. That's it — no server, no hosting cost.

### Making the 2-hour update actually happen every 2 hours

The dashboard header says "Every 2h" and `update.yml` has `cron: '0 */2 * * *'`,
but GitHub does not honour that reliably on low-traffic repos — observed gaps of
6-8 hours are normal. The fix is the same external-trigger trick, pointed at the
main workflow:

At cron-job.org, create a **second** cron job:

- URL: `https://api.github.com/repos/awaisraxa202-ctrl/crypto_market_intelligence/actions/workflows/update.yml/dispatches`
- Method: `POST`
- Headers: `Authorization: Bearer YOUR_TOKEN`, `Accept: application/vnd.github+json`
- Body: `{"ref":"main"}`
- Schedule: **every 2 hours** (e.g. at minute 0 of every 2nd hour)

Use the same Personal Access Token as the position monitor. Leave the `schedule:`
block in `update.yml` alone — it's a harmless fallback. If GitHub's cron happens
to fire close to the external trigger you may occasionally get two runs back to
back; that's wasteful but not harmful, and the second run simply overwrites the
first with fresher data.

**Free-tier note:** cron-job.org's free plan allows a 1-minute minimum interval,
so both jobs fit comfortably. The full pipeline takes ~4 minutes per run; at 12
runs/day that's ~50 minutes/day of Actions time. Public repos get unlimited free
Actions minutes, so this costs nothing.

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