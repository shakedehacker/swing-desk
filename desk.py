#!/usr/bin/env python3
"""Weekly swing-trading desk. Stdlib only (chart needs matplotlib).

  python3 desk.py brief                 premarket brief: macro, sectors, positions, earnings
  python3 desk.py scan                  dip-bounce signals for the next open, with go/skip verdicts
  python3 desk.py hold HOOD 70          hold/sell check for a position (ticker, cost)
  python3 desk.py backtest              re-verify the dip-bounce edge on 2y of data
  python3 desk.py chart PANW CRWD       candles + EMAs to charts.png (needs .venv with matplotlib)

The edge (backtested 2y, S&P 500+400): stock above its 200-day EMA drops >10% in 5 days.
Buy next open, exit first close above EMA5 or after 6 days, stop 2 ATR. 66% win, +1.6% avg.
Strongest on cluster days (10+ signals). Dies if entry is 2+ days late, the open gaps up >2%,
or the open is already above EMA5. Never hold through earnings.
"""
import json, sys, os, time, datetime as dt, urllib.request, http.cookiejar, concurrent.futures as cf, re

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
UA = {"User-Agent": "Mozilla/5.0"}
os.makedirs(CACHE, exist_ok=True)

# ---------- data ----------
def _get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20))

def bars(t, rng="2y"):
    """Daily bars, cached per ticker per day. Returns dict of lists: d,o,h,l,c,v."""
    path = os.path.join(CACHE, f"{t}_{rng}.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 1800:
        return json.load(open(path))
    for _ in range(3):
        try:
            r = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range={rng}&interval=1d")["chart"]["result"][0]
            q = r["indicators"]["quote"][0]
            rows = [(dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d"), o, h, l, c, v)
                    for ts, o, h, l, c, v in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"], q["volume"])
                    if None not in (o, h, l, c, v)]
            b = dict(zip("dohlcv", map(list, zip(*rows))))
            json.dump(b, open(path, "w"))
            return b
        except Exception:
            time.sleep(1)
    return None

_opener = None
def quotes(syms):
    """Live quote fields (price, pre/post market, earnings timestamp) via the crumb-protected v7 endpoint."""
    global _opener, _crumb
    if _opener is None:
        _opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        _opener.addheaders = list(UA.items())
        try: _opener.open("https://fc.yahoo.com", timeout=10)
        except Exception: pass
        _crumb = _opener.open("https://query1.finance.yahoo.com/v1/test/getcrumb").read().decode()
    out = {}
    for i in range(0, len(syms), 40):
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={','.join(syms[i:i+40])}&crumb={_crumb}"
        for q in json.load(_opener.open(url))["quoteResponse"]["result"]:
            out[q["symbol"]] = q
    return out

def _get_auth(url):
    """GET a crumb-protected Yahoo endpoint (quoteSummary, options) with the session quotes() set up."""
    if _opener is None: quotes(["SPY"])
    sep = "&" if "?" in url else "?"
    return json.load(_opener.open(f"{url}{sep}crumb={_crumb}", timeout=20))

def universe():
    path = os.path.join(HERE, "universe.txt")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
        return open(path).read().split()
    out = set()
    for u in ["https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
              "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"]:
        h = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read().decode()
        for row in h.split('id="constituents"')[1].split("</table>")[0].split("<tr")[2:]:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if cells:
                out.add(re.sub("<.*?>", "", cells[0]).strip().replace(".", "-"))
    open(path, "w").write("\n".join(sorted(out)))
    return sorted(out)

def fetch_all(syms, rng="2y"):
    with cf.ThreadPoolExecutor(16) as ex:
        return {t: b for t, b in zip(syms, ex.map(lambda s: bars(s, rng), syms)) if b and len(b["c"]) > 210}

# ---------- indicators ----------
def ema(x, n):
    k, e, o = 2 / (n + 1), x[0], []
    for v in x:
        e = v * k + e * (1 - k); o.append(e)
    return o

def sma(x, n): return sum(x[-n:]) / n

def atr(b, n=14, end=None):
    end = len(b["c"]) if end is None else end + 1
    h, l, c = b["h"], b["l"], b["c"]
    tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(end - n, end)]
    return sum(tr) / n

def rsi(c, n=14):
    g = l = 0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]; g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]; g = (g * (n - 1) + max(d, 0)) / n; l = (l * (n - 1) + max(-d, 0)) / n
    return 100 - 100 / (1 + g / l) if l else 100

