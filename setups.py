"""Search for 'solid' daily setups: 10y, realistic fills, 0.15% costs, vs a random-stock baseline.
Run: python3 setups.py"""
from collections import defaultdict
import desk
from desk import ema, atr, pct

COST = 0.15

def rsi2(c):
    """Wilder RSI(2) series."""
    out, g, l = [50.0] * len(c), 0.0, 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        g = (g + max(d, 0)) / 2; l = (l + max(-d, 0)) / 2
        out[i] = 100 - 100 / (1 + g / l) if l else 100
    return out

def trade(b, i, mode, stop_px=None):
    """Enter next open. mode 'ema5': exit first close > EMA5 (max 6d); 'hold5': exit close day 5. Stop 2 ATR unless given."""
    O, L, C, e5 = b["o"], b["l"], b["c"], b["_e5"]
    en = O[i + 1]; st = stop_px if stop_px else en - 2 * atr(b, end=i)
    for j in range(i + 1, i + 7 if mode == "ema5" else i + 6):
        if L[j] <= st: return pct(min(O[j], st) if j > i + 1 else st, en) - COST
        if mode == "ema5" and C[j] > e5[j]: return pct(C[j], en) - COST
    return pct(C[i + 6 if mode == "ema5" else i + 5], en) - COST

if __name__ == "__main__":
    data = desk.fetch_all(desk.universe() + ["SPY"], "10y")
    res, days = defaultdict(list), defaultdict(lambda: defaultdict(int))
    for t, b in data.items():
        if t == "SPY": continue
        O, H, L, C, V, d = b["o"], b["h"], b["l"], b["c"], b["v"], b["d"]
        b["_e5"] = ema(C, 5); e200 = ema(C, 200); r2 = rsi2(C)
        for i in range(260, len(C) - 7):
            if sum(C[j] * V[j] for j in range(i - 19, i + 1)) / 20 < 5e7 or C[i] < 10: continue
            up = C[i] > e200[i]
            ibs = (C[i] - L[i]) / (H[i] - L[i]) if H[i] > L[i] else 0.5
            sma50 = sum(C[i - 49:i + 1]) / 50
            mom = pct(C[i], C[i - 126])
            rvol = V[i] / (sum(V[i - 20:i]) / 20)
            hits = []
            if i % 5 == 0 and up: hits.append(("0 baseline: random uptrend stock, hold 5d", "hold5", None))
            d5 = pct(C[i], C[i - 5])
            if up and -10 < d5 <= -7: hits.append(("A dip 7-10% in 5d (looser dip)", "ema5", None))
            if up and -10 < d5 <= -7 and ibs < 0.3: hits.append(("A2 dip 7-10% + close near low", "ema5", None))
            if mom > 30 and C[i] > sma50 and r2[i] < 10: hits.append(("B leader (6m +30%) RSI2<10 pullback", "ema5", None))
            if mom > 30 and C[i] > sma50 and r2[i] < 5 and ibs < 0.3: hits.append(("B2 leader RSI2<5 + close near low", "ema5", None))
            if up and C[i] < C[i - 1] < C[i - 2] < C[i - 3] and ibs < 0.3: hits.append(("C 3 down days in uptrend, close near low", "ema5", None))
            if O[i] > C[i - 1] * 1.04 and rvol >= 3 and ibs > 0.5: hits.append(("D gap up 4%+ on 3x volume (episodic pivot)", "hold5", L[i]))
            if C[i] >= max(H[i - 252:i]) and rvol >= 2: hits.append(("E 52w-high breakout on 2x volume", "hold5", None))
            for name, mode, sp in hits:
                res[name].append((d[i][:4], trade(b, i, mode, sp)))
                days[name][d[i]] += 1
    nd = len({x for b in data.values() for x in b["d"][260:-7]})
    print(f"{'setup':45} {'n':>6} {'win':>5} {'avg':>7} {'days w/ signal':>15}  losing years")
    for k in sorted(res):
        v = [r for _, r in res[k]]
        yr = defaultdict(list)
        for y, r in res[k]: yr[y].append(r)
        bad = [y for y, x in sorted(yr.items()) if sum(x) / len(x) < 0]
        print(f"{k:45} {len(v):6} {sum(x > 0 for x in v) / len(v) * 100:4.0f}% {sum(v) / len(v):+6.2f}% {len(days[k]) / nd * 100:13.0f}%  {','.join(bad) or '-'}")
