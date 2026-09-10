# Progress Log — Crypto Market Intelligence

Rebuilt from the claude.ai chat "Crypto market intelligence repository issue"
(234 messages, 2026-08-25 → 2026-09-09) after moving to Claude Code.

## Fixed and confirmed live (do not re-litigate)
- Deploy workflow staleness — `deploy` job now checks out `ref: main` (was building stale pre-push snapshot).
- "Why This Trade?" — was hardcoded "72% win rate" string; now pulls real per-asset `sub_signals` + `signal_history.win_rate`.
- BIG/SMALL trade sizing — was BTC-only, copy-pasted to all 9 assets; now computed per-asset.
- Macro regime (Fed/DXY/Liquidity/VIX) — backend now computes and saves `macro_data`; dead frontend stub that force-blanked it and hardcoded "RISK ON" was removed.
- Support/resistance wrong-side bug — 2% tolerance let resistance land below price; fixed, no tolerance, verified across all 9 assets.
- SMALL trade permanently gated — signal threshold (0.20) vs sizing gate threshold (0.40) mismatch; aligned.
- Binance 451 (GitHub Actions IPs geo-blocked) — added Kraken → Yahoo fallback chain for 4h klines. Later found the **daily** klines fetch had no fallback at all (same root cause silently breaking MVRV, explanation history, event-risk range) — fixed same way.
- blockchain.info also blocks CI IPs — added mempool.space fallback for hashrate/miner reserves.
- Fake 109x backtest outlier — stop-distance calc had no floor, one trade landed with $6.20 risk distance and inflated profit factor to 3.73 (real: 1.19 on that period). Fixed with 0.5 ATR minimum stop distance. Confirmed same trade now reports r_multiple 2.04.
- `big_trade`/`small_trade` showing a position size while `trade_qualified: false` — second ungated code path, fixed to match the gated one.
- Intraday engine was structurally incapable of trading in choppy/ranging markets (trend-only mode). Added range/mean-reversion mode: buffered stop beyond support/resistance (was sitting exactly on it), target at range midpoint (was defaulting closer than stop → auto-rejected), fixed the "is this a range?" detector for the sell side (was always reading bullish near resistance).
- Duplicate `fetch_fred_data` definition removed (verified byte-identical bodies first, no behavior change).
- Fed rate / TPU now say `"FRED_API_KEY not configured"` instead of misleading `0` when the key is absent.

Test suite: 57/57 passing as of last commit in the old chat. 147 functions, 2 harmless dead wrappers.

## Confirmed still true in THIS repo checkout (verified 2026-09-10)
- DCA re-anchoring bug is real and unfixed — `crypto_market_intelligence_v60.py` line ~2362-2365: on each DCA tranche, stop is re-set to `avg_entry ± risk_distance` using the *new* average entry. Up to 3 tranches (`dca_max_tranches`) means the stop keeps retreating as price falls, so a losing trade structurally struggles to ever register as a loss. This is what produced the suspicious 31/31 swing win rate the user flagged. **Not yet fixed.**

## Open items (unresolved as of session handoff)
1. **DCA fix, proposed not yet implemented**: cap at 1 add max, and/or stop should never widen past its original distance from the *first* entry (not re-anchored each tranche).
2. **Visually distinguish filtered vs. traded signals** on the dashboard — Signal Performance (all signals incl. filtered) vs Paper Trading Account (only gated ones) currently blend together with wildly different win rates (26-30% vs 82-89%) and no label explaining why.
3. **Portfolio Simulator stuck at $10,000 / 0%** — confirmed correct behavior, not a bug: every asset is NO TRADE right now, so it opens nothing. No action needed unless user wants this explained better in the UI.
4. **FRED_API_KEY** — user was about to add this (free key, St. Louis Fed) to unlock Fed rate / TPU. Unknown if done yet — check `market_intelligence.json`/`v6_results.json` for `fed_trend` != "UNKNOWN".
5. On-chain data (MVRV, hashrate) intermittently null — lower priority, not fully root-caused beyond the fallback fixes above.

## Three tracker types (for reference, came up in user Q&A)
- **Portfolio Simulator**: "if I acted on today's signals right now" snapshot, resets every cycle. Not a track record.
- **Signal Performance**: logbook of every signal ever generated, including filtered/weak ones.
- **Paper Trading Account**: the real simulated-money account — only opens on signals that clear conviction gates, manages DCA/trailing stops/partial exits.
