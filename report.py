#!/usr/bin/env python3
"""Daily desk report -> site/index.html. Run once a day before 9:00 Israel time.

Picks come only from rules that backtested with an edge; confidence is that bucket's own
backtest win rate, recomputed every run on 2y of data. Slots the edge can't fill become a
labelled watchlist, never fake picks.
"""
import os, sys, json, time, html, re, datetime as dt, urllib.request, xml.etree.ElementTree as ET
from collections import defaultdict
from zoneinfo import ZoneInfo
import desk, ai
from desk import ema, atr, pct, quotes, trading_days_until

OUT = os.path.join(desk.HERE, "site", "index.html")
TOP = 10

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
    """Backtest every signal in the sample; stats per (rule, cluster, IBS) bucket."""
    n = len(data["SPY"]["c"])
    sigs = defaultdict(list)
    for t, b in data.items():
        if t == "SPY" or len(b["c"]) != n: continue
        for i in range(260, n - 7):
            r = rule(b, i)
            if r: sigs[i].append((t, r))
    stats = defaultdict(list)
    for i, lst in sigs.items():
        nd = sum(r == "dip" for _, r in lst)
        for t, r in lst:
            b = data[t]; O, L, C, e5 = b["o"], b["l"], b["c"], b["_e5"]
            en, st, out, held = O[i + 1], O[i + 1] - 2 * atr(b, end=i), None, 6
            for j in range(i + 1, i + 7):
                if L[j] <= st: out, held = pct(st, en), j - i; break
                if C[j] > e5[j]: out, held = pct(C[j], en), j - i; break
            stats[bucket(r, nd, ibs(b, i))].append((pct(C[i + 6], en) if out is None else out, held))
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

def build():
    uni = desk.universe()
    data = desk.fetch_all(uni + ["SPY"])
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
        ets = z.get("earningsTimestamp")
        ed = trading_days_until(ets) if ets and ets > time.time() else None
        if ed is not None and ed <= 7: continue  # never hold a swing through earnings
        s = table.get(bucket(r, n_dip, ibs(b, last[t])))
        if not s or s["n"] < 30 or s["avg"] < 0.3: continue  # bucket without a real edge
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
                          conf=None, s=None, ed=None, name=z.get("shortName", ""), drop=d5))
    for p in picks: p["news"] = ticker_news(p["t"])
    r = dict(picks=picks, n_dip=n_dip, n_sig=len(today), table=table, asof=max(b["d"][-1] for b in data.values()),
             news=headlines(), cal=econ_calendar(), earn=earnings_week(set(uni)), macro=macro(), sectors=sectors())
    apply_ai(r)
    r["track"] = track(r)
    return r

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
        p["ai"] = (cl.get("picks") or {}).get(p["t"]) if cl else ai.check_pick(p, [h for h, *_ in p["news"]])
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

