#!/usr/bin/env python3
"""Claude's own account: trades the desk's Golden/Solid picks at Alpaca (paper first), long only, stocks only.

  python3 trader.py morning   # 07:30 Israel, after report.py: session stops, overdue exits, new entries
  python3 trader.py watch     # every 5 min: fills, target and day-6 exits; after the close, the daily summary
  python3 trader.py status    # print account, positions, ledger
  python3 trader.py setup     # asks for the Alpaca keys (hidden input), writes .alpaca, checks they work

Keys: .alpaca (gitignored), lines KEY=..., SECRET=..., BASE=https://paper-api.alpaca.markets
Kill switch: create ~/trading/STOP  -> no new entries, open entry orders cancelled (stops stay).
Fractional orders at Alpaca are DAY only and can't be brackets, so the stop is re-placed every session
(server-side, survives a sleeping Mac) and the target / time exits are handled by `watch`.
"""
import os, sys, json, time, html, datetime as dt, urllib.request, urllib.error
from zoneinfo import ZoneInfo
import desk, report

HERE = desk.HERE
LEDGER = os.path.join(HERE, "trader.json")
STOP = os.path.join(HERE, "STOP")
OUT = os.path.join(HERE, "site", "trader.html")
ET, IL = ZoneInfo("America/New_York"), ZoneInfo("Asia/Jerusalem")
MAX_POS = 2          # open positions at once
MAX_FRAC = 0.5       # of equity per position
MIN_ORDER = 5.0      # USD; smaller orders are noise
WEEK_STOP = 0.85     # no new entries once equity < 85% of the week's starting equity
HOLD = 6             # trading days, as on the page

def cfg():
    return dict(l.strip().split("=", 1) for l in open(os.path.join(HERE, ".alpaca")) if "=" in l)

def api(method, path, body=None, data=False):
    c = cfg()
    base = "https://data.alpaca.markets" if data else c.get("BASE", "https://paper-api.alpaca.markets")
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"APCA-API-KEY-ID": c["KEY"], "APCA-API-SECRET-KEY": c["SECRET"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            t = r.read()
            return json.loads(t) if t else {}
    except urllib.error.HTTPError as e:
        return {"error": e.code, "message": e.read().decode()[:300]}

def push(title, body, prio="default"):
    tf = os.path.join(HERE, ".ntfy_topic")
    if not os.path.exists(tf): return
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{open(tf).read().strip()}", data=body.encode(),
                               headers={"Title": title.encode("ascii", "ignore").decode(), "Priority": prio, "Tags": "robot",
                                        "Click": "https://swing-desk-five.vercel.app/trader.html"}), timeout=15)
    except Exception as e: print("push failed", e)

def load():
    return json.load(open(LEDGER)) if os.path.exists(LEDGER) else dict(positions={}, closed=[], log=[], equity=[], week={}, summary="")

def save(L): json.dump(L, open(LEDGER, "w"), indent=1)

def log(L, msg):
    L["log"].append([dt.datetime.now(IL).strftime("%Y-%m-%d %H:%M"), msg]); L["log"] = L["log"][-300:]; print(msg)

def today_et(): return dt.datetime.now(ET).date()

def held_days(entry):
    """Trading days from the entry session to today, entry day = 1 (holidays ignored)."""
    d, n, t = dt.date.fromisoformat(entry), 1, today_et()
    while d < t:
        d += dt.timedelta(1)
        if d.weekday() < 5: n += 1
    return n

def last_price(sym):
    r = api("GET", f"/v2/stocks/{sym}/trades/latest?feed=iex", data=True)
    return (r.get("trade") or {}).get("p")

def open_orders(sym=None):
    o = api("GET", "/v2/orders?status=open&limit=200")
    return [x for x in o if not sym or x["symbol"] == sym] if isinstance(o, list) else []

def order(sym, side, qty, type_, **px):
    body = dict(symbol=sym, side=side, qty=str(round(qty, 6)), type=type_, time_in_force="day", **{k: str(round(v, 2)) for k, v in px.items()})
    return api("POST", "/v2/orders", body)

def sell_now(L, sym, p, reason):
    """Cancel the stop, then a marketable limit sell (never a market order)."""
    for o in open_orders(sym):
        if o["side"] == "sell": api("DELETE", f"/v2/orders/{o['id']}")
    time.sleep(2)
    px = last_price(sym) or p["entry"]
    r = order(sym, "sell", p["qty"], "limit", limit_price=px * 0.995)
    if "error" in r: log(L, f"{sym}: {reason} sell rejected: {r['message']}"); return
    p.update(status="exiting", reason=reason, exit_order=r["id"]); log(L, f"{sym}: {reason} exit, limit ${px * 0.995:,.2f}")