def signal(b, i):
    """Dip-bounce signal at bar i: >10% drop in 5 days, still above EMA200, $50M+ daily volume."""
    c, v = b["c"], b["v"]
    if sum(c[j] * v[j] for j in range(i - 19, i + 1)) / 20 < 5e7: return False
    return c[i] / c[i - 5] - 1 < -0.10 and c[i] > b["_e200"][i]

def trading_days_until(ts):
    d, n, today = dt.date.fromtimestamp(ts), 0, dt.date.today()
    x = today
    while x < d:
        x += dt.timedelta(1)
        if x.weekday() < 5: n += 1
    return n

def pct(a, b): return (a / b - 1) * 100

# ---------- commands ----------
def cmd_scan():
    data = fetch_all(universe())
    for b in data.values(): b["_e200"], b["_e5"] = ema(b["c"], 200), ema(b["c"], 5)
    sig = [t for t, b in data.items() if signal(b, len(b["c"]) - 1)]
    last = max(b["d"][-1] for b in data.values())
    print(f"Signals at {last} close: {len(sig)}  " + ("CLUSTER DAY (strongest edge)" if len(sig) >= 10 else
          "(1-3 signals historically had NO edge)" if len(sig) <= 3 else ""))
    if not sig: return
    q = quotes(sig)
    rows = []
    for t in sig:
        b, z = data[t], q.get(t, {})
        c, e5, a = b["c"][-1], b["_e5"][-1], atr(b)
        live = z.get("preMarketPrice") if z.get("marketState") in ("PRE", "PREPRE") else z.get("regularMarketPrice")
        live = live or c
        gap, room = pct(live, c), pct(e5, live)
        ets = z.get("earningsTimestamp")
        edays = trading_days_until(ets) if ets and ets > time.time() else None
        why = []
        rng = b["h"][-1] - b["l"][-1]
        if rng and (c - b["l"][-1]) / rng >= 0.3: why.append("closed off the low (IBS>=0.3, half the edge)")
        if edays is not None and edays <= 7: why.append(f"earnings in {edays}d")
        if gap > 2: why.append(f"gap {gap:+.1f}%")
        if room < 3: why.append(f"only {room:.1f}% to EMA5")
        rows.append((not why, t, c, live, gap, room, e5 / 1.03, e5, live - 2 * a, edays, ", ".join(why)))
    rows.sort(key=lambda r: (not r[0], -r[5]))
    print(f"{'':4}{'tick':6}{'close':>9}{'live':>9}{'gap%':>7}{'room%':>7}{'buy<=':>9}{'target':>9}{'stop':>9}  earn  why")
    for ok, t, c, live, gap, room, cap, tgt, st, ed, why in rows:
        print(f"{'GO ' if ok else 'no ':4}{t:6}{c:9.2f}{live:9.2f}{gap:7.1f}{room:7.1f}{cap:9.2f}{tgt:9.2f}{st:9.2f}  {ed if ed is not None else '-':>4}  {why}")
    print("\nRules: limit buy at buy<= price at the open, bracket target/stop GTC, exit by day 6.\n"
          "Target = today's EMA5; it drifts lower daily. Signals are one correlated bet: <=1-2% total account risk.")

