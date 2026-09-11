"""Scoped mutation testing against the trading/risk logic the test suite
actually targets. Not run against the whole file — most of it (API fetchers,
ML models, report generation) has no unit tests at all yet, so mutating it
would only prove what `coverage run` already shows: untested code has
untested mutants, which isn't new information. This targets the handful of
functions that decide real money movements, to measure whether the test suite
actually catches a bug there, rather than assuming it does because the tests
look thorough.

For each mutation: back up the file, apply it, run the full test suite,
restore the file, record KILLED (suite failed -> caught) or SURVIVED (suite
still passed -> a real bug here would ship undetected). Never leaves the
source file mutated, even on a crash mid-run.

    python3 mutation_test.py

Exit code 1 if anything survives.
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(REPO, "crypto_market_intelligence_v60.py")
TEST_CMD = ["python3", "test_system.py"]

MUTATIONS = [
    ("sign flip: SHORT treated as LONG in position sizing",
     "sign = -1 if str(direction).upper() in ('SHORT', 'STRONG SHORT', 'SELL') else 1",
     "sign = 1 if str(direction).upper() in ('SHORT', 'STRONG SHORT', 'SELL') else 1"),

    ("stop multiplier changed 2.0 -> 3.0",
     "'atr_multiplier_stop': 2.0,",
     "'atr_multiplier_stop': 3.0,"),

    ("target multiplier changed 4.0 -> 5.0",
     "'atr_multiplier_target': 4.0,",
     "'atr_multiplier_target': 5.0,"),

    ("zero-crossing clamp removed (99% -> 200% of price, can cross zero again)",
     "max_distance = price * 0.99",
     "max_distance = price * 2.0"),

    ("_paper_close: LONG pnl sign flipped",
     "        gross = qty * price\n        pnl = (price - pos['avg_entry']) * qty",
     "        gross = qty * price\n        pnl = (pos['avg_entry'] - price) * qty"),

    ("_paper_close: SHORT pnl sign flipped",
     "        gross = qty * pos['avg_entry']\n        pnl = (pos['avg_entry'] - price) * qty",
     "        gross = qty * pos['avg_entry']\n        pnl = (price - pos['avg_entry']) * qty"),

    ("SUCCESS_REASONS silently drops TAKE_PROFIT_2",
     "SUCCESS_REASONS = {'TAKE_PROFIT_1_PARTIAL', 'TAKE_PROFIT_2'}",
     "SUCCESS_REASONS = {'TAKE_PROFIT_1_PARTIAL'}"),

    ("corrupted-SHORT detector loses the side check (flags LONGs too)",
     "    return t.get('side') == 'SHORT' and t.get('reason') == 'STOP_LOSS' and t.get('pnl', 0) > 0",
     "    return t.get('reason') == 'STOP_LOSS' and t.get('pnl', 0) > 0"),

    ("win/loss boundary: pnl > 0 relaxed to >= 0 (a breakeven trade counts as a win)",
     "    wins = [t for t in closed if t['pnl'] > 0]\n    losses = [t for t in closed if t['pnl'] <= 0]",
     "    wins = [t for t in closed if t['pnl'] >= 0]\n    losses = [t for t in closed if t['pnl'] < 0]"),

    ("min_profitable_move_pct: safety margin dropped from 1.5x to 1.0x (ties, doesn't beat, costs)",
     "def min_profitable_move_pct(margin=1.5):",
     "def min_profitable_move_pct(margin=1.0):"),

    ("LONG stop-loss check: <= relaxed to < (exact stop price no longer triggers)",
     "        if sl and ((long and price <= sl) or (not long and price >= sl)):\n            fill = sl * (1 - slip) if long else sl * (1 + slip)\n            pnl = _paper_close(acct, code, pos, fill, pos['qty'], 'STOP_LOSS')\n            events.append(f\"{code} stopped out ({pnl:+.2f})\")",
     "        if sl and ((long and price < sl) or (not long and price >= sl)):\n            fill = sl * (1 - slip) if long else sl * (1 + slip)\n            pnl = _paper_close(acct, code, pos, fill, pos['qty'], 'STOP_LOSS')\n            events.append(f\"{code} stopped out ({pnl:+.2f})\")"),

    ("DCA drawdown trigger: >= relaxed to > (exact-threshold drawdown no longer DCAs)",
     "                and drawdown >= PAPER_CONFIG['dca_trigger_drawdown']",
     "                and drawdown > PAPER_CONFIG['dca_trigger_drawdown']"),

    ("trailing stop: ratchet direction inverted for LONG (would loosen, not tighten)",
     "            if long:\n                new_stop = price - trail_dist\n                if new_stop > pos['stop_loss']:\n                    pos['stop_loss'] = new_stop",
     "            if long:\n                new_stop = price - trail_dist\n                if new_stop < pos['stop_loss']:\n                    pos['stop_loss'] = new_stop"),

    ("time-exit multiplier: 3x expected duration relaxed to 30x (barely ever fires)",
     "    'time_exit_multiplier': 3,",
     "    'time_exit_multiplier': 30,"),

    ("intraday round-trip-cost floor check inverted (lets sub-cost targets through)",
     "        if abs(float(it['take_profit']) - price) < price * min_profitable_move_pct():",
     "        if abs(float(it['take_profit']) - price) > price * min_profitable_move_pct():"),

    ("portfolio risk cap removed (new entries never blocked regardless of open risk)",
     "        if equity_now and (open_risk + this_risk) / equity_now > RISK_PARAMS['max_portfolio_risk']:\n            events.append(f\"{code} skipped — portfolio risk cap \"\n                          f\"({(open_risk / equity_now * 100):.1f}% already at risk)\")\n            continue\n        open_risk += this_risk\n\n        acct['cash'] -= notional * (1 + fee)",
     "        open_risk += this_risk\n\n        acct['cash'] -= notional * (1 + fee)"),
]


def run_tests():
    r = subprocess.run(TEST_CMD, cwd=REPO, capture_output=True, text=True, timeout=180)
    return r.returncode == 0, r.stdout + r.stderr


def main():
    with tempfile.TemporaryDirectory() as tmp:
        backup = os.path.join(tmp, "cmi_backup.py")
        shutil.copy(SRC, backup)
        results = []
        try:
            for name, old, new in MUTATIONS:
                src = open(SRC).read()
                if old not in src:
                    results.append((name, "SKIPPED", "pattern not found in current source"))
                    continue
                mutated = src.replace(old, new, 1)
                open(SRC, 'w').write(mutated)
                passed, output = run_tests()
                shutil.copy(backup, SRC)   # restore immediately, before the next mutation
                if passed:
                    results.append((name, "SURVIVED", "test suite still passed with this bug present"))
                else:
                    fail_line = next((l for l in output.splitlines() if l.startswith('FAIL:')), '')
                    results.append((name, "KILLED", fail_line or "suite failed"))
        finally:
            shutil.copy(backup, SRC)   # guarantee restoration even on crash

    print("=" * 70)
    for name, status, detail in results:
        marker = {"KILLED": " killed ", "SURVIVED": "SURVIVED", "SKIPPED": " skip  "}[status]
        print(f"[{marker}] {name}")
        if status != "KILLED":
            print(f"           {detail}")
    killed = sum(1 for _, s, _ in results if s == "KILLED")
    survived = sum(1 for _, s, _ in results if s == "SURVIVED")
    skipped = sum(1 for _, s, _ in results if s == "SKIPPED")
    print("=" * 70)
    print(f"{killed} killed / {survived} survived / {skipped} skipped (of {len(results)} mutations)")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
