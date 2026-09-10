"""Live-data integrity audit for crypto_market_intelligence.

This is NOT part of the dashboard and never writes to docs/. It is a read-only
verdict on the data the system just produced. Run it any time you want to know
whether the system is actually working right now:

    python3 audit_system.py                 # audit the committed docs/ files
    python3 audit_system.py --dir /tmp/x    # audit files pulled from anywhere

Exit code is 0 when every check passes, 1 when any check FAILS.

Why this exists: three real bugs were found in this project in one day, and two
of them were invisible in the source code — they only showed up as impossible
combinations in the live output (a SHORT booking profit on a stop-loss; a
"trailing" stop sitting below entry). Reading code does not catch those. Checking
the produced data against invariants does. Every check below corresponds to a
way this system has actually been wrong, or a claim the dashboard makes that
must be backed by a real calculation.

Categories:
    FILES        the outputs exist, parse, and contain no NaN poison
    FRESHNESS    the pipeline actually ran recently; nothing is stale
    PROVENANCE   no fabricated numbers — an unknown reads as unknown
    SANITY       every displayed number is finite and in a possible range
    LOGIC        trading invariants that cannot be violated by correct code
    CONSISTENCY  the same fact agrees across files and against recomputation
    WIRING       every value the dashboard reads exists in the data
"""
import argparse
import importlib.util
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta

PASS, FAIL, WARN, INFO = 'PASS', 'FAIL', 'WARN', 'INFO'

# The pipeline runs every 2h. Anything older than this means it stopped running
# and the dashboard is showing history while looking live.
STALE_WARN_HOURS = 3
STALE_FAIL_HOURS = 8


class Audit:
    def __init__(self):
        self.results = []

    def add(self, category, name, status, detail=''):
        self.results.append((category, name, status, detail))

    def check(self, category, name, condition, ok_detail='', bad_detail='', warn_only=False):
        """Record PASS when condition is true, else FAIL (or WARN)."""
        if condition:
            self.add(category, name, PASS, ok_detail)
        else:
            self.add(category, name, WARN if warn_only else FAIL, bad_detail)
        return bool(condition)

    @property
    def failures(self):
        return [r for r in self.results if r[2] == FAIL]

    @property
    def warnings(self):
        return [r for r in self.results if r[2] == WARN]

    def report(self):
        symbols = {PASS: '  ok  ', FAIL: ' FAIL ', WARN: ' warn ', INFO: ' info '}
        current = None
        for category, name, status, detail in self.results:
            if category != current:
                print(f"\n─── {category} " + "─" * max(0, 56 - len(category)))
                current = category
            line = f"[{symbols[status]}] {name}"
            if detail:
                line += f"\n              {detail}"
            print(line)

        total = len(self.results)
        n_fail, n_warn = len(self.failures), len(self.warnings)
        n_pass = total - n_fail - n_warn
        print("\n" + "=" * 64)
        print(f"{n_pass}/{total} checks passed"
              + (f" · {n_warn} warning(s)" if n_warn else '')
              + (f" · {n_fail} FAILURE(S)" if n_fail else ''))
        if n_fail:
            print("\nSystem is NOT clean. Failures:")
            for category, name, _, detail in self.failures:
                print(f"  • [{category}] {name}: {detail}")
        elif n_warn:
            print("\nNo failures. Warnings are worth a look but are not broken state.")
        else:
            print("\nEvery check passed against this data.")
        print("=" * 64)
        return 1 if n_fail else 0


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _age_hours(ts):
    try:
        return (datetime.now() - datetime.fromisoformat(str(ts))).total_seconds() / 3600
    except Exception:
        return None