def cmd_brief():
    macro = {"ES=F": "S&P fut", "NQ=F": "Nasdaq fut", "^VIX": "VIX", "^TNX": "10Y yield", "BZ=F": "Brent",
             "GC=F": "Gold", "BTC-USD": "Bitcoin", "DX-Y.NYB": "Dollar idx"}
    sectors = "XLK SMH IGV XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC".split()
    pos = json.load(open(os.path.join(HERE, "positions.json"))) if os.path.exists(os.path.join(HERE, "positions.json")) else {}
    q = quotes(list(macro) + ["SPY", "QQQ", "IWM"] + list(pos))
    print(f"== Premarket brief {dt.datetime.now():%Y-%m-%d %H:%M}\n-- Macro")
    for s, name in macro.items():
        z = q.get(s, {})
        print(f"  {name:12}{z.get('regularMarketPrice', 0):>11.2f}  {z.get('regularMarketChangePercent', 0):+6.2f}%")
    print("-- Index trend (close vs EMA20/50/200)")
    for s in ("SPY", "QQQ", "IWM"):
        b = bars(s); c = b["c"]
        z = q.get(s, {}); pre = z.get("preMarketPrice")
        flags = " ".join(f"{'>' if c[-1] > ema(c, n)[-1] else '<'}{n}" for n in (20, 50, 200))
        print(f"  {s:5} {c[-1]:8.2f} {flags}  RSI {rsi(c):4.0f}" + (f"  pre {pct(pre, c[-1]):+.2f}%" if pre else ""))
    print("-- Sectors (1d / 5d / 20d %)  money flows to the top")
    rows = []
    for s, b in fetch_all(sectors, "1y").items():
        c = b["c"]; rows.append((pct(c[-1], c[-6]), s, pct(c[-1], c[-2]), pct(c[-1], c[-21])))
    for r5, s, r1, r20 in sorted(rows, reverse=True):
        print(f"  {s:5}{r1:+7.2f}{r5:+7.2f}{r20:+7.2f}")
    if pos:
        print("-- Positions")
        for t, p in pos.items():
            z = q.get(t, {}); px = z.get("preMarketPrice") or z.get("regularMarketPrice", 0)
            ets = z.get("earningsTimestamp"); ed = trading_days_until(ets) if ets and ets > time.time() else None
            warn = "  EARNINGS THIS WEEK" if ed is not None and ed <= 5 else ""
            print(f"  {t:6}{px:9.2f}  cost {p['cost']}  {pct(px, p['cost']):+.1f}%" + (f"  earnings in {ed}d" if ed is not None else "") + warn)
    print("\nNext: python3 desk.py scan   |  news: finviz.com/news.ashx, earningswhispers.com, investing.com/economic-calendar")

def cmd_hold(t, cost):
    b, z = bars(t), quotes([t])[t]
    c, h, l = b["c"], b["h"], b["l"]
    px = z.get("preMarketPrice") or z.get("regularMarketPrice") or c[-1]
    a = atr(b)
    e20, e50, s150, s200 = ema(c, 20)[-1], ema(c, 50)[-1], sma(c, 150), sma(c, 200)
    lows = sorted({round(l[i], 2) for i in range(len(c) - 120, len(c) - 3) if l[i] == min(l[i - 5:i + 6]) and l[i] < px})[-3:]
    chand = max(h[-22:]) - 3 * a
    ets = z.get("earningsTimestamp"); ed = trading_days_until(ets) if ets and ets > time.time() else None
    gaps = sorted((abs(pct(b["o"][i], c[i - 1])) for i in range(len(c) - 500, len(c))), reverse=True)[:8]
    print(f"== {t}  price {px:.2f}  cost {cost}  P/L {pct(px, cost):+.1f}%  ATR {a:.2f} ({a / px * 100:.1f}%)  RSI {rsi(c):.0f}")
    print(f"  EMA20 {e20:.2f}  EMA50 {e50:.2f}  SMA150 {s150:.2f}  SMA200 {s200:.2f}  52w {min(l[-252:]):.2f}-{max(h[-252:]):.2f}")
    print(f"  swing-low supports below: {lows}   chandelier stop (22d high - 3ATR): " +
          (f"{chand:.2f}" if chand < px else f"{chand:.2f} = already below it, trend is correcting; use supports instead"))
    print(f"  next earnings: {'in %d trading days' % ed if ed is not None else 'unknown'}   typical big gap: {sum(gaps) / len(gaps):.1f}%")
    v = []
    if px < s200: v.append("below 200-day: long-term trend broken -> exit")
    if ed is not None and ed <= 7: v.append(f"earnings in {ed}d with ~{sum(gaps) / len(gaps):.0f}% gap risk -> trim before (sell 1/3-1/2) or accept the gamble")
    if px < e50 and px > s200: v.append("below EMA50 but above 200-day: correction inside uptrend -> hold core, stop under supports/200-day")
    if px > e20 > e50: v.append("uptrend intact -> hold, trail stop at chandelier")
    print("  read: " + ("\n        ".join(v) or "neutral"))