def reconcile(L):
    """Broker positions must match the ledger; a stranger means stop trading until a human looks."""
    pos = {x["symbol"]: x for x in api("GET", "/v2/positions") or [] if isinstance(x, dict)}
    for sym, p in list(L["positions"].items()):
        if p["status"] == "entry":
            o = api("GET", f"/v2/orders/{p['entry_order']}")
            if o.get("status") == "filled":
                p.update(status="open", entry=float(o["filled_avg_price"]), qty=float(o["filled_qty"]), entry_date=today_et().isoformat())
                log(L, f"{sym}: bought {p['qty']:g} @ ${p['entry']:,.2f}"); push(f"Bought {sym}", f"{p['qty']:g} sh @ ${p['entry']:,.2f}\nTarget ${p['tgt']:,.2f} · stop ${p['stop']:,.2f}")
            elif o.get("status") in ("canceled", "expired", "rejected"):
                log(L, f"{sym}: entry {o.get('status')}, not filled"); del L["positions"][sym]
        elif sym not in pos:  # gone: stop hit, target/time sell filled
            sells = [o for o in api("GET", f"/v2/orders?status=closed&symbols={sym}&limit=20") or [] if isinstance(o, dict) and o["side"] == "sell" and o["status"] == "filled"]
            px = float(sells[0]["filled_avg_price"]) if sells else None
            why = p.get("reason") or "stop"
            ret = (px / p["entry"] - 1) * 100 if px else None
            L["closed"].append(dict(sym=sym, tier=p["tier"], entry_date=p.get("entry_date"), exit_date=today_et().isoformat(), entry=p["entry"],
                                    exit=px, qty=p["qty"], pnl=(px - p["entry"]) * p["qty"] if px else None, ret=ret, reason=why))
            log(L, f"{sym}: closed ({why}) {'' if ret is None else f'{ret:+.2f}%'}")
            push(f"Sold {sym} ({why})", f"{'' if ret is None else f'{ret:+.2f}%, ${(px - p['entry']) * p['qty']:+,.2f}'}")
            del L["positions"][sym]
    strangers = set(pos) - set(L["positions"])
    if strangers: log(L, f"HALT: positions not in ledger: {sorted(strangers)}"); push("Trader halted", f"Unknown positions {sorted(strangers)}", "high")
    return not strangers

def morning():
    L = load()
    clock = api("GET", "/v2/clock")
    if "error" in clock: return print("alpaca error", clock)
    if dt.datetime.fromisoformat(clock["next_open"]).astimezone(ET).date() != today_et(): return print("no session today")
    ok = reconcile(L)
    acct = api("GET", "/v2/account")
    eq = float(acct["equity"])
    wk = today_et().isocalendar()[:2]
    if L["week"].get("id") != list(wk): L["week"] = dict(id=list(wk), equity=eq)
    # protect what we hold: today's stop (DAY orders placed pre-market work for the coming session), or exit if overdue
    for sym, p in L["positions"].items():
        if p["status"] != "open": continue
        if held_days(p["entry_date"]) > HOLD: sell_now(L, sym, p, "time"); continue
        if not any(o["side"] == "sell" for o in open_orders(sym)):
            r = order(sym, "sell", p["qty"], "stop", stop_price=p["stop"])
            log(L, f"{sym}: stop for today ${p['stop']:,.2f}" + (f" REJECTED {r['message']}" if "error" in r else ""))
    # new entries
    reasons = []
    if os.path.exists(STOP): reasons.append("kill switch")
    if not ok: reasons.append("ledger mismatch")
    if eq < WEEK_STOP * L["week"]["equity"]: reasons.append(f"weekly loss stop ({eq:,.2f} < {WEEK_STOP:.0%} of {L['week']['equity']:,.2f})")
    picks = json.load(open(os.path.join(HERE, "picks.json")))
    ai = json.load(open(os.path.join(HERE, "ai.json"))) if os.path.exists(os.path.join(HERE, "ai.json")) else {}
    verdicts = (ai.get("picks") or {}) if ai.get("asof") == picks["asof"] else {}
    cands = [p for p in picks["picks"] if p["kind"] == "SIGNAL" and p.get("tier") in ("golden", "solid") and p["t"] not in L["positions"]
             and (verdicts.get(p["t"]) or {}).get("verdict") != "VETO" and not (p.get("ed") is not None and p["ed"] <= 7)]
    cands.sort(key=lambda p: (p["tier"] != "golden", (verdicts.get(p["t"]) or {}).get("verdict") == "CAUTION", -(p["conf"] or 0)))
    cash = min(float(acct["cash"]), float(acct.get("non_marginable_buying_power") or acct["cash"]))
    for p in cands[:max(0, MAX_POS - len(L["positions"]))] if not reasons else []:
        notional = min(cash, eq * MAX_FRAC)
        if notional < MIN_ORDER: reasons.append("not enough settled cash"); break
        r = order(p["t"], "buy", notional / p["buy"], "limit", limit_price=p["buy"])
        if "error" in r: log(L, f"{p['t']}: entry rejected {r['message']}"); continue
        cash -= notional
        L["positions"][p["t"]] = dict(status="entry", entry_order=r["id"], tier=p["tier"], conf=p["conf"], tgt=p["tgt"], stop=p["stop"],
                                      entry=p["buy"], qty=notional / p["buy"], asof=picks["asof"],
                                      verdict=(verdicts.get(p["t"]) or {}).get("verdict"))
        log(L, f"{p['t']}: {p['tier']} entry limit ${p['buy']:,.2f} for ${notional:,.2f} (conf {p['conf']:.1f})")
    if reasons: log(L, "no new entries: " + "; ".join(reasons))
    elif not cands: log(L, "no qualifying picks today")
    save(L); render(L, acct)