def _walk_numbers(obj, path=''):
    """Yield (path, value) for every numeric leaf, for NaN/Infinity hunting."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_numbers(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_numbers(v, f"{path}[{i}]")
    elif isinstance(obj, float):
        yield path, obj


# ───────────────────────── check groups ─────────────────────────

def check_files(a, docs, raw_text):
    expected = ['market_intelligence.json', 'paper_account.json', 'signal_history.json',
                'signal_database.json', 'v6_results.json', 'market_summary.txt']
    for name in expected:
        a.check('FILES', f"{name} present", name in raw_text,
                bad_detail=f"{name} is missing from the docs directory")

    for name, text in raw_text.items():
        if not name.endswith('.json'):
            continue
        # NaN/Infinity are valid Python floats but INVALID JSON. json.load accepts
        # them by default; a browser's JSON.parse rejects the whole file, which
        # blanks the dashboard. This has happened before.
        bad = re.findall(r'(?<![\w"])(NaN|-?Infinity)(?![\w"])', text)
        a.check('FILES', f"{name} is browser-parseable JSON", not bad,
                ok_detail='no NaN/Infinity literals',
                bad_detail=f"contains {len(bad)} NaN/Infinity literal(s) — JSON.parse "
                           f"would reject this file and blank the dashboard")

    for name, data in docs.items():
        if not name.endswith('.json'):
            continue  # market_summary.txt is plain text by design
        a.check('FILES', f"{name} parses", data is not None,
                bad_detail='failed to parse as JSON')


def check_freshness(a, mi, pa, sh):
    gen = mi.get('generated_at')
    age = _age_hours(gen)
    if age is None:
        a.add('FRESHNESS', 'market_intelligence generated_at readable', FAIL,
              f"unparseable timestamp: {gen!r}")
    else:
        status = PASS if age < STALE_WARN_HOURS else (WARN if age < STALE_FAIL_HOURS else FAIL)
        a.add('FRESHNESS', 'pipeline ran recently', status,
              f"last full run {age:.1f}h ago (runs every 2h){'' if status == PASS else ' — pipeline may have stopped'}")

    # Every asset should carry a date from the current run, not a cached older one.
    assets = mi.get('assets', {})
    stale_assets = []
    for code, asset in assets.items():
        d = asset.get('date')
        try:
            days = (datetime.now() - datetime.fromisoformat(str(d))).days
        except Exception:
            stale_assets.append(f"{code}(unparseable:{d})")
            continue
        if days > 1:
            stale_assets.append(f"{code}({days}d old)")
    a.check('FRESHNESS', 'all asset prices are current', not stale_assets,
            ok_detail=f"{len(assets)} assets dated today/yesterday",
            bad_detail=f"stale: {', '.join(stale_assets)}")

    curve = pa.get('equity_curve') or []
    if curve:
        age_eq = _age_hours(curve[-1].get('ts'))
        a.check('FRESHNESS', 'paper account marked to market recently',
                age_eq is not None and age_eq < STALE_FAIL_HOURS,
                ok_detail=f"last equity snapshot {age_eq:.1f}h ago" if age_eq is not None else '',
                bad_detail=f"last equity snapshot {age_eq}h ago" if age_eq is not None else 'unreadable ts')

    sigs = sh.get('signals') or []
    if sigs:
        age_sig = _age_hours(sigs[-1].get('timestamp'))
        a.check('FRESHNESS', 'signal log is being appended',
                age_sig is not None and age_sig < STALE_FAIL_HOURS,
                ok_detail=f"newest signal {age_sig:.1f}h ago" if age_sig is not None else '',
                bad_detail=f"newest signal {age_sig}h ago — signals may have stopped logging"
                           if age_sig is not None else 'unreadable ts')


def check_provenance(a, mi, pa, sh, v6):
    """No fabricated numbers. An unknown must read as unknown, never as a
    plausible-looking figure. This whole category exists because win rates were
    once computed as `0.45 + conviction * 0.3` — a formula, not a measurement."""

    sp = mi.get('signal_performance') or {}
    closed = sp.get('closed_signals') or 0
    wr = sp.get('win_rate')
    a.check('PROVENANCE', 'signal win rate is measured, not invented',
            closed > 0 or not wr,
            ok_detail=f"{wr}% from {closed} closed signals" if closed else 'no closed signals, no number claimed',
            bad_detail=f"claims win_rate={wr} with {closed} closed signals — that number "
                       f"cannot come from evidence")

    ps = pa.get('stats') or {}
    pclosed = ps.get('closed_trades') or 0
    pwr = ps.get('win_rate')
    a.check('PROVENANCE', 'paper account win rate is measured, not invented',
            pclosed > 0 or pwr is None,
            ok_detail=f"{pwr}% from {pclosed} closed trades" if pclosed else 'no closed trades, win_rate is null',
            bad_detail=f"claims win_rate={pwr} with {pclosed} closed trades")

    # Per-asset embedded history must obey the same rule.
    liars = []
    for code, asset in (mi.get('assets') or {}).items():
        h = asset.get('signal_history') or {}
        if (h.get('closed_trades') or 0) == 0 and h.get('win_rate'):
            liars.append(f"{code}(win_rate={h.get('win_rate')})")
    a.check('PROVENANCE', 'per-asset win rates are measured, not invented', not liars,
            ok_detail='every asset with zero closed trades reports no win rate',
            bad_detail=f"assets claiming a win rate with no closed trades: {', '.join(liars)}")

    sl = mi.get('self_learning') or {}
    if str(sl.get('status')) == 'WARMING_UP':
        a.check('PROVENANCE', 'self-learning admits it is warming up',
                not sl.get('recent_accuracy'),
                ok_detail=f"status WARMING_UP with {sl.get('sample_size')} samples, no accuracy claimed",
                bad_detail=f"status WARMING_UP but reports recent_accuracy={sl.get('recent_accuracy')}")
    else:
        a.add('PROVENANCE', 'self-learning accuracy is backed by samples',
              PASS if (sl.get('sample_size') or 0) > 0 else FAIL,
              f"accuracy {sl.get('recent_accuracy')} from {sl.get('sample_size')} samples")

    # Macro / TPU: when the upstream source is missing, the regime must say so
    # rather than defaulting to a confident-sounding label.
    tpu = v6.get('tpu_data') or {}
    macro = v6.get('macro_data') or {}
    if str(tpu.get('source')) == 'fallback':
        a.check('PROVENANCE', 'TPU fallback is labelled, not disguised',
                tpu.get('tpu_value') in (0, None) or tpu.get('regime') in (None, 'UNKNOWN'),
                ok_detail="source=fallback and no confident TPU value is asserted",
                bad_detail=f"source=fallback yet asserts tpu_value={tpu.get('tpu_value')} "
                           f"regime={tpu.get('regime')}")
    if macro:
        # NOT_CONFIGURED / UNKNOWN are honest labels — the FRED key is absent and
        # the system says so instead of inventing a rate. What would FAIL here is a
        # confident-sounding trend (TIGHTENING/EASING) with no rate behind it.
        honest_labels = ('UNKNOWN', 'NOT_CONFIGURED', 'UNAVAILABLE', None)
        unknown_ok = macro.get('fed_trend') in honest_labels or _finite(macro.get('fed_funds_rate'))
        a.check('PROVENANCE', 'macro data is real or honestly labelled unavailable', unknown_ok,
                ok_detail=f"fed_trend={macro.get('fed_trend')} (no rate invented)",
                bad_detail=f"fed_trend={macro.get('fed_trend')} asserts a direction with no "
                           f"backing rate value")

    perf = sh.get('performance') or {}
    basis = perf.get('win_rate_basis')
    a.check('PROVENANCE', 'signal history states the basis of its win rate', bool(basis),
            ok_detail=str(basis),
            bad_detail='no win_rate_basis — a number with no stated population is not verifiable')


def check_sanity(a, mi, pa):
    """Every number the dashboard displays must be finite and in a possible range."""
    bad_nums = []
    for path, value in _walk_numbers(mi):
        if not math.isfinite(value):
            bad_nums.append(f"{path}={value}")
    a.check('SANITY', 'no NaN/Infinity anywhere in market intelligence', not bad_nums,
            ok_detail='all numeric leaves finite',
            bad_detail=f"{len(bad_nums)} non-finite value(s): {', '.join(bad_nums[:5])}")

    bad_prices, bad_conv = [], []
    for code, asset in (mi.get('assets') or {}).items():
        p = asset.get('price')
        if not _finite(p) or p <= 0:
            bad_prices.append(f"{code}={p}")
        c = asset.get('conviction')
        if c is not None and (not _finite(c) or not 0 <= c <= 1):
            bad_conv.append(f"{code}={c}")
    a.check('SANITY', 'every asset price is a positive real number', not bad_prices,
            ok_detail=f"{len(mi.get('assets') or {})} assets priced",
            bad_detail=f"impossible prices: {', '.join(bad_prices)}")
    a.check('SANITY', 'every conviction is within 0..1', not bad_conv,
            bad_detail=f"out of range: {', '.join(bad_conv)}")

    fg = mi.get('fear_greed') or {}
    v = fg.get('value')
    a.check('SANITY', 'fear & greed index within 0..100',
            v is None or (_finite(v) and 0 <= v <= 100),
            ok_detail=f"{v} ({fg.get('label')})", bad_detail=f"impossible value: {v}")

    bad_pos = []
    for code, p in (pa.get('positions') or {}).items():
        if not _finite(p.get('qty')) or p.get('qty', 0) <= 0:
            bad_pos.append(f"{code}(qty={p.get('qty')})")
        if not _finite(p.get('avg_entry')) or p.get('avg_entry', 0) <= 0:
            bad_pos.append(f"{code}(entry={p.get('avg_entry')})")
    a.check('SANITY', 'every open position has real size and entry', not bad_pos,
            ok_detail=f"{len(pa.get('positions') or {})} open positions",
            bad_detail=f"impossible: {', '.join(bad_pos)}")

    cash = pa.get('cash')
    a.check('SANITY', 'cash balance is a finite number', _finite(cash),
            ok_detail=f"${cash:,.2f}" if _finite(cash) else '', bad_detail=f"cash={cash}")


def check_logic(a, pa, cmi):
    """Invariants that correct trading code cannot violate. Each one here maps to
    a bug that actually shipped."""
    closed = pa.get('closed_trades') or []

    # BUG 1 (fixed): inverted SHORT levels made stop-losses pay out.
    corrupted = [t for t in closed if cmi._is_corrupted_short_stopout(t)]
    known = (pa.get('stats') or {}).get('excluded_corrupted') or 0
    new_corrupt = [t for t in corrupted
                   if str(t.get('opened', '')) > '2026-09-10T21:00']
    a.check('LOGIC', 'no NEW stop-loss trade booked a profit', not new_corrupt,
            ok_detail=f"{len(corrupted)} historical corrupted row(s), all pre-fix, "
                      f"{known} excluded from stats",
            bad_detail=f"{len(new_corrupt)} SHORT trade(s) opened after the fix still "
                       f"closed STOP_LOSS with positive pnl — the fix is not working")

    # A take-profit exit must make money; a stop-loss exit must not (except a LONG
    # whose stop legitimately trailed to breakeven-or-better after a partial).
    contradictions = []
    for t in closed:
        reason, pnl, side = t.get('reason'), t.get('pnl', 0), t.get('side')
        if reason in cmi.SUCCESS_REASONS and pnl < 0:
            contradictions.append(f"{t.get('asset')} {side} {reason} pnl={pnl}")
    a.check('LOGIC', 'no take-profit exit recorded a loss', not contradictions,
            ok_detail='every target exit made money',
            bad_detail=f"{len(contradictions)}: {', '.join(contradictions[:4])}")

    # BUG 3 (fixed): intraday stops "trailed" without ever reaching breakeven.
    wrong_side, phantom_trail = [], []
    for code, p in (pa.get('positions') or {}).items():
        side, entry = p.get('side'), p.get('avg_entry')
        sl, tp = p.get('stop_loss'), p.get('take_profit_1')
        be = p.get('stop_moved_to_breakeven')
        long = side == 'LONG'
        if _finite(sl) and _finite(entry):
            if be:
                # Post-breakeven the stop must sit at entry or better.
                ok = sl >= entry if long else sl <= entry
                if not ok:
                    wrong_side.append(f"{code} {side} stop {sl} worse than breakeven {entry}")
            else:
                # Pre-breakeven the stop must sit on the losing side of entry.
                ok = sl < entry if long else sl > entry
                if not ok:
                    wrong_side.append(f"{code} {side} entry={entry} stop={sl} on the wrong side")
        if p.get('trade_type') == 'INTRADAY_4H' and be:
            phantom_trail.append(f"{code} claims breakeven without a partial exit")
        if _finite(tp) and _finite(entry):
            ok_tp = tp > entry if long else tp < entry
            if not ok_tp:
                wrong_side.append(f"{code} {side} entry={entry} target={tp} on the wrong side")
    a.check('LOGIC', 'every open stop and target is on the correct side of entry',
            not wrong_side, ok_detail=f"{len(pa.get('positions') or {})} positions verified",
            bad_detail='; '.join(wrong_side))
    a.check('LOGIC', 'no intraday position claims a breakeven it never earned',
            not phantom_trail, bad_detail='; '.join(phantom_trail))

    zero_risk = [c for c, p in (pa.get('positions') or {}).items()
                 if _finite(p.get('stop_loss')) and p.get('stop_loss') == p.get('avg_entry')
                 and not p.get('stop_moved_to_breakeven')]
    a.check('LOGIC', 'no position opened with a zero-distance stop', not zero_risk,
            bad_detail=f"stop equals entry on: {', '.join(zero_risk)}")

    # Risk per trade must respect the configured cap.
    equity = (pa.get('stats') or {}).get('equity')
    if _finite(equity) and equity > 0:
        cap = cmi.RISK_PARAMS['max_risk_per_trade']
        over = []
        for code, p in (pa.get('positions') or {}).items():
            if not (_finite(p.get('qty')) and _finite(p.get('avg_entry')) and _finite(p.get('stop_loss'))):
                continue
            if p.get('stop_moved_to_breakeven'):
                continue  # risk already removed from the table
            risk = abs(p['avg_entry'] - p['stop_loss']) * p['qty']
            if risk > equity * cap * 1.5:  # 50% tolerance for drift since entry
                over.append(f"{code} risking ${risk:,.0f} ({risk / equity * 100:.1f}% of equity)")
        a.check('LOGIC', f"no position risks more than {cap:.0%} of equity", not over,
                ok_detail='all open risk within the configured cap',
                bad_detail='; '.join(over), warn_only=True)


def check_consistency(a, mi, pa, sh, cmi):
    """The same fact must agree everywhere, and must survive recomputation."""
    stats = pa.get('stats') or {}
    closed = pa.get('closed_trades') or []

    recomputed = cmi.compute_paper_account_stats(pa, equity=stats.get('equity') or 0)
    for field in ['closed_trades', 'wins', 'losses', 'win_rate', 'excluded_corrupted',
                  'excluded_pre_fix', 'profit_factor']:
        published, actual = stats.get(field), recomputed.get(field)
        a.check('CONSISTENCY', f"published {field} survives recomputation",
                published == actual,
                ok_detail=f"{field}={published}",
                bad_detail=f"dashboard shows {field}={published} but recomputing from the "
                           f"raw ledger gives {actual}")

    w, l, c = stats.get('wins') or 0, stats.get('losses') or 0, stats.get('closed_trades') or 0
    a.check('CONSISTENCY', 'wins + losses equals closed trades', w + l == c,
            ok_detail=f"{w}W + {l}L = {c}",
            bad_detail=f"{w}W + {l}L = {w + l}, but closed_trades says {c}")

    # Equity must equal cash plus what the open positions are actually worth.
    cash = pa.get('cash')
    if _finite(cash):
        marked = cash + sum(p['qty'] * p['avg_entry']
                            for p in (pa.get('positions') or {}).values()
                            if _finite(p.get('qty')) and _finite(p.get('avg_entry')))
        published_eq = stats.get('equity')
        # Published equity marks to CURRENT price, this marks to entry, so they
        # differ by open P&L. A wild gap means the accounting is broken.
        if _finite(published_eq) and marked > 0:
            drift = abs(published_eq - marked) / marked
            a.check('CONSISTENCY', 'equity is explainable by cash plus open positions',
                    drift < 0.25,
                    ok_detail=f"published ${published_eq:,.2f} vs at-entry ${marked:,.2f} "
                              f"({drift * 100:.1f}% open P&L)",
                    bad_detail=f"published equity ${published_eq:,.2f} is {drift * 100:.0f}% away "
                               f"from cash+positions ${marked:,.2f} — accounting drift")

    perf = sh.get('performance') or {}
    dist = perf.get('signal_distribution') or {}
    if dist and perf.get('total_rows_logged'):
        a.check('CONSISTENCY', 'signal distribution sums to the rows logged',
                sum(dist.values()) == perf['total_rows_logged'],
                ok_detail=f"{sum(dist.values())} rows across {len(dist)} buckets",
                bad_detail=f"buckets sum to {sum(dist.values())} but total_rows_logged "
                           f"says {perf['total_rows_logged']}")
        a.check('CONSISTENCY', 'logged signals match the stored signal array',
                len(sh.get('signals') or []) == perf['total_rows_logged'],
                bad_detail=f"{len(sh.get('signals') or [])} rows stored vs "
                           f"{perf['total_rows_logged']} claimed")

    # Every configured asset must actually be present in the output.
    configured, produced = set(cmi.ASSETS.keys()), set((mi.get('assets') or {}).keys())
    a.check('CONSISTENCY', 'every configured asset produced output',
            configured == produced,
            ok_detail=f"{len(produced)} assets: {', '.join(sorted(produced))}",
            bad_detail=f"missing: {sorted(configured - produced)} | unexpected: {sorted(produced - configured)}")

    # The embedded copy of the paper account vs the standalone file. These
    # legitimately drift: the position monitor updates paper_account.json every
    # 10-15 min, while market_intelligence.json only regenerates every 2h. The
    # dashboard fetches paper_account.json directly and prefers it, so the drift
    # is invisible in normal operation — it only surfaces if that fetch fails.
    # Worth knowing about, not a broken state, hence a warning.
    embedded = (mi.get('paper_account') or {}).get('stats') or {}
    if embedded:
        mismatched = [k for k in ['equity', 'closed_trades', 'win_rate']
                      if k in embedded and embedded.get(k) != stats.get(k)]
        a.check('CONSISTENCY', 'dashboard fallback snapshot is in step with the ledger',
                not mismatched,
                ok_detail='embedded snapshot agrees with paper_account.json',
                bad_detail=f"embedded snapshot is behind on {', '.join(mismatched)} "
                           f"(expected between runs; dashboard prefers the live file)",
                warn_only=True)


def check_wiring(a, mi, html):
    """Every value the dashboard reads must exist in the data it reads from."""
    if not html:
        a.add('WIRING', 'index.html readable', FAIL, 'could not read index.html')
        return

    top_level = set(re.findall(r"\bd\.([a-z_][a-z_0-9]*)", html))
    missing = sorted(k for k in top_level if k not in mi)
    a.check('WIRING', 'every top-level field the dashboard reads exists',
            not missing,
            ok_detail=f"{len(top_level)} fields read, all present",
            bad_detail=f"dashboard reads fields that do not exist in the data: {missing}")

    ids_defined = set(re.findall(r"id=[\"']([A-Za-z0-9_\-]+)[\"']", html))
    ids_used = set(re.findall(r"\$\(['\"]([A-Za-z0-9_\-]+)['\"]\)", html)) \
        | set(re.findall(r"getElementById\(['\"]([A-Za-z0-9_\-]+)['\"]\)", html))
    orphans = sorted(ids_used - ids_defined)
    a.check('WIRING', 'every element the script writes to exists in the page',
            not orphans,
            ok_detail=f"{len(ids_used)} element references, all defined",
            bad_detail=f"script writes to non-existent elements (silent blank sections): {orphans}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default='docs', help='directory holding the JSON outputs')
    ap.add_argument('--html', default='index.html', help='dashboard file to check wiring against')
    args = ap.parse_args()

    spec = importlib.util.spec_from_file_location('cmi', 'crypto_market_intelligence_v60.py')
    cmi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cmi)

    docs, raw_text = {}, {}
    for name in os.listdir(args.dir) if os.path.isdir(args.dir) else []:
        path = os.path.join(args.dir, name)
        try:
            with open(path, encoding='utf-8') as fh:
                raw_text[name] = fh.read()
            docs[name] = json.loads(raw_text[name]) if name.endswith('.json') else None
        except Exception as e:
            docs[name] = None
            print(f"  !! could not read {name}: {e}")

    mi = docs.get('market_intelligence.json') or {}
    pa = docs.get('paper_account.json') or {}
    sh = docs.get('signal_history.json') or {}
    v6 = docs.get('v6_results.json') or {}
    html = ''
    try:
        with open(args.html, encoding='utf-8') as fh:
            html = fh.read()
    except Exception:
        pass

    print("=" * 64)
    print(f"SYSTEM INTEGRITY AUDIT · {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"data: {os.path.abspath(args.dir)}")
    print("=" * 64)

    a = Audit()
    check_files(a, docs, raw_text)
    check_freshness(a, mi, pa, sh)
    check_provenance(a, mi, pa, sh, v6)
    check_sanity(a, mi, pa)
    check_logic(a, pa, cmi)
    check_consistency(a, mi, pa, sh, cmi)
    check_wiring(a, mi, html)
    return a.report()


if __name__ == '__main__':
    sys.exit(main())
