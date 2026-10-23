"""Closed replay: run the desk's selection as of past dates with only the data it had then, score against what happened.
Run: python3 replay.py 2026-10-12 2026-10-16   (dates inclusive; stats, signals and watchlist all point-in-time)."""
import sys
import desk, report
from desk import ema, atr, pct

def cut(b, n):
    return {k: v[:n] for k, v in b.items() if isinstance(v, list)}

def day(full, d):
    """Picks the desk would have published the morning after close d."""
    data = {}
    for t, b in full.items():
        if d not in b["d"]: continue
        x = cut(b, b["d"].index(d) + 1)
        x["_e200"], x["_e5"] = ema(x["c"], 200), ema(x["c"], 5)
        data[t] = x
    table = report.edge_table(data)
    last = {t: len(b["c"]) - 1 for t, b in data.items()}
    today = [(t, report.rules(b, last[t])) for t, b in data.items() if t != "SPY"]
    today = [(t, rs) for t, rs in today if rs]
    n_dip = sum("dip" in rs for _, rs in today)
    s5 = report.spy5(data).get(d, 0)
    sig, rejected = [], []
    for t, rs in today:
        b = data[t]
        r, s = report.choose(b, last[t], rs, n_dip, s5, table)
        c, e5, a = b["c"][-1], b["_e5"][-1], atr(b)
        cap = min(e5 / 1.03, c * 1.02)
        why = "bucket below the bar" if not s else "no room to target" if cap <= c * 0.97 else None
        p = dict(t=t, rule=r, tier=report.tier(r, n_dip), buy=cap, tgt=e5, stop=cap - 2 * a, conf=report.confidence(s) if s else None,
                 bucket=report.bucket(r, n_dip, b, last[t], s5), rvol=b["v"][-1] / (sum(b["v"][-21:-1]) / 20))
        if p["conf"] and p["rvol"] >= 3: p["conf"] = max(1.0, p["conf"] - 1)
        (rejected if why else sig).append(dict(p, why=why))
    sig.sort(key=lambda p: (p["tier"] != "golden", -(p["conf"] or 0)))
    sig = [p for p in sig if p["tier"] == "golden"][:report.TOP] + [p for p in sig if p["tier"] == "solid"][:report.SOLID]
    watch = sorted((pct(b["c"][-1], b["c"][-6]), t) for t, b in data.items()
                   if t != "SPY" and report.liquid(b, last[t]) and b["c"][-1] > b["_e200"][-1]
                   and -10 <= pct(b["c"][-1], b["c"][-6]) < -6 and t not in {x for x, _ in today})
    return sig, rejected, [dict(t=t, trig=data[t]["c"][-5] * 0.90, drop=d5) for d5, t in watch[:max(0, 10 - len(sig))]], n_dip

def score(b, d, p):
    """Same rules as report.track: day limit at buy, stop checked before target, exit at close of day 6."""
    i = b["d"].index(d)
    if i + 1 >= len(b["d"]): return "no data yet", None
    if b["l"][i + 1] > p["buy"]: return "not filled (opened and stayed above limit)", None
    en = min(b["o"][i + 1], p["buy"])
    for j in range(i + 1, min(i + 7, len(b["d"]))):
        if b["l"][j] <= p["stop"]: return f"stop day {j - i}", pct(min(b["o"][j], p["stop"]) if j > i + 1 else p["stop"], en) - report.COST
        if b["h"][j] >= p["tgt"]: return f"target day {j - i}", pct(p["tgt"], en) - report.COST
    if i + 6 < len(b["d"]): return "time exit day 6", pct(b["c"][i + 6], en) - report.COST
    return f"still open ({len(b['d']) - 1 - i}d)", pct(b["c"][-1], en) - report.COST

if __name__ == "__main__":
    a, z = sys.argv[1], sys.argv[2]
    full = desk.fetch_all(desk.universe() + ["SPY"], "10y")
    spy = full["SPY"]
    days = [d for d in spy["d"] if a <= d <= z]
    allr = []
    for d in days:
        sig, rej, watch, nd = day(full, d)
        i = spy["d"].index(d)
        print(f"\n=== {d} close -> page next morning | dip signals {nd} | SPY next 5d {pct(spy['c'][min(i + 5, len(spy['c']) - 1)], spy['c'][i]):+.1f}%")
        for p in sig:
            st, r = score(full[p["t"]], d, p); allr.append(r) if r is not None else None
            print(f"  {p['tier'].upper():6} {p['t']:6} {p['rule']:5} conf {p['conf']:.1f} buy<={p['buy']:.2f} tgt {p['tgt']:.2f} stop {p['stop']:.2f} rvol {p['rvol']:.1f} -> {st} {'' if r is None else f'{r:+.2f}%'}")
        for p in rej:
            st, r = score(full[p["t"]], d, p)
            print(f"  skip  {p['t']:6} ({p['why']}) would have: {st} {'' if r is None else f'{r:+.2f}%'}")
        for w in watch:
            b = full[w["t"]]; i2 = b["d"].index(d)
            hit = next((b["d"][j] for j in range(i2 + 1, len(b["d"])) if b["c"][j] <= w["trig"]), None)
            print(f"  watch {w['t']:6} trigger {w['trig']:.2f} -> {'triggered ' + hit if hit else 'not triggered'} (now {pct(b['c'][-1], b['c'][i2]):+.1f}% since)")
    if allr:
        print(f"\nTRADES scored: {len(allr)}  won {sum(r > 0 for r in allr)}  avg {sum(allr) / len(allr):+.2f}% net")