def watch():
    L = load()
    clock = api("GET", "/v2/clock")
    if "error" in clock: return
    now = dt.datetime.now(ET)
    if os.path.exists(STOP):
        for sym, p in list(L["positions"].items()):
            if p["status"] == "entry": api("DELETE", f"/v2/orders/{p['entry_order']}"); log(L, f"{sym}: entry cancelled (kill switch)"); del L["positions"][sym]
    if not clock["is_open"]:
        if now.hour >= 16 and L["summary"] != now.date().isoformat() and now.weekday() < 5:
            reconcile(L); summary(L)
        return save(L)
    changed = len(L["log"])
    reconcile(L)
    for sym, p in L["positions"].items():
        if p["status"] != "open": continue
        px = last_price(sym)
        if px and px >= p["tgt"]: sell_now(L, sym, p, "target")
        elif held_days(p["entry_date"]) >= HOLD and (now.hour, now.minute) >= (15, 50): sell_now(L, sym, p, "time")
    save(L)
    if len(L["log"]) != changed: render(L, api("GET", "/v2/account"), deploy=True)

def summary(L):
    acct = api("GET", "/v2/account")
    eq, last = float(acct["equity"]), float(acct.get("last_equity") or acct["equity"])
    day = now_il = dt.datetime.now(IL).strftime("%Y-%m-%d")
    L["equity"].append([today_et().isoformat(), eq]); L["summary"] = dt.datetime.now(ET).date().isoformat()
    acts = [m for t, m in L["log"] if t.startswith(day) or t.startswith((dt.datetime.now(IL) - dt.timedelta(1)).strftime("%Y-%m-%d"))][-8:]
    held = ", ".join(f"{s} ({held_days(p['entry_date'])}d)" if p["status"] == "open" else f"{s} ({p['status']})" for s, p in L["positions"].items()) or "none"
    push(f"Trader day: ${eq:,.2f} ({eq - last:+,.2f})", f"Equity ${eq:,.2f}, today {eq - last:+,.2f}\nHolding: {held}\n" + "\n".join(acts[-5:]))
    log(L, f"summary: equity ${eq:,.2f} ({eq - last:+,.2f} today)")
    render(L, acct, deploy=True)