def cmd_backtest():
    data = fetch_all(universe() + ["SPY"])
    trades = {}
    for t, b in data.items():
        if t == "SPY": continue
        b["_e200"], e5 = ema(b["c"], 200), ema(b["c"], 5)
        O, L, C, n = b["o"], b["l"], b["c"], len(b["c"])
        for i in range(260, n - 7):
            if not signal(b, i): continue
            en, st = O[i + 1], O[i + 1] - 2 * atr(b, end=i)
            out = None
            for j in range(i + 1, i + 7):
                if L[j] <= st: out = pct(st, en); break
                if C[j] > e5[j]: out = pct(C[j], en); break
            trades.setdefault(b["d"][i], []).append(pct(C[i + 6], en) if out is None else out)
    def rep(k, v): print(f"  {k:24} n={len(v):5} win%={sum(x > 0 for x in v) / len(v) * 100:3.0f} avg%={sum(v) / len(v):+.2f}")
    allv = [x for v in trades.values() for x in v]
    rep("all signals", allv)
    rep("cluster days (10+)", [x for v in trades.values() if len(v) >= 10 for x in v])
    rep("lone days (1-3)", [x for v in trades.values() if len(v) <= 3 for x in v])
    days = [sum(v) / len(v) for v in trades.values() if len(v) >= 10]
    if days: print(f"  cluster days green: {sum(d > 0 for d in days)}/{len(days)}")
    # ponytail: survivorship bias (today's index members only), no slippage/commissions, no earnings filter in history.

def cmd_chart(ticks):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, axs = plt.subplots(len(ticks), 1, figsize=(12, 4.5 * len(ticks)), squeeze=False)
    for ax, t in zip(axs.flat, ticks):
        b = bars(t, "1y"); n, s = len(b["c"]), len(b["c"]) - 120
        for i in range(120):
            j = s + i; col = "#1a9850" if b["c"][j] >= b["o"][j] else "#d73027"
            ax.vlines(i, b["l"][j], b["h"][j], color=col, lw=.8)
            ax.vlines(i, min(b["o"][j], b["c"][j]), max(b["o"][j], b["c"][j]), color=col, lw=3)
        for p, col in ((5, "orange"), (20, "blue"), (50, "purple"), (200, "black")):
            ax.plot(range(120), ema(b["c"], p)[s:], color=col, lw=1, label=f"EMA{p}")
        ax2 = ax.twinx(); ax2.bar(range(120), b["v"][s:], color="gray", alpha=.25); ax2.set_ylim(0, max(b["v"][s:]) * 4); ax2.set_yticks([])
        ax.set_title(f"{t} {b['c'][-1]:.2f}"); ax.legend(loc="upper left", fontsize=7); ax.grid(alpha=.2)
    plt.tight_layout(); out = os.path.join(HERE, "charts.png"); plt.savefig(out, dpi=70); print(out)

if __name__ == "__main__":
    a = sys.argv[1:] or ["brief"]
    {"brief": lambda: cmd_brief(), "scan": lambda: cmd_scan(), "backtest": lambda: cmd_backtest(),
     "hold": lambda: cmd_hold(a[1].upper(), float(a[2])), "chart": lambda: cmd_chart([x.upper() for x in a[1:]])}[a[0]]()