def render(r):
    il = dt.datetime.now(ZoneInfo("Asia/Jerusalem"))
    sig = [p for p in r["picks"] if p["kind"] == "SIGNAL"]
    head = (f"{len(sig)} tradeable signal{'s' if len(sig) != 1 else ''} from {r['n_sig']} raw "
            + ("· CLUSTER DAY: strongest historical edge" if r["n_dip"] >= 10 else
               "· quiet day: no forced trades" if not sig else ""))
    rows = []
    for i, p in enumerate(r["picks"], 1):
        if p["kind"] == "SIGNAL":
            s = p["s"]; rr = (p["tgt"] - p["buy"]) / (p["buy"] - p["stop"])
            conf = f'<div class="conf"><b>{p["conf"]:.1f}</b>/10</div><div class="mut">{s["win"]:.0f}% win · {s["avg"]:+.1f}% avg · n={s["n"]}</div>'
            plan = (f'<div class="lv"><span>Buy limit ≤</span><b>{p["buy"]:.2f}</b></div>'
                    f'<div class="lv"><span>Take profit</span><b class="up">{p["tgt"]:.2f}</b> <i>{pct(p["tgt"], p["buy"]):+.1f}%</i></div>'
                    f'<div class="lv"><span>Stop loss</span><b class="dn">{p["stop"]:.2f}</b> <i>{pct(p["stop"], p["buy"]):+.1f}%</i></div>'
                    f'<div class="lv"><span>Hold</span><b>~{p["days"]}d</b> <i>max 6 · R:R {rr:.2f}</i></div>')
            tag = "Dip-bounce" if p["rule"] == "dip" else "3-day reversal"
        else:
            conf = '<div class="conf mut">watch</div><div class="mut">not a signal yet</div>'
            plan = (f'<div class="lv"><span>Becomes a signal near</span><b>{p["buy"]:.2f}</b></div>'
                    f'<div class="lv mut"><span>Re-check tomorrow\'s report</span></div>')
            tag = "Watchlist"
        news = "".join(f'<a href="{E(u)}" target="_blank" rel="noopener">{E(h)}</a>' for h, u, _, _ in p["news"][:2])
        a = p.get("ai")
        if a:
            src = "".join(f' <a href="{E(x.get("url", ""))}" target="_blank" rel="noopener">[{k + 1}]</a>' for k, x in enumerate(a.get("sources", [])[:4]))
            extra = f'<div class="mut">{E(a["catalysts"])}</div>' if a.get("catalysts") else ""
            flags = "".join(f'<span class="flag">{E(f)}</span>' for f in a.get("flags", []))
            news = (f'<div class="ai {a["verdict"].lower()}"><b>AI {E(a["verdict"])}</b> <i>{E(a.get("cause", ""))}</i> '
                    f'{E(a.get("why", ""))}{src}{extra}<div>{flags}</div></div>') + news
        rows.append(f'''<article class="pick {p["kind"].lower()}">
  <div class="top"><span class="rank">{i}</span><div><h3>{E(p["t"])}</h3><div class="mut">{E(p["name"])}</div></div>
  <span class="tag">{tag}</span></div>
  <div class="grid"><div>{conf}<div class="mut">close {p["c"]:.2f} · 5d <span class="{sign(p["drop"])}">{p["drop"]:+.1f}%</span></div></div><div>{plan}</div></div>
  {f'<div class="news">{news}</div>' if news else ''}
</article>''')
    macro_html = "".join(f'<div class="tick"><span>{E(n)}</span><b>{v:,.2f}</b><i class="{sign(c)}">{c:+.2f}%</i></div>' for n, v, c in r["macro"])
    sec_html = "".join(f'<tr><td>{E(n)}</td><td class="{sign(a)}">{a:+.1f}%</td><td class="{sign(b)}">{b:+.1f}%</td></tr>' for a, n, b in r["sectors"])
    news_html = "".join(f'<li><a href="{E(u)}" target="_blank" rel="noopener">{E(h)}</a><span>{E(s)}</span></li>' for h, u, s, _ in r["news"])
    cal_html = "".join(f'<tr><td>{w.astimezone(il.tzinfo):%a %H:%M}</td><td>{E(t)}</td><td class="{"dn" if imp == "High" else ""}">{imp}</td><td>{E(f)}</td><td>{E(pv)}</td></tr>' for w, t, imp, f, pv in r["cal"])
    note = r.get("note")
    note_html = (f'<h2>Desk note · {E(note.get("mood", ""))}</h2><div class="rules note"><ul>'
                 + "".join(f"<li>{E(b)}</li>" for b in note["bullets"]) + f'</ul><div class="mut">AI: {E(r["ai_source"] or "")}. Built only from data on this page; not a forecast.</div></div>') if note else ""
    done = [t for t in r["track"] if t["ret"] is not None and t["status"] != "open"]
    summ = (f'{len(done)} closed · {sum(t["ret"] > 0 for t in done) / len(done) * 100:.0f}% win · {sum(t["ret"] for t in done) / len(done):+.2f}% avg per trade'
            if done else "No closed trades yet: the log started 2026-10-21.")
    track_html = (f'<p class="mut">{summ} · backtest expectation about 65% win, +1.2% avg. Fill = day limit order at the buy limit.</p><table>'
                  + "".join(f'<tr><td>{t["asof"]}</td><td><b>{E(t["t"])}</b></td><td>{t["conf"]}</td><td>{E(t.get("verdict") or "")}</td>'
                            f'<td>{E(t["status"])}</td><td class="{sign(t["ret"] or 0)}">{fmt_ret(t["ret"])}</td></tr>'
                            for t in reversed(r["track"][-20:])) + "</table>")
    earn_html = "".join(f'<tr><td>{d:%a %d %b}</td><td>{E(", ".join(n) or "-")}</td></tr>' for d, n in r["earn"])
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Swing Desk</title><meta name="robots" content="noindex">
<style>
:root{{--bg:#0b0e13;--panel:#131821;--line:#222a36;--txt:#e6e9ef;--mut:#8a93a3;--acc:#f5b301;--up:#22c55e;--dn:#ef4444}}
@media (prefers-color-scheme:light){{:root:not([data-theme=dark]){{--bg:#f6f7f9;--panel:#fff;--line:#e3e6eb;--txt:#151a22;--mut:#5d6675;--acc:#b07d00;--up:#15803d;--dn:#c62828}}}}
*{{box-sizing:border-box;min-width:0}}html,body{{overflow-x:hidden}}body{{margin:0;background:var(--bg);color:var(--txt);font:15px/1.45 ui-sans-serif,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:20px 16px 60px}}
header{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;border-bottom:2px solid var(--acc);padding-bottom:10px}}
h1{{margin:0;font-size:22px;letter-spacing:.04em}}h1 b{{color:var(--acc)}}h2{{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--acc);margin:28px 0 10px}}
.mut{{color:var(--mut);font-size:13px}}.up{{color:var(--up)}}.dn{{color:var(--dn)}}
.strip{{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px;margin-top:14px}}
.tick{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;display:flex;flex-direction:column}}
.tick span{{color:var(--mut);font-size:12px}}.tick i{{font-style:normal;font-size:12px}}
.pick{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:10px}}
.pick.signal{{border-left:3px solid var(--acc)}}.pick.watch{{opacity:.8}}
.top{{display:flex;gap:12px;align-items:center}}.top h3{{margin:0;font-size:19px}}.top>div{{flex:1}}
.rank{{width:28px;height:28px;border-radius:50%;background:var(--line);display:grid;place-items:center;font-weight:700;flex:none}}
.tag{{margin-left:auto;font-size:11px;letter-spacing:.08em;text-transform:uppercase;border:1px solid var(--line);border-radius:20px;padding:3px 9px;color:var(--mut)}}
.grid{{display:grid;grid-template-columns:200px 1fr;gap:16px;margin-top:12px}}
.conf b{{font-size:28px;color:var(--acc)}}.lv{{display:flex;gap:10px;align-items:baseline;padding:3px 0;border-bottom:1px dashed var(--line)}}
.lv span{{color:var(--mut);min-width:150px;font-size:13px}}.lv i{{color:var(--mut);font-style:normal;font-size:13px}}
.news{{margin-top:10px;display:flex;flex-direction:column;gap:3px}}.news a,li a{{color:var(--txt);text-decoration:none;font-size:13px}}.news a:hover,li a:hover{{color:var(--acc)}}
.cols{{display:grid;grid-template-columns:1.4fr 1fr;gap:20px}}ul{{list-style:none;padding:0;margin:0}}
li{{padding:7px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:10px}}li span{{color:var(--mut);font-size:12px;flex:none;max-width:30%;text-align:right}}
table{{width:100%;border-collapse:collapse;font-size:13px}}td{{padding:6px 4px;border-bottom:1px solid var(--line)}}
.ai{{margin-top:10px;padding:8px 10px;border-radius:8px;background:var(--bg);font-size:13px;border-left:3px solid var(--mut)}}
.ai.confirm{{border-color:var(--up)}}.ai.caution{{border-color:var(--acc)}}.ai.veto{{border-color:var(--dn)}}.ai a{{color:var(--acc)}}.ai i{{color:var(--mut)}}
.flag{{display:inline-block;margin:4px 4px 0 0;padding:1px 7px;border:1px solid var(--line);border-radius:10px;font-size:11px;color:var(--mut)}}
.note ul{{margin:0 0 6px}}.note li{{border:0;padding:3px 0;display:list-item;list-style:disc inside;color:var(--txt)}}
.rules{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 16px;font-size:13px;color:var(--mut)}}
@media (max-width:720px){{.grid,.cols{{grid-template-columns:1fr}}.lv span{{min-width:120px}}}}
</style></head><body><main>
<header><h1>SWING <b>DESK</b></h1><div class="mut">Updated {il:%a %d %b %Y, %H:%M} Israel · data as of {r["asof"]} close · US open 16:30</div></header>
<div class="strip">{macro_html}</div>
{note_html}<h2>Today's top {TOP}</h2><p class="mut">{head}. Entries are LIMIT orders at the US open: if it opens above the buy limit, skip. Attach the bracket (take profit + stop) GTC. Exit by day 6 regardless.</p>
{"".join(rows) or '<p class="mut">No setups today.</p>'}
<div class="cols"><section><h2>Headlines</h2><ul>{news_html}</ul></section>
<section><h2>Sector flows · 5d / 20d</h2><table>{sec_html}</table>
<h2>US economic calendar</h2><table>{cal_html or '<tr><td class="mut">nothing major</td></tr>'}</table>
<h2>Earnings next 5 days</h2><table>{earn_html}</table></section></div>
<h2>Track record · live picks</h2>{track_html}<h2>Rules</h2><div class="rules">Confidence = this exact setup's win rate in a 2-year backtest of S&amp;P 500+400 stocks, recomputed daily (survivorship bias, no costs: real results are lower).
Correlated picks are one bet: risk at most 1-2% of the account across all of them. Never hold through earnings (filtered out here). Not financial advice.</div>
</main></body></html>'''

if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    r = build()
    open(OUT, "w").write(render(r))
    # picks.json: committed by the workflow, read by the Claude research routine (its sandbox can't reach Yahoo)
    json.dump(dict(asof=r["asof"], picks=[{k: v for k, v in p.items() if k != "ai"} for p in r["picks"]],
                   macro=r["macro"], sectors=r["sectors"], news=r["news"], cal=[(str(w), *x) for w, *x in r["cal"]]),
              open(os.path.join(desk.HERE, "picks.json"), "w"), indent=1, default=str)
    print(OUT, f"{sum(p['kind'] == 'SIGNAL' for p in r['picks'])} signals, {len(r['picks'])} rows, {len(r['news'])} headlines")
