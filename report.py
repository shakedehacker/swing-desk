#!/usr/bin/env python3
"""Daily desk report -> site/index.html. Run once a day before 9:00 Israel time.

Picks come only from rules that backtested with an edge; confidence is that bucket's own
backtest win rate (net of costs, gap-through-stop fills), recomputed every run on 10y of data. Slots the edge can't fill become a
labelled watchlist, never fake picks.
"""
import os, sys, json, time, html, re, datetime as dt, urllib.request, xml.etree.ElementTree as ET
from collections import defaultdict
from zoneinfo import ZoneInfo
import desk, ai
from desk import ema, atr, pct, quotes, trading_days_until

OUT = os.path.join(desk.HERE, "site", "index.html")
TOP = 10
COST = 0.15  # % per round trip (commission + spread), so confidence reflects net results

def ibs(b, i):
    r = b["h"][i] - b["l"][i]
    return (b["c"][i] - b["l"][i]) / r if r else 0.5

def liquid(b, i):
    return sum(b["c"][j] * b["v"][j] for j in range(i - 19, i + 1)) / 20 >= 5e7 and b["c"][i] >= 10

def rule(b, i):
    """'dip' = >10% 5-day drop, 'rev3' = >7% 3-day drop (Lehmann); both above EMA200."""
    c = b["c"]
    if not liquid(b, i) or c[i] <= b["_e200"][i]: return None
    if c[i] / c[i - 5] - 1 < -0.10: return "dip"
    if c[i] / c[i - 3] - 1 < -0.07: return "rev3"
    return None

def cluster(n): return "cluster" if n >= 10 else "mid" if n >= 4 else "lone"

def bucket(r, n_dip, x): return (r, cluster(n_dip), "low" if x < 0.3 else "high")

