"""Render index.html in a real headless browser and cross-check what it actually
DISPLAYS against an independent recalculation from the raw data files.

This is a different kind of check from audit_system.py. That script verifies the
DATA is self-consistent and honest. This script verifies the DASHBOARD CODE turns
that data into the correct screen text — it catches a wrong field read, an
inverted sign, a mislabeled unit, or bad arithmetic in the JavaScript itself,
none of which a JSON-only check can see. Together they cover the full chain:
raw data -> Python calculation -> JSON -> JavaScript -> what a visitor reads.

Never touches the live site: index.html is opened from the local disk file, and
every network request it would normally make (the live JSON fetch, CDN scripts,
fonts) is intercepted and answered from local files or blocked. Writes nothing.
Changes nothing. Not part of the dashboard or the pipeline — a report you run
by hand:

    python3 audit_dashboard_render.py

Exit code 1 if anything displayed disagrees with the independently recomputed
truth.
"""
import argparse
import importlib.util
import json
import os
import re
import sys

CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'


def _num(text):
    """Pull the first number out of rendered text, e.g. '43.6% win rate (17W/22L)'
    -> 43.6. Returns None if nothing numeric is present."""
    if text is None:
        return None
    m = re.search(r'-?\d[\d,]*\.?\d*', text)
    if not m:
        return None
    return float(m.group(0).replace(',', ''))


def _all_nums(text):
    return [float(x.replace(',', '')) for x in re.findall(r'-?\d[\d,]*\.?\d*', text or '')]