def render(L, acct, deploy=False):
    E = html.escape
    eq = float(acct.get("equity", 0)); cash = float(acct.get("cash", 0)); start = L["equity"][0][1] if L["equity"] else eq
    mode = "PAPER" if "paper" in cfg().get("BASE", "paper") else "LIVE"
    done = [c for c in L["closed"] if c["ret"] is not None]
    pos = "".join(f'<tr><td><b>{E(s)}</b> <span class="tier {p["tier"]}">{p["tier"].title()}</span></td><td>{E(p["status"])}</td>'
                  f'<td class="num">{p["qty"]:.4g}</td><td class="num">${p["entry"]:,.2f}</td><td class="num up">${p["tgt"]:,.2f}</td>'
                  f'<td class="num dn">${p["stop"]:,.2f}</td><td class="num">{held_days(p["entry_date"]) if p.get("entry_date") else "-"}</td></tr>'
                  for s, p in L["positions"].items()) or '<tr><td class="mut">No positions</td></tr>'
    closed = "".join(f'<tr><td>{E(c["exit_date"])}</td><td><b>{E(c["sym"])}</b></td><td>{E(c["reason"])}</td>'
                     f'<td class="num {report.sign(c["ret"] or 0)}">{report.fmt_ret(c["ret"])}</td>'
                     f'<td class="num {report.sign(c["pnl"] or 0)}">{"" if c["pnl"] is None else f"${c["pnl"]:+,.2f}"}</td></tr>' for c in reversed(L["closed"][-30:]))
    pts = [v for _, v in L["equity"][-60:]] or [eq]
    lo, hi = min(pts), max(pts)
    spark = " ".join(f"{i / max(1, len(pts) - 1) * 300:.1f},{60 - (v - lo) / ((hi - lo) or 1) * 56 - 2:.1f}" for i, v in enumerate(pts))
    logs = "".join(f'<li><span class="mut">{E(t)}</span> {E(m)}</li>' for t, m in reversed(L["log"][-25:]))
    stat = (f'<b>{len(done)}</b> closed · <b>{sum(c["ret"] > 0 for c in done) / len(done) * 100:.0f}%</b> won · '
            f'<b>${sum(c["pnl"] or 0 for c in done):+,.2f}</b> realised' if done else "No closed trades yet.")
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="apple-touch-icon" href="apple-touch-icon.png"><link rel="icon" type="image/png" href="favicon.png"><meta name="apple-mobile-web-app-title" content="Swing Desk"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black"><meta name="theme-color" content="#0b0e13">'
            '<title>Swing Desk Trader</title><meta name="robots" content="noindex">'
            f'<style>{report.CSS}.spark{{width:100%;height:64px}}.logl{{list-style:none;padding:0;margin:0;font-size:14px}}.logl li{{padding:6px 0;border-top:1px solid var(--line)}}</style></head><body><main>'
            f'{report.nav("trader")}'
            f'<header><h1>CLAUDE\'S <b>ACCOUNT</b></h1><span class="mut">{mode} · updated {dt.datetime.now(IL):%a %d %b, %H:%M} Israel</span></header>'
            f'<div class="hero"><b>${eq:,.2f}</b><p class="mut">{(eq / start - 1) * 100 if start else 0:+.2f}% since start (${start:,.2f}) · cash ${cash:,.2f} · '
            f'{len(L["positions"])} of {MAX_POS} slots used{" · KILL SWITCH ON" if os.path.exists(STOP) else ""}</p>'
            f'<svg class="spark" viewBox="0 0 300 60" preserveAspectRatio="none"><polyline fill="none" stroke="var(--acc)" stroke-width="2" points="{spark}"/></svg></div>'
            f'<section class="panel"><h2>Positions</h2><table><tr class="mut"><td>Stock</td><td>Status</td><td class="num">Shares</td><td class="num">Entry</td>'
            f'<td class="num">Target</td><td class="num">Stop</td><td class="num">Day</td></tr>{pos}</table></section>'
            f'<section class="panel"><h2>Closed trades</h2><p>{stat}</p><table>{closed}</table></section>'
            f'<section class="panel"><h2>Activity</h2><ul class="logl">{logs}</ul></section>'
            '<p class="mut" style="margin-top:28px">Rules: only the desk\'s Golden/Solid picks, long only, no margin, options, shorts or crypto. '
            f'Max {MAX_POS} positions, max {MAX_FRAC:.0%} of equity each, new entries pause after a 15% weekly drawdown. Stops sit at the broker every session.</p>'
            '</main></body></html>')
    open(OUT, "w").write(page)
    if deploy:
        os.system(f'cd "{HERE}/site" && (npx vercel deploy --prod --yes >/dev/null 2>&1 || NODE_OPTIONS="--require {HERE}/dnsfix.js" npx vercel deploy --prod --yes >/dev/null 2>&1)')

def status():
    L = load(); a = api("GET", "/v2/account")
    print({k: a.get(k) for k in ("status", "equity", "cash", "buying_power", "non_marginable_buying_power", "daytrade_count", "pattern_day_trader")})
    print("broker positions:", [(x["symbol"], x["qty"]) for x in api("GET", "/v2/positions") or []])
    print("ledger:", json.dumps(L["positions"], indent=1))

def setup():
    import getpass
    key = input("Alpaca API Key ID: ").strip()
    secret = getpass.getpass("Alpaca Secret Key (hidden): ").strip()
    live = input("Live money account? Type LIVE for live, Enter for paper: ").strip() == "LIVE"
    base = "https://api.alpaca.markets" if live else "https://paper-api.alpaca.markets"
    f = os.path.join(HERE, ".alpaca")
    with open(os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as o:
        o.write(f"KEY={key}\nSECRET={secret}\nBASE={base}\n")
    a = api("GET", "/v2/account")
    if "error" in a: os.remove(f); sys.exit(f"Keys rejected ({a['error']}), nothing saved. Copy them again and rerun setup.")
    print(f"OK: {'LIVE' if live else 'paper'} account, status {a['status']}, equity ${float(a['equity']):,.2f}")
    if not live and float(a["equity"]) > 1000: print("Equity is not $60 yet: reset the paper account to $60 in the Alpaca dashboard, then run status.")

if __name__ == "__main__":
    {"morning": morning, "watch": watch, "status": status, "setup": setup}[sys.argv[1]]()
