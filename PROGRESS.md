# Progress Log — Crypto Market Intelligence

Context rebuilt from the claude.ai chat "Crypto market intelligence repository issue"
(234 messages, 2026-08-25 → 2026-09-09), then verified against the actual code.

## How to verify anything here yourself

```bash
pip install -r requirements.txt pyflakes
python3 test_system.py          # 36 regression tests, no network needed
python3 -m pyflakes crypto_market_intelligence_v60.py
```

Every test corresponds to a bug that really shipped. Previous sessions claimed
"57/57 tests pass" but the test file was never committed — it existed only in a
chat sandbox, so none of it was reproducible. `test_system.py` is the real thing.

---

## Session 2026-09-10 — findings and fixes

### 1. SHORT trades had completely inverted risk management (most serious)
`calculate_dynamic_position_size()` had **no direction parameter**. It always set
`stop_loss = price - distance` and both take-profits *above* price — hardcoded for
a LONG. Every SHORT therefore got:
- a "stop loss" BELOW entry, i.e. in the profit direction
- "take profits" ABOVE entry, i.e. in the loss direction

Evidence in `docs/paper_account.json`: all 31 closed `SWING_DAILY` trades are
SHORTs that exited with `reason: STOP_LOSS` while booking **+$101 to +$132 profit
each**, every one flagged `successful: false`.

Fixed: the function now takes `direction` and mirrors the levels. The call site
moved to after the signal is finalised, since direction isn't known before that.

**This, not DCA, was the cause of the 31-wins-from-31-swings result.** The prior
chat attributed it to DCA stop re-anchoring; every closed trade has `tranches: 1`,
so DCA never fired once. That diagnosis was wrong.

### 2. Sub-dollar assets had meaningless trade levels
`generate_trade_plan()` rounded every price with `round(x, 2)`. For DOGE at
$0.095 the stop rounds to $0.10 — identical to entry. Risk distance collapses to
zero, so risk/reward reported 0 and the displayed stop/targets were nonsense.
Affected DOGE, ADA and XRP on every run. Fixed with magnitude-aware rounding
(`_round_price`). Found by a test, not by reading.

### 3. Missing data was silently manufacturing bullish signals
`predict_bilstm()` did `mvrv = onchain.get('mvrv_zscore', 0) or 0`. Zero satisfies
the *bullish* branch of both the MVRV and NVT tests, so a failed on-chain fetch
produced a 0.7 bullish score indistinguishable from a real one. Absent inputs are
now skipped instead of scored.

### 4. Missing data was producing confident market-regime verdicts
No `FRED_API_KEY` meant `tpu_value = 0`, which flowed into `detect_market_regime()`
and returned a definitive "LOW_UNCERTAINTY — fundamentals-driven market". Now
returns `UNKNOWN_UNCERTAINTY`; `adjust_weights()` already had a safe default branch.

### 5. Fabricated historical win rate
`historical_win_rate` defaulted to `50`, and the trader comment printed
"Historical win rate: 50%" as measured fact even with zero historical data. Now
`None`, and the comment says there are no comparable setups yet.

### 6. Trade counts included non-trades
951 of 1000 logged rows were `NO TRADE`, yet `total_signals` reported 1000 and
`avg_conviction` averaged across them (0.06). Now reported separately:
**49 tradeable signals, 951 NO TRADE readings**, avg conviction **0.37**.
Also: the rolling 1000-row cap meant NO TRADE noise steadily evicted real signals;
the two classes are now trimmed independently.

### 7. Filtered vs traded signals are now labelled
Signal Performance counts *every* generated signal; the Paper Trading Account only
counts ones it actually opened. Different populations, so the win rates legitimately
differ. Both are now labelled on the dashboard and in the JSON (`population_label`).

### 8. One broken chart could blank the whole dashboard
If the Chart.js CDN failed, `renderDashboard` threw, the catch replaced the entire
page with "Unable to Load Data", and every real number vanished. Chart rendering is
now isolated (`safeRender`) and guarded on `typeof Chart`.

### 9. `STRONG_SHORT` counter could never be non-zero
Compared against `'STRONG_SHORT'` (underscore) while the engine emits
`'STRONG SHORT'` (space).

### 10. Ported the 2026-09-08 round that was never pushed
- `fetch_binance_klines` (daily) had no fallback while its 4h sibling did — now
  shares the Binance → Kraken → Yahoo chain. This was starving MVRV, the
  explanation history, and the event-risk range simultaneously.
- Added mempool.space fallback for hashrate and miner revenue (blockchain.info
  blocks CI IPs, same class of block as Binance's HTTP 451).
- Removed the duplicate `fetch_fred_data` definition.
- Fed rate / TPU now say `FRED_API_KEY not configured` instead of a misleading `0`.

### 11. Honesty and dead-code cleanup
- Removed 2 genuinely unreferenced functions (`get_order_book_imbalance`,
  `build_explanation_json`).
- `"ALL 42 FEATURES — FULLY WORKING"` banner → accurate description.
- `"ML models loaded successfully"` loaded no model — it only probes whether
  tensorflow/sklearn import. `predict_bilstm` is a hand-written rule, not a
  BiLSTM. Labels corrected; behaviour unchanged.
- Misleading neutral defaults (`altcoin_season = 50`, `nvt/mvrv/miner = 0`) now
  `None` so the dashboard shows "—" instead of a real-looking reading.
- pyflakes: 15 findings → 2 (both intentional `noqa` availability probes).

### 12. Plain-English guide on every dashboard section
All 11 sections have a "What am I looking at?" panel written for a non-technical
reader: what the section does, what each number means, and what is normal vs
worrying. Verified rendering in a real browser.

---

## Open items / decisions for you

1. **`docs/paper_account.json` history is corrupt and I did not touch it.**
   Those 31 swing trades were produced by the inverted-short bug. Their P&L
   arithmetic is right but they should never have been opened or closed that way,
   so every stat derived from them is misleading. Recommend archiving the file and
   letting the account restart at $10,000 — your call, since it is your track
   record and deleting it is not reversible.
2. **`FRED_API_KEY`** — still not configured. Free key from the St. Louis Fed,
   goes in repo Settings → Secrets → Actions. Until then Fed rate and TPU
   correctly report "not configured".
3. **Nothing here is verified against a live run.** These fixes are proven by the
   test suite and by direct inspection of the committed JSON. The next GitHub
   Actions run is the real check — particularly whether SHORT signals now produce
   sane exits.
4. Zero closed trades exist under the corrected logic, so the system still has no
   track record. That is a matter of elapsed time, not code.