def edge_table(data):
    """Backtest every signal in the sample; stats per (rule, cluster, IBS) bucket.
    Realistic: a gap through the stop fills at the open, and every trade pays COST."""
    sigs = defaultdict(list)
    for t, b in data.items():
        if t == "SPY": continue
        for i in range(260, len(b["c"]) - 7):
            r = rule(b, i)
            if r: sigs[b["d"][i]].append((t, i, r))
    stats = defaultdict(list)
    for lst in sigs.values():
        nd = sum(r == "dip" for *_, r in lst)
        for t, i, r in lst:
            b = data[t]; O, L, C, e5 = b["o"], b["l"], b["c"], b["_e5"]
            en, st, out, held = O[i + 1], O[i + 1] - 2 * atr(b, end=i), None, 6
            for j in range(i + 1, i + 7):
                if L[j] <= st: out, held = pct(min(O[j], st) if j > i + 1 else st, en), j - i; break
                if C[j] > e5[j]: out, held = pct(C[j], en), j - i; break
            stats[bucket(r, nd, ibs(b, i))].append(((pct(C[i + 6], en) if out is None else out) - COST, held))
    table = {}
    for k, v in stats.items():
        rets = [x for x, _ in v]; days = sorted(d for _, d in v)
        table[k] = dict(n=len(v), win=sum(x > 0 for x in rets) / len(v) * 100, avg=sum(rets) / len(v), days=days[len(days) // 2])
    return table

def confidence(s):
    # ponytail: linear map of backtest win% (55%->3.3, 66%->7, 70%->8.3), minus 1 when avg return is thin.
    return max(1.0, min(9.0, (s["win"] - 45) / 3 - (1 if s["avg"] < 0.5 else 0)))

def feed(url, n):
    try:
        root = ET.fromstring(urllib.request.urlopen(urllib.request.Request(url, headers=desk.UA), timeout=15).read())
        out = []
        for it in root.iter("item"):
            title, link = it.findtext("title") or "", it.findtext("link") or ""
            src = it.findtext("source") or {"www.cnbc.com": "CNBC", "feeds.content.dowjones.io": "MarketWatch"}.get(url.split("/")[2], url.split("/")[2])
            out.append((title.strip(), link.strip(), src, it.findtext("pubDate") or ""))
        return out[:n]
    except Exception:
        return []

def headlines():
    seen, out = set(), []
    for url, n in [("https://www.cnbc.com/id/100003114/device/rss/rss.html", 8),
                   ("https://feeds.content.dowjones.io/public/rss/mw_topstories", 6),
                   ("https://news.google.com/rss/search?q=stock+market+when:1d&hl=en-US&gl=US&ceid=US:en", 8),
                   ("https://news.google.com/rss/search?q=world+news+economy+when:1d&hl=en-US&gl=US&ceid=US:en", 6)]:
        for h in feed(url, n):
            key = re.sub(r"\W", "", h[0].lower())[:50]
            if key not in seen: seen.add(key); out.append(h)
    return out[:20]

def econ_calendar():
    try:
        ev = json.load(urllib.request.urlopen(urllib.request.Request("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=desk.UA), timeout=15))
    except Exception:
        return []
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for e in ev:
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"): continue
        when = dt.datetime.fromisoformat(e["date"])
        if when < now - dt.timedelta(hours=2): continue
        rows.append((when, e["title"], e["impact"], e.get("forecast", ""), e.get("previous", "")))
    return sorted(rows)[:12]

def earnings_week(universe):
    """Next 5 weekdays of earnings for index members, biggest companies first."""
    out, d = [], dt.date.today()
    while len(out) < 5:
        d += dt.timedelta(1)
        if d.weekday() >= 5: continue
        try:
            rows = json.load(urllib.request.urlopen(urllib.request.Request(
                f"https://api.nasdaq.com/api/calendar/earnings?date={d}", headers=desk.UA), timeout=15))["data"]["rows"] or []
        except Exception:
            rows = []
        def cap(r):
            try: return float(re.sub(r"[^\d.]", "", r.get("marketCap") or "0") or 0)
            except ValueError: return 0
        names = [r["symbol"] for r in sorted(rows, key=cap, reverse=True) if r["symbol"] in universe][:12]
        out.append((d, names))
    return out

def desk_data(t, c, hold):
    """Terminal-style panel from Yahoo (free): short interest, analyst moves in the last 10 days, options-implied move to the exit."""
    out = {}
    try:
        z = desk._get_auth(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{t}?modules=defaultKeyStatistics,upgradeDowngradeHistory,recommendationTrend")["quoteSummary"]["result"][0]
        k = z.get("defaultKeyStatistics", {})
        out["short"] = (k.get("shortPercentOfFloat") or {}).get("raw")
        out["dtc"] = (k.get("shortRatio") or {}).get("raw")
        rt = (z.get("recommendationTrend", {}).get("trend") or [{}])[0]
        out["ratings"] = [rt.get("strongBuy", 0) + rt.get("buy", 0), rt.get("hold", 0), rt.get("sell", 0) + rt.get("strongSell", 0)]
        cut = time.time() - 14 * 86400
        out["moves"] = [dict(firm=h["firm"], action=h["action"], to=h["toGrade"], pt=h.get("currentPriceTarget"), prior=h.get("priorPriceTarget"),
                             date=dt.date.fromtimestamp(h["epochGradeDate"]).isoformat())
                        for h in z.get("upgradeDowngradeHistory", {}).get("history", []) if h["epochGradeDate"] > cut][:4]
    except Exception: pass
    try:
        base = f"https://query1.finance.yahoo.com/v7/finance/options/{t}"
        exps = desk._get_auth(base)["optionChain"]["result"][0]["expirationDates"]
        exit_ts = time.time() + hold * 1.4 * 86400
        e = next((x for x in exps if x >= exit_ts), exps[-1])
        o = desk._get_auth(f"{base}?date={e}")["optionChain"]["result"][0]["options"][0]
        iv = [x["impliedVolatility"] for x in o["calls"] + o["puts"] if abs(x["strike"] / c - 1) < 0.03 and x.get("impliedVolatility", 0) > 0.05]
        if iv: out["iv"] = sum(iv) / len(iv); out["imove"] = out["iv"] * (hold / 252) ** 0.5 * 100
    except Exception: pass
    return out

def breadth(data):
    """Market internals across the universe: share above 50/200-day, 52-week lows, dip signals."""
    xs = [b for t, b in data.items() if t != "SPY" and len(b["c"]) > 260]
    return dict(a200=sum(b["c"][-1] > b["_e200"][-1] for b in xs) / len(xs) * 100,
                a50=sum(b["c"][-1] > sum(b["c"][-50:]) / 50 for b in xs) / len(xs) * 100,
                lows=sum(b["c"][-1] <= min(b["l"][-252:]) for b in xs), n=len(xs))

def build():
    uni = desk.universe()
    data = desk.fetch_all(uni + ["SPY"], "10y")  # 10y = several regimes (2018, 2020, 2022 bear) in the stats
    for b in data.values(): b["_e200"], b["_e5"] = ema(b["c"], 200), ema(b["c"], 5)
    table = edge_table(data)
    last = {t: len(b["c"]) - 1 for t, b in data.items()}
    today = [(t, rule(b, last[t])) for t, b in data.items() if t != "SPY"]
    today = [(t, r) for t, r in today if r]
    n_dip = sum(r == "dip" for _, r in today)
    # watchlist: 5-day drop of 6-10% above EMA200, not yet a signal
    watch = sorted(((pct(b["c"][-1], b["c"][-6]), t) for t, b in data.items()
                    if t != "SPY" and liquid(b, last[t]) and b["c"][-1] > b["_e200"][-1]
                    and -10 <= pct(b["c"][-1], b["c"][-6]) < -6 and not any(t == x for x, _ in today)))
    q = quotes([t for t, _ in today] + [t for _, t in watch[:15]]) if today or watch else {}
    picks = []
    for t, r in today:
        b, z = data[t], q.get(t, {})
        ed = edays(z)
        if ed is not None and ed <= 7: continue  # never hold a swing through earnings
        s = table.get(bucket(r, n_dip, ibs(b, last[t])))
        if not s or s["n"] < 30 or s["avg"] < 0.5: continue  # needs +0.5%/trade net of costs over 10y (audit.py)
        c, e5, a = b["c"][-1], b["_e5"][-1], atr(b)
        cap = min(e5 / 1.03, c * 1.02)
        if cap <= c * 0.97: continue  # target too close to price: no room left
        picks.append(dict(t=t, kind="SIGNAL", rule=r, c=c, buy=cap, tgt=e5, stop=cap - 2 * a, days=s["days"],
                          conf=confidence(s), s=s, ed=ed, name=z.get("shortName", ""), drop=pct(c, b["c"][-6])))
    picks.sort(key=lambda p: -p["s"]["avg"])
    picks = picks[:TOP]
    for d5, t in watch:
        if len(picks) >= TOP: break
        b, z = data[t], q.get(t, {})
        c, a = b["c"][-1], atr(b)
        trig = b["c"][-5] * 0.90  # price where it becomes a 10% 5-day dip tomorrow (approx)
        picks.append(dict(t=t, kind="WATCH", rule="watch", c=c, buy=trig, tgt=b["_e5"][-1], stop=trig - 2 * a, days=None,
                          conf=None, s=None, ed=edays(z), name=z.get("shortName", ""), drop=d5))
    for p in picks:
        b = data[p["t"]]
        p["rvol"] = b["v"][-1] / (sum(b["v"][-21:-1]) / 20)
        p["dd"] = desk_data(p["t"], p["c"], p["days"] or 4)
        p["news"] = ticker_news(p["t"])
        # audit 2026-10-22: signal-day volume 3x+ normal won 55%, -0.03% net over 10y (usually company news), vs 64% / +0.5% otherwise
        if p["conf"] is not None and p["rvol"] >= 3: p["conf"] = max(1.0, p["conf"] - 1)
    r = dict(picks=picks, n_dip=n_dip, n_sig=len(today), table=table, asof=max(b["d"][-1] for b in data.values()),
             news=headlines(), cal=econ_calendar(), earn=earnings_week(set(uni)), macro=macro(), sectors=sectors(), breadth=breadth(data))
    apply_ai(r)
    r["track"] = track(r)
    return r

def edays(z):
    ets = z.get("earningsTimestamp")
    return trading_days_until(ets) if ets and ets > time.time() else None

def earnings_veto(p):
    """Known-in-advance earnings inside the hold window: veto in Python, no AI research spent on it."""
    if p["ed"] is not None and p["ed"] <= 6:
        return dict(verdict="VETO", cause="company", why=f"Earnings in {p['ed']} trading days, inside the hold window.",
                    catalysts="earnings", flags=["earnings gap risk"], sources=[])

def ticker_news(t):
    seen, out = set(), []
    for h in feed(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US", 6) + \
             feed(f"https://news.google.com/rss/search?q={t}+stock+when:7d&hl=en-US&gl=US&ceid=US:en", 6):
        k = re.sub(r"\W", "", h[0].lower())[:50]
        if k not in seen: seen.add(k); out.append(h)
    return out[:10]

AI_FILE = os.path.join(desk.HERE, "ai.json")
def apply_ai(r):
    """Claude's research (ai.json from the daily routine) if it covers today's data, else free Groq checks."""
    cl = json.load(open(AI_FILE)) if os.path.exists(AI_FILE) else {}
    if cl.get("asof") != r["asof"]: cl = {}
    r["ai_source"] = cl.get("model", "Claude") + " research" if cl else ("Groq quick check" if os.environ.get("GROQ_API_KEY") else None)
    r["note"] = cl.get("note") or ai.desk_note(r["macro"], r["sectors"], r["news"], r["cal"], r["picks"])
    for p in r["picks"]:
        p["ai"] = earnings_veto(p) or ((cl.get("picks") or {}).get(p["t"]) if cl else ai.check_pick(p, [h for h, *_ in p["news"]]))
        v = (p["ai"] or {}).get("verdict")
        if p["conf"] is not None and v == "CAUTION": p["conf"] = max(1.0, p["conf"] - 1)
        if p["conf"] is not None and v == "VETO": p["conf"] = 0.0
    r["picks"].sort(key=lambda p: (p["kind"] != "SIGNAL", (p["ai"] or {}).get("verdict") == "VETO"))

HIST = os.path.join(desk.HERE, "history.json")
def track(r):
    """Log today's signals, then score every logged pick against real prices (limit fill, target/stop/day-6 exit)."""
    hist = json.load(open(HIST)) if os.path.exists(HIST) else []
    have = {(h["asof"], h["t"]) for h in hist}
    for p in r["picks"]:
        if p["kind"] == "SIGNAL" and (r["asof"], p["t"]) not in have:
            hist.append(dict(asof=r["asof"], t=p["t"], buy=round(p["buy"], 2), tgt=round(p["tgt"], 2), stop=round(p["stop"], 2),
                             conf=round(p["conf"], 1), verdict=(p["ai"] or {}).get("verdict")))
    json.dump(hist, open(HIST, "w"), indent=1)
    rows = []
    for h in hist:
        b = desk.bars(h["t"])
        i = b["d"].index(h["asof"]) if b and h["asof"] in b["d"] else None
        if i is None or i + 1 >= len(b["d"]): rows.append(dict(h, status="pending", ret=None)); continue
        if b["l"][i + 1] > h["buy"]: rows.append(dict(h, status="not filled: stayed above limit", ret=None)); continue
        en, res = min(b["o"][i + 1], h["buy"]), None  # day limit order: fills at the open or when price dips to the limit
        for j in range(i + 1, min(i + 7, len(b["d"]))):
            if b["l"][j] <= h["stop"]: res = ("stop", pct(h["stop"], en)); break  # ponytail: stop assumed first when both hit
            if b["h"][j] >= h["tgt"]: res = ("target", pct(h["tgt"], en)); break
        if not res:
            res = ("time exit", pct(b["c"][i + 6], en)) if i + 6 < len(b["d"]) else ("open", pct(b["c"][-1], en))
        rows.append(dict(h, status=res[0], ret=res[1]))
    return rows

def macro():
    m = {"ES=F": "S&P fut", "NQ=F": "Nasdaq fut", "^VIX": "VIX", "^TNX": "US 10Y", "BZ=F": "Brent", "GC=F": "Gold",
         "BTC-USD": "Bitcoin", "DX-Y.NYB": "Dollar"}
    q = quotes(list(m))
    return [(name, q.get(s, {}).get("regularMarketPrice", 0), q.get(s, {}).get("regularMarketChangePercent", 0)) for s, name in m.items()]

def sectors():
    names = dict(XLK="Tech", SMH="Semis", IGV="Software", XLF="Financials", XLE="Energy", XLV="Health", XLI="Industrials",
                 XLY="Discretionary", XLP="Staples", XLU="Utilities", XLB="Materials", XLRE="Real estate", XLC="Comms")
    rows = [(pct(b["c"][-1], b["c"][-6]), names[s], pct(b["c"][-1], b["c"][-21])) for s, b in desk.fetch_all(list(names), "1y").items()]
    return sorted(rows, reverse=True)

# ---------- html ----------
E = html.escape
def sign(x): return "up" if x > 0 else "dn" if x < 0 else ""
def fmt_ret(x): return "" if x is None else f"{x:+.1f}%"

def add_trading_days(d, n):
    d = dt.date.fromisoformat(d)
    while n:
        d += dt.timedelta(1)
        if d.weekday() < 5: n -= 1
    return d

def status(p):
    v = (p.get("ai") or {}).get("verdict")
    if v == "VETO": return "skip", "Skip"
    if p["kind"] == "SIGNAL": return ("trade", "Trade today") if v != "CAUTION" else ("trade caution", "Trade · caution")
    return "watch", "Watching"

def ladder(p):
    """Horizontal price map: stop, buy, target, and where the stock last closed."""
    pts = {"Stop": p["stop"], "Buy" if p["kind"] == "SIGNAL" else "Trigger": p["buy"], "Target": p["tgt"]}
    lo, hi = min(list(pts.values()) + [p["c"]]), max(list(pts.values()) + [p["c"]])
    pos = lambda x: (x - lo) / (hi - lo) * 100 if hi > lo else 50
    marks = "".join(f'<span class="mk {k.lower()}" style="left:{pos(v):.1f}%"><i></i><b>{k}</b><em>{v:,.2f}</em></span>' for k, v in pts.items())
    return (f'<div class="ladder" aria-hidden="true"><div class="rail">'
            f'<span class="risk" style="left:{pos(p["stop"]):.1f}%;width:{pos(p["buy"]) - pos(p["stop"]):.1f}%"></span>'
            f'<span class="gain" style="left:{pos(p["buy"]):.1f}%;width:{pos(p["tgt"]) - pos(p["buy"]):.1f}%"></span>'
            f'<span class="now" style="left:{pos(p["c"]):.1f}%"><b>Last {p["c"]:,.2f}</b></span></div>{marks}</div>')

def card(p, asof):
    cls, label = status(p)
    a = p.get("ai") or {}
    up, down = pct(p["tgt"], p["buy"]), pct(p["stop"], p["buy"])
    if p["kind"] == "SIGNAL":
        exit_by = add_trading_days(asof, 6)
        play = (f'Down {abs(p["drop"]):.0f}% in 5 days while its long-term uptrend holds. Buy the fear at the open, '
                f'sell the rebound to its 5-day average, usually within {p["days"]} trading days.')
        steps = (f'<li><span>Buy</span><div><b>Limit at ${p["buy"]:,.2f} or lower</b> at the US open (16:30 Israel). '
                 f'If it opens above that, skip it.</div></li>'
                 f'<li><span>Take profit</span><div><b class="up">${p["tgt"]:,.2f}</b> <i>+{up:.1f}%</i> · sell limit, good-til-cancelled</div></li>'
                 f'<li><span>Stop loss</span><div><b class="dn">${p["stop"]:,.2f}</b> <i>{down:.1f}%</i> · stop order, good-til-cancelled</div></li>'
                 f'<li><span>Exit by</span><div><b>{exit_by:%a %d %b}</b> at the close, whatever happens</div></li>')
        s = p["s"]
        conf = (f'<div class="conf"><b>{p["conf"]:.1f}</b><span>/10</span></div>'
                f'<p class="mut">Similar setups won <b>{s["win"]:.0f}%</b> of {s["n"]} past trades, average {s["avg"]:+.1f}%.</p>')
    else:
        play = (f'Down {abs(p["drop"]):.0f}% in 5 days, close to a dip-bounce setup but not there yet. '
                f'It becomes a trade if it closes at or below ${p["buy"]:,.2f}; the next morning page then gives exact prices.')
        steps = (f'<li><span>Trigger</span><div>A close at or below <b>${p["buy"]:,.2f}</b> (last close ${p["c"]:,.2f})</div></li>'
                 f'<li><span>Then roughly</span><div>target <b class="up">${p["tgt"]:,.2f}</b>, stop <b class="dn">${p["stop"]:,.2f}</b>, hold 3-4 days</div></li>'
                 f'<li><span>Today</span><div>No order. Do not buy early.</div></li>')
        conf = '<div class="conf idle"><b>Wait</b></div><p class="mut">No trade until the trigger hits.</p>'
    drive = ""
    if a:
        flags = "".join(f'<span class="chip">{E(f)}</span>' for f in a.get("flags", []))
        drive = (f'<div class="drive {a["verdict"].lower()}"><div class="dh"><b>AI check: {E(a["verdict"].title())}</b>'
                 f'<span class="chip">{E(a.get("cause", ""))} driven</span></div><p>{E(a.get("why", ""))}</p>'
                 + sentiment(a)
                 + (f'<div class="bb"><p><b class="up">Bull</b> {E(a["bull"])}</p><p><b class="dn">Bear</b> {E(a["bear"])}</p></div>' if a.get("bull") and a.get("bear") else "")
                 + (f'<p class="mut"><b>Coming up:</b> {E(a["catalysts"])}</p>' if a.get("catalysts") else "")
                 + (f'<div>{flags}</div>' if flags else "") + '</div>')
    links = "".join(f'<li><a href="{E(x.get("url", ""))}" target="_blank" rel="noopener">{E(x.get("title") or x.get("url", ""))}</a></li>'
                    for x in a.get("sources", []))
    links += "".join(f'<li><a href="{E(u)}" target="_blank" rel="noopener">{E(h)}</a> <span class="mut">{E(src)}</span></li>'
                     for h, u, src, _ in p["news"][:4])
    more = f'<details><summary>Sources and headlines</summary><ul class="links">{links}</ul></details>' if links else ""
    return (f'<article class="card {cls.split()[0]}">'
            f'<div class="ch"><div><h3>{E(p["t"])}</h3><div class="mut">{E(p["name"])}</div></div><span class="badge {cls}">{label}</span></div>'
            f'<p class="play"><b>The play.</b> {play}</p>{ladder(p)}'
            f'<div class="body"><ol class="steps">{steps}</ol><div class="side">{conf}</div></div>{panel(p)}{drive}{more}</article>')

def panel(p):
    """Desk data block: tape, options, positioning, the street. Facts only, each one line."""
    d, rows = p.get("dd") or {}, []
    rv = p.get("rvol")
    if rv:
        rows.append(("Volume", f'{rv:.1f}x normal on the last day' + (' · <b class="dn">news-sized, -1 confidence</b>' if rv >= 3 and p["kind"] == "SIGNAL"
                     else ' · <b class="dn">news-sized: check the cause</b>' if rv >= 3 else ' · selling climax zone' if rv >= 2 else "")))
    if d.get("imove"):
        risk = abs(pct(p["stop"], p["buy"]))
        rows.append(("Options", f'price a <b>&plusmn;{d["imove"]:.1f}%</b> move by the exit (IV {d["iv"] * 100:.0f}%)'
                     + (f' · stop is {risk / d["imove"]:.1f}x that' if p["kind"] == "SIGNAL" else "")))
    if d.get("short") is not None:
        rows.append(("Short", f'{d["short"] * 100:.1f}% of float' + (f', {d["dtc"]:.1f} days to cover' if d.get("dtc") else "")
                     + (' · squeeze fuel' if d["short"] >= 0.1 else "")))
    if d.get("ratings"):
        b, h, s = d["ratings"]
        mv = "; ".join(f'{E(m["firm"])} {"upgrade" if m["action"] == "up" else "downgrade" if m["action"] == "down" else "reiterate"} '
                       f'{E(m["to"])} {m["date"][5:]}' + (f' (PT {m["prior"]:.0f}&rarr;{m["pt"]:.0f})' if m.get("pt") and m.get("prior") else "")
                       for m in d.get("moves", []))
        rows.append(("Analysts", f'{b} buy · {h} hold · {s} sell' + (f'<br><span class="mut">{mv}</span>' if mv else "")))
    return ('<dl class="dd">' + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>") if rows else ""

def sentiment(a):
    x = a.get("sentiment")
    if x is None: return ""
    return (f'<div class="sent"><span>News sentiment</span><div class="sbar"><i style="left:{(x + 1) * 50:.0f}%"></i></div>'
            f'<b class="{sign(x)}">{x:+.2f}</b><span class="mut">conf {a.get("sent_conf", 0):.1f}</span></div>')

def skip_row(p):
    a = p.get("ai") or {}
    return f'<li><b>{E(p["t"])}</b><span>{E(a.get("why", ""))}</span></li>'

CSS = """
:root{--bg:#0b0e13;--panel:#141922;--panel2:#1a2029;--line:#262e3a;--txt:#e8ebf0;--mut:#9aa3b2;--acc:#f5b301;--up:#2fd47a;--dn:#ff5a5a;--warn:#f5b301}
@media (prefers-color-scheme:light){:root:not([data-theme=dark]){--bg:#f4f5f7;--panel:#fff;--panel2:#f7f8fa;--line:#e2e5ea;--txt:#141a22;--mut:#5b6472;--acc:#a86f00;--up:#13803d;--dn:#c62828;--warn:#a86f00}}
*{box-sizing:border-box;min-width:0}html,body{overflow-x:hidden}
body{margin:0;background:var(--bg);color:var(--txt);font:16px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
main{max-width:880px;margin:0 auto;padding:20px 16px 64px}
a{color:inherit}a:hover{color:var(--acc)}
.mut{color:var(--mut);font-size:14px}.up{color:var(--up)}.dn{color:var(--dn)}
header{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px}
h1{margin:0;font-size:20px;letter-spacing:.06em}h1 b{color:var(--acc)}
h2{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);margin:36px 0 6px;display:flex;align-items:center;gap:8px}
.hero{margin:18px 0 6px;padding:18px;border-radius:14px;background:var(--panel);border:1px solid var(--line)}
.hero b{font-size:24px;display:block;line-height:1.25}.hero p{margin:4px 0 0}.hero details b{font-size:inherit;display:inline}
.strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}
.tick{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 10px;display:flex;flex-direction:column}
.tick span{color:var(--mut);font-size:12px}.tick b{font-size:15px}.tick i{font-style:normal;font-size:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}
.card.trade{border-color:color-mix(in srgb,var(--up) 45%,var(--line))}
.ch{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.ch h3{margin:0;font-size:22px;letter-spacing:.02em}
.badge{flex:none;font-size:13px;font-weight:600;padding:5px 11px;border-radius:999px;border:1px solid}
.badge.trade{color:var(--up);border-color:var(--up)}.badge.caution{color:var(--warn);border-color:var(--warn)}
.badge.watch{color:var(--mut);border-color:var(--line)}.badge.skip{color:var(--dn);border-color:var(--dn)}
.play{margin:12px 0 4px}.play b{color:var(--acc)}
.ladder{position:relative;height:78px;margin:22px 12px 4px}
.rail{position:absolute;left:0;right:0;top:28px;height:6px;border-radius:3px;background:var(--line)}
.rail .risk,.rail .gain{position:absolute;top:0;height:6px}.rail .risk{background:color-mix(in srgb,var(--dn) 55%,transparent)}.rail .gain{background:var(--up)}
.rail .now{position:absolute;top:-26px;transform:translateX(-50%);white-space:nowrap;font-size:12px;color:var(--mut)}
.rail .now b{font-weight:500}
.rail .now::after{content:"";position:absolute;left:50%;top:18px;width:2px;height:18px;background:var(--txt);transform:translateX(-50%)}
.mk{position:absolute;top:24px;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;font-size:12px;white-space:nowrap;line-height:1.3}
.mk i{width:14px;height:14px;border-radius:50%;border:2px solid var(--panel);margin-bottom:4px}
.mk b{font-weight:600}.mk em{font-style:normal;color:var(--mut)}
.mk.stop i{background:var(--dn)}.mk.buy i,.mk.trigger i{background:var(--txt)}.mk.target i{background:var(--up)}
.mk.stop{transform:translateX(-14%);align-items:flex-start}.mk.target{transform:translateX(-86%);align-items:flex-end}
.body{display:grid;grid-template-columns:1fr 180px;gap:16px;margin-top:10px}
.steps{list-style:none;margin:0;padding:0}.steps li{display:grid;grid-template-columns:104px 1fr;gap:10px;padding:9px 0;border-top:1px solid var(--line)}
.steps span{color:var(--mut);font-size:14px}.steps i{color:var(--mut);font-style:normal}
.side{background:var(--panel2);border-radius:10px;padding:12px;align-self:start}.side p{margin:6px 0 0}
.conf b{font-size:34px;color:var(--acc);line-height:1}.conf span{color:var(--mut)}.conf.idle b{font-size:24px;color:var(--mut)}
.drive{margin-top:12px;padding:12px 14px;border-radius:10px;background:var(--panel2);border-left:3px solid var(--mut)}
.drive.confirm{border-color:var(--up)}.drive.caution{border-color:var(--warn)}.drive.veto{border-color:var(--dn)}
.drive p{margin:6px 0 0}.dh{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.chip{display:inline-block;margin:6px 6px 0 0;padding:2px 9px;border:1px solid var(--line);border-radius:999px;font-size:12px;color:var(--mut);text-transform:none;letter-spacing:0}
.dh .chip,h2 .chip{margin:0}
details{margin-top:6px}summary{cursor:pointer;min-height:44px;display:flex;align-items:center;color:var(--mut);font-size:14px}
.hero summary{color:var(--acc)}summary::before{content:"\\25B8";margin-right:8px;transition:transform .15s}details[open]>summary::before{transform:rotate(90deg)}summary::-webkit-details-marker{display:none}summary{list-style:none}
.links{list-style:none;padding:0;margin:0}.links li{padding:8px 0;border-top:1px solid var(--line);font-size:14px}
.skips{list-style:none;padding:0;margin:8px 0 0;background:var(--panel);border:1px solid var(--line);border-radius:14px}
.skips li{display:grid;grid-template-columns:64px 1fr;gap:10px;padding:12px 16px;border-top:1px solid var(--line);font-size:14px}.skips li:first-child{border-top:0}
.skips span{color:var(--mut)}
.dd{display:grid;grid-template-columns:96px 1fr;gap:6px 12px;margin:14px 0 0;padding:12px 0 0;border-top:1px solid var(--line);font-size:14px}
.dd dt{color:var(--mut)}.dd dd{margin:0}
.sent{display:flex;align-items:center;gap:10px;margin:8px 0;font-size:14px}.sent>span:first-child{color:var(--mut)}.sent span{white-space:nowrap}
.sbar{position:relative;flex:0 1 140px;height:6px;border-radius:3px;background:linear-gradient(90deg,var(--dn),var(--line),var(--up))}
.sbar i{position:absolute;top:-4px;width:3px;height:14px;margin-left:-1px;background:var(--txt);border-radius:2px}
.bb{display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:14px}.bb p{margin:6px 0}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:4px 16px 12px;margin-top:12px}.panel h2{margin-top:14px}
.bul{margin:0;padding-left:18px}.bul li{margin:6px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:7px 4px;border-top:1px solid var(--line)}.num{text-align:right}
.news{list-style:none;padding:0;margin:0}.news li{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-top:1px solid var(--line);font-size:14px}
.news a{text-decoration:none}.news span{color:var(--mut);font-size:12px;flex:none;max-width:30%;text-align:right}
@media (max-width:640px){.strip{grid-template-columns:repeat(2,1fr)}.body,.grid2,.bb{grid-template-columns:1fr}.steps li{grid-template-columns:92px 1fr}}
"""

def render(r):
    il = dt.datetime.now(ZoneInfo("Asia/Jerusalem"))
    picks = r["picks"]
    trade = [p for p in picks if status(p)[0].startswith("trade")]
    watch = [p for p in picks if status(p)[0] == "watch"]
    skip = [p for p in picks if status(p)[0] == "skip"]
    verdict = f'{len(trade)} trade{"s" if len(trade) > 1 else ""} ready for the open' if trade else "No trades today"
    sub = f'{len(watch)} on the watchlist · {len(skip)} skipped' + (" · cluster day: strongest historical edge" if r["n_dip"] >= 10 else "")
    note = r.get("note")
    note_html = (f'<section class="panel"><h2>Morning note <span class="chip">{E(note.get("mood", ""))}</span></h2><ul class="bul">'
                 + "".join(f"<li>{E(b)}</li>" for b in note["bullets"]) + f'</ul><p class="mut">{E(r["ai_source"] or "")}</p></section>') if note else ""
    arrow = lambda c: "▲" if c > 0 else "▼" if c < 0 else ""
    macro_html = "".join(f'<div class="tick"><span>{E(n)}</span><b>{v:,.2f}</b><i class="{sign(c)}">{arrow(c)} {abs(c):.2f}%</i></div>' for n, v, c in r["macro"])
    br = r["breadth"]
    breadth_html = (f'<div class="tick"><span>Above 200-day</span><b>{br["a200"]:.0f}%</b><i class="mut">of {br["n"]} stocks</i></div>'
                    f'<div class="tick"><span>Above 50-day</span><b>{br["a50"]:.0f}%</b><i class="mut">short-term trend</i></div>'
                    f'<div class="tick"><span>52-week lows</span><b>{br["lows"]}</b><i class="mut">stocks today</i></div>'
                    f'<div class="tick"><span>Dip signals</span><b>{r["n_dip"]}</b><i class="mut">{"cluster day" if r["n_dip"] >= 10 else "10+ = cluster day"}</i></div>')
    sec_html = "".join(f'<tr><td>{E(n)}</td><td class="num {sign(a)}">{a:+.1f}%</td><td class="num {sign(b)}">{b:+.1f}%</td></tr>' for a, n, b in r["sectors"])
    news_html = "".join(f'<li><a href="{E(u)}" target="_blank" rel="noopener">{E(h)}</a><span>{E(s)}</span></li>' for h, u, s, _ in r["news"][:12])
    cal_html = "".join(f'<tr><td>{w.astimezone(il.tzinfo):%a %H:%M}</td><td>{E(t)}</td><td class="num">{"High" if imp == "High" else "Med"}</td></tr>'
                       for w, t, imp, *_ in r["cal"])
    earn_html = "".join(f'<tr><td>{d:%a %d %b}</td><td>{E(", ".join(n) or "-")}</td></tr>' for d, n in r["earn"])
    done = [t for t in r["track"] if t["ret"] is not None and t["status"] != "open"]
    summ = (f'<b>{len(done)}</b> closed · <b>{sum(t["ret"] > 0 for t in done) / len(done) * 100:.0f}%</b> won · '
            f'<b>{sum(t["ret"] for t in done) / len(done):+.2f}%</b> average' if done else "No closed trades yet.")
    track_html = (f'<p>{summ} <span class="mut">(10-year backtest, after costs: 65-70% won, +0.7% to +1.5% average)</span></p><table>'
                  + "".join(f'<tr><td>{t["asof"]}</td><td><b>{E(t["t"])}</b></td><td>{E(t["status"])}</td>'
                            f'<td class="num {sign(t["ret"] or 0)}">{fmt_ret(t["ret"])}</td></tr>' for t in reversed(r["track"][-15:])) + "</table>")
    def section(title, sub, items):
        return f'<section><h2>{title}</h2><p class="mut">{sub}</p>{"".join(items)}</section>' if items else ""
    skips = (f'<section><h2>Skipped</h2><p class="mut">Looked at, then ruled out.</p><ul class="skips">'
             + "".join(skip_row(p) for p in skip) + '</ul></section>') if skip else ""
    how = ('<details><summary>How to use this page</summary><p class="mut">Only cards under <b>Trade today</b> are trades. '
           'Before 16:30 Israel time, place a limit buy at the buy price and attach a take-profit and a stop-loss (good-til-cancelled). '
           'If it opens above the buy price, skip it. Sell by the exit date whatever happens. All open trades together should risk '
           'at most 1-2% of your account. Not financial advice.</p></details>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Swing Desk</title><meta name="robots" content="noindex"><meta name="theme-color" content="#0b0e13">'
            f'<style>{CSS}</style></head><body><main>'
            f'<header><h1>SWING <b>DESK</b></h1><span class="mut">{il:%a %d %b, %H:%M} Israel · prices as of {r["asof"]} close</span></header>'
            f'<div class="hero"><b>{verdict}</b><p class="mut">{sub}</p>{how}</div>'
            f'<div class="strip">{macro_html}</div><div class="strip">{breadth_html}</div>{note_html}'
            + section("Trade today", "Place these before the US open.", [card(p, r["asof"]) for p in trade])
            + section("Watchlist", "Not trades yet. Each shows the price that would turn it into one.", [card(p, r["asof"]) for p in watch])
            + skips
            + '<h2>Market</h2><div class="grid2">'
            f'<section class="panel"><h2>Sector money flow</h2><table><tr><td class="mut">Sector</td><td class="num mut">5 days</td><td class="num mut">20 days</td></tr>{sec_html}</table></section>'
            f'<section class="panel"><h2>US data this week</h2><table>{cal_html or "<tr><td class=mut>No major US data</td></tr>"}</table>'
            f'<h2>Earnings next 5 days</h2><table>{earn_html}</table></section></div>'
            f'<section class="panel"><h2>Headlines</h2><ul class="news">{news_html}</ul></section>'
            f'<section class="panel"><h2>Track record</h2>{track_html}</section>'
            '<p class="mut" style="margin-top:28px">Confidence = how often this exact setup won over 10 years of S&amp;P 500 and 400 stocks, '
            'after 0.15% costs and with gaps through the stop filled at the open, recomputed daily. Minus 1 for news-sized volume or an AI caution. '
            'Desk data and news sentiment are context, not part of the tested edge. Not financial advice.</p>'
            '</main></body></html>')

if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    r = build()
    open(OUT, "w").write(render(r))
    # picks.json: committed by the workflow, read by the Claude research routine (its sandbox can't reach Yahoo)
    json.dump(dict(asof=r["asof"], picks=[{k: v for k, v in p.items() if k != "ai"} for p in r["picks"]],
                   macro=r["macro"], sectors=r["sectors"], news=r["news"], cal=[(str(w), *x) for w, *x in r["cal"]]),
              open(os.path.join(desk.HERE, "picks.json"), "w"), indent=1, default=str)
    print(OUT, f"{sum(p['kind'] == 'SIGNAL' for p in r['picks'])} signals, {len(r['picks'])} rows, {len(r['news'])} headlines")
