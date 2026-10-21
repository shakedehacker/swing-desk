"""Robustness audit of the dip rule: 10y history, removed S&P members (survivorship), gap-through-stop fills, costs.
Run: python3 audit.py   (first run downloads ~1000 tickers x 10y, a few minutes; cached after)."""
import urllib.request
from collections import defaultdict
import desk
from desk import ema, atr, pct

COST = 0.15  # % per round trip: IBKR commission on a ~$1K position plus half-spread each side

def removed_tickers():
    """{ticker: (start, end)} for S&P 500 members removed since 2016 (github.com/fja05680/sp500). Acquired/bankrupt ones Yahoo no longer serves."""
    url = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv"
    rows = urllib.request.urlopen(url, timeout=30).read().decode().split("\n")[1:]
    return {t.replace(".", "-"): (a, e) for t, a, e in (r.split(",") for r in rows if r.count(",") == 2) if e >= "2016"}

def run(data, realistic, window={}):
    by_day = defaultdict(list)
    for t, b in data.items():
        if t == "SPY": continue
        O, H, L, C, V, d = b["o"], b["h"], b["l"], b["c"], b["v"], b["d"]
        e200, e5 = ema(C, 200), ema(C, 5)
        for i in range(260, len(C) - 7):
            if t in window and not window[t][0] <= d[i] <= window[t][1]: continue
            if C[i] / C[i - 5] - 1 >= -0.10 or C[i] <= e200[i]: continue
            if sum(C[j] * V[j] for j in range(i - 19, i + 1)) / 20 < 5e7: continue
            en, st, out = O[i + 1], O[i + 1] - 2 * atr(b, end=i), None
            for j in range(i + 1, i + 7):
                if L[j] <= st: out = pct(min(O[j], st) if realistic and j > i + 1 else st, en); break
                if C[j] > e5[j]: out = pct(C[j], en); break
            r = pct(C[i + 6], en) if out is None else out
            ibs = (C[i] - L[i]) / (H[i] - L[i]) if H[i] > L[i] else 0.5
            by_day[d[i]].append((r - (COST if realistic else 0), t, ibs))
    return by_day

def rep(k, v):
    if not v: return print(f"  {k:30} n=0")
    w = sum(x > 0 for x in v) / len(v) * 100
    print(f"  {k:30} n={len(v):5}  win {w:3.0f}%  avg {sum(v) / len(v):+.2f}%  conf {max(1, min(9, (w - 45) / 3)):.1f}")

if __name__ == "__main__":
    cur = set(desk.universe())
    win = removed_tickers()
    gone = set(win) - cur
    data = desk.fetch_all(sorted(cur | gone) + ["SPY"], "10y")
    print(f"tickers with 10y data: {len(data) - 1} (current {len(cur & set(data))}, removed-from-S&P {len(gone & set(data))} of {len(gone)})")
    for realistic in (False, True):
        bd = run(data, realistic, {t: win[t] for t in gone})
        allr = [(r, t, ib, len(v)) for v in bd.values() for r, t, ib in v]
        print("\n== " + ("REALISTIC: gap-through-stop at open, -0.15% costs" if realistic else "OLD METHOD: stop fills exact, no costs"))
        rep("all", [r for r, *_ in allr])
        rep("current members only", [r for r, t, *_ in allr if t in cur])
        rep("removed members only", [r for r, t, *_ in allr if t in gone])
        rep("cluster day (10+)", [r for r, _, _, n in allr if n >= 10])
        rep("lone day (1-3)", [r for r, _, _, n in allr if n <= 3])
        rep("IBS<0.3", [r for r, _, ib, _ in allr if ib < 0.3])
        rep("cluster + IBS<0.3", [r for r, _, ib, n in allr if n >= 10 and ib < 0.3])
        if realistic:
            print("  -- by year --")
            for y in sorted({d[:4] for d in bd}):
                rep(y, [r for d, v in bd.items() if d[:4] == y for r, *_ in v])