def render(repo_dir, returning_visitor=True):
    """returning_visitor=True simulates the far more common real-world case: a
    visitor whose browser already has localStorage set from an earlier visit.
    That path silently never called loadData() until this audit caught it — so
    it is the default here, not the freshly-arrived first-time path."""
    from playwright.sync_api import sync_playwright

    repo_dir = os.path.abspath(repo_dir)
    docs = os.path.join(repo_dir, 'docs')
    html_path = os.path.join(repo_dir, 'index.html')
    local_json = {}
    for name in os.listdir(docs):
        if name.endswith('.json'):
            with open(os.path.join(docs, name), 'rb') as fh:
                local_json[name] = fh.read()

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True,
                                    args=['--no-sandbox'])
        page = browser.new_page()
        # This harness itself blocks CDN/font requests (see handle_route below) so
        # the audit never depends on outbound network access. Those deliberate
        # aborts produce a "Failed to load resource" console line each — an
        # artifact of how the check is run, not something a real visitor's
        # browser would see (theirs reaches jsdelivr/fonts normally). Matched by
        # the actual failed request's URL, not by message text, so an unrelated
        # real failure (e.g. the data fetch itself breaking) still surfaces.
        blocked_hosts = ('cdn.jsdelivr.net', 'fonts.googleapis.com', 'fonts.gstatic.com')
        console_errors = []
        self_inflicted_failures = set()
        page.on('requestfailed', lambda req: self_inflicted_failures.add(req.url)
                if any(h in req.url for h in blocked_hosts) else None)
        page.on('console', lambda msg: console_errors.append(msg.text)
                if msg.type == 'error' else None)
        page.on('pageerror', lambda exc: console_errors.append(str(exc)))

        def handle_route(route):
            url = route.request.url
            for name, content in local_json.items():
                if name in url:
                    route.fulfill(status=200, content_type='application/json', body=content)
                    return
            if any(h in url for h in blocked_hosts):
                route.abort()
                return
            if url.startswith('file://'):
                route.continue_()
                return
            # Anything else (unexpected external request) — don't hang on it.
            route.abort()

        page.route('**/*', handle_route)
        if returning_visitor:
            # Set BEFORE any page script runs, exactly like a real browser that
            # already has this from a prior visit — this is the path that
            # silently skipped loadData() entirely.
            page.add_init_script(
                "try { localStorage.setItem('market-cortex-understood', 'true'); } catch(e) {}")
        page.goto(f'file://{html_path}')
        if not returning_visitor:
            # First-time visitor: click through the disclaimer, same as a person would.
            page.locator('#understand-check').check()
            page.locator('#enter-btn').click()
        page.wait_for_timeout(2500)  # let async fetch + render handlers finish

        def text(el_id):
            try:
                loc = page.locator(f'#{el_id}')
                return loc.first.inner_text(timeout=1000) if loc.count() else None
            except Exception:
                return None

        ids = ['pa-equity', 'pa-return', 'pa-closed', 'pa-winrate', 'pa-open', 'pa-dca',
               'pa-pf', 'pa-avg', 'pa-by-type', 'risk-grade', 'risk-detail']
        rendered = {i: text(i) for i in ids}
        browser.close()
        # Real errors are whatever's left after accounting for the self-inflicted
        # CDN/font blocks — a generic browser console message doesn't carry the
        # URL, so this is a count reconciliation rather than a per-message match.
        real_error_count = max(0, len(console_errors) - len(self_inflicted_failures))
        return rendered, console_errors, real_error_count


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default='.', help='repo root containing index.html and docs/')
    args = ap.parse_args()

    spec = importlib.util.spec_from_file_location('cmi', os.path.join(args.dir, 'crypto_market_intelligence_v60.py'))
    cmi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cmi)

    pa = json.load(open(os.path.join(args.dir, 'docs', 'paper_account.json')))
    mi = json.load(open(os.path.join(args.dir, 'docs', 'market_intelligence.json')))

    # The one number that matters most: recompute straight from the raw ledger,
    # not from the stats block already in the JSON (comparing to that would be
    # circular — if compute_paper_account_stats had a bug, the JSON and the
    # dashboard could agree with each other and both be wrong).
    truth = cmi.compute_paper_account_stats(pa, equity=(pa.get('stats') or {}).get('equity') or pa['cash'])
    risk = (mi.get('risk_metrics') or {})

    print("=" * 64)
    print("DASHBOARD RENDER AUDIT — real browser vs. independent recalculation")
    print(f"data: {os.path.abspath(args.dir)}")
    print("=" * 64)

    rendered, console_errors, real_error_count = render(args.dir, returning_visitor=True)

    print("\n─── JS console/page errors during render ───────────────")
    if console_errors:
        for e in console_errors[:10]:
            print(f"  !! {e}")
        print(f"  ({len(console_errors) - real_error_count} of these are this harness's own "
              f"deliberate CDN/font blocks, not a page problem)")
    else:
        print("  none")
    if real_error_count:
        print(f"  {real_error_count} UNEXPLAINED error(s) — investigate before trusting this render")

    checks = []

    def check(name, ok, detail):
        checks.append((name, ok, detail))

    # --- paper account card ---
    eq_shown = _num(rendered.get('pa-equity'))
    check('Equity shown matches recomputed truth',
          eq_shown is not None and abs(eq_shown - truth['equity']) < 1.0,
          f"dashboard shows {rendered.get('pa-equity')!r} (parsed ${eq_shown}), "
          f"recomputed truth is ${truth['equity']}")

    closed_shown = _num(rendered.get('pa-closed'))
    check('Closed trade count shown matches recomputed truth',
          closed_shown == truth['closed_trades'],
          f"dashboard shows {rendered.get('pa-closed')!r}, truth is {truth['closed_trades']}")

    wr_text = rendered.get('pa-winrate') or ''
    wr_shown = _num(wr_text)
    if truth['win_rate'] is None:
        # No closed trades: the honest render is text with no number in it, same
        # as win_rate_basis says. A parsed None here is the correct outcome, not
        # a missing value.
        check('Win rate shown correctly says no closed trades yet (no invented number)',
              wr_shown is None,
              f"dashboard shows {wr_text!r}" if wr_shown is None else
              f"expected no numeric win rate with 0 closed trades, but dashboard "
              f"shows a number: {wr_text!r}")
    else:
        check('Win rate shown matches recomputed truth (not just the stored field)',
              wr_shown is not None and abs(wr_shown - truth['win_rate']) < 0.15,
              f"dashboard shows {wr_text!r} (parsed {wr_shown}%), "
              f"recomputed win_rate is {truth['win_rate']}%")

    wl = re.search(r'\((\d+)W/(\d+)L\)', wr_text)
    if wl:
        w_shown, l_shown = int(wl.group(1)), int(wl.group(2))
        check('W/L counts shown match recomputed truth',
              w_shown == truth['wins'] and l_shown == truth['losses'],
              f"dashboard shows {w_shown}W/{l_shown}L, truth is {truth['wins']}W/{truth['losses']}L")
    elif truth['closed_trades'] > 0:
        check('W/L counts present when there are closed trades', False,
              f"win-rate text {wr_text!r} has no W/L breakdown but {truth['closed_trades']} trades are closed")

    pf_shown = _num(rendered.get('pa-pf'))
    if truth['profit_factor'] is not None:
        check('Profit factor shown matches recomputed truth',
              pf_shown is not None and abs(pf_shown - truth['profit_factor']) < 0.02,
              f"dashboard shows {rendered.get('pa-pf')!r}, truth is {truth['profit_factor']}")

    ret_shown = _num(rendered.get('pa-return'))
    check('Return % shown matches recomputed truth',
          ret_shown is not None and abs(ret_shown - truth['total_return_pct']) < 0.1,
          f"dashboard shows {rendered.get('pa-return')!r}, truth is {truth['total_return_pct']}%")

    open_shown = _num(rendered.get('pa-open'))
    check('Open position count shown matches the ledger',
          open_shown == len(pa.get('positions') or {}),
          f"dashboard shows {rendered.get('pa-open')!r}, ledger has {len(pa.get('positions') or {})} open")

    # --- by-type breakdown: cross-check against the SWING/INTRADAY split in truth ---
    bt_text = rendered.get('pa-by-type') or ''
    for label, key in [('Intraday', 'INTRADAY_4H'), ('Swing', 'SWING_DAILY')]:
        expected = truth['by_type'][key]
        m = re.search(rf'{label}.*?open\s*(\d+).*?closed\s*(\d+)', bt_text, re.S)
        if m:
            open_shown_t, closed_shown_t = int(m.group(1)), int(m.group(2))
            check(f'{label} open/closed counts shown match recomputed truth',
                  open_shown_t == expected['open'] and closed_shown_t == expected['closed'],
                  f"dashboard shows open={open_shown_t} closed={closed_shown_t}, "
                  f"truth is open={expected['open']} closed={expected['closed']}")
        elif expected['closed'] or expected['open']:
            check(f'{label} row present in by-type breakdown', False,
                  f"expected open={expected['open']} closed={expected['closed']} but no matching row rendered")

    # --- risk grade: rendered value must be one JSON actually produced ---
    check('Risk grade shown is the value the pipeline produced',
          (rendered.get('risk-grade') or '').strip() == str(risk.get('risk_grade', '')).strip(),
          f"dashboard shows {rendered.get('risk-grade')!r}, data has {risk.get('risk_grade')!r}")

    print("\n─── cross-checks: rendered DOM vs. independent recalculation ───")
    n_fail = 0
    for name, ok, detail in checks:
        print(f"[{'  ok  ' if ok else ' FAIL '}] {name}")
        print(f"              {detail}")
        if not ok:
            n_fail += 1

    print("\n" + "=" * 64)
    print(f"{len(checks) - n_fail}/{len(checks)} render checks passed"
          + (f" · {n_fail} FAILURE(S)" if n_fail else ''))
    if real_error_count:
        print(f"{real_error_count} unexplained JS console error(s) during render — see above")
    print("=" * 64)
    return 1 if (n_fail or real_error_count) else 0


if __name__ == '__main__':
    sys.exit(main())
