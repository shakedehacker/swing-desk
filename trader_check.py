"""Self-check for trader.py against a fake broker: entry -> fill -> stop -> target exit -> closed P&L. Run: python3 trader_check.py"""
import json, os, tempfile, datetime as dt
import trader

tmp = tempfile.mkdtemp()
trader.LEDGER, trader.OUT, trader.STOP = f"{tmp}/l.json", f"{tmp}/t.html", f"{tmp}/STOP"
trader.cfg = lambda: {"BASE": "https://paper-api.alpaca.markets", "KEY": "x", "SECRET": "y"}
trader.push = lambda *a, **k: None
trader.time.sleep = lambda s: None
trader.in_window = lambda now: True
trader.os.system = lambda c: 0  # never deploy from the check
today = trader.today_et().isoformat()
B = dict(orders={}, pos={}, cash=60.0, px=100.0, open=False, n=0)

def fake(method, path, body=None, data=False):
    if data: return {"trade": {"p": B["px"]}}
    if path == "/v2/clock":
        nxt = dt.datetime.now(trader.ET).replace(hour=9, minute=30)
        return {"is_open": B["open"], "next_open": nxt.isoformat()}
    if path == "/v2/account":
        eq = B["cash"] + sum(q * B["px"] for q in B["pos"].values())
        return {"equity": str(eq), "cash": str(B["cash"]), "non_marginable_buying_power": str(B["cash"]), "last_equity": "60"}
    if path == "/v2/positions": return [{"symbol": s, "qty": str(q)} for s, q in B["pos"].items()]
    if method == "POST":
        B["n"] += 1; oid = f"o{B['n']}"; B["orders"][oid] = dict(body, id=oid, status="new"); return B["orders"][oid]
    if method == "DELETE": B["orders"][path.split("/")[-1]]["status"] = "canceled"; return {}
    if path.startswith("/v2/orders?status=open"): return [o for o in B["orders"].values() if o["status"] == "new"]
    if path.startswith("/v2/orders?status=closed"): return [o for o in B["orders"].values() if o["status"] == "filled"][::-1]
    if path.startswith("/v2/orders/"): return B["orders"][path.split("/")[-1]]
    raise AssertionError(path)
trader.api = fake

def fill(oid, px):
    o = B["orders"][oid]; q = float(o["qty"]); o.update(status="filled", filled_avg_price=str(px), filled_qty=str(q))
    if o["side"] == "buy": B["pos"][o["symbol"]] = q; B["cash"] -= q * px
    else: B["pos"].pop(o["symbol"]); B["cash"] += q * px

pick = dict(t="AAA", kind="SIGNAL", tier="golden", conf=8.2, buy=100.0, tgt=104.0, stop=90.0, ed=None)
real_open = open
def fake_open(p, *a, **k):
    if p.endswith("picks.json"): p = f"{tmp}/picks.json"
    if p.endswith("ai.json"): p = f"{tmp}/ai.json"
    return real_open(p, *a, **k)
json.dump(dict(asof=today, picks=[pick]), real_open(f"{tmp}/picks.json", "w"))
json.dump(dict(asof=today, picks={"AAA": {"verdict": "CONFIRM"}}), real_open(f"{tmp}/ai.json", "w"))
trader.open = fake_open

trader.morning()                                   # places the entry
L = trader.load(); p = L["positions"]["AAA"]
assert p["status"] == "entry" and abs(p["qty"] * 100 - 30) < 1e-6, p      # half of $60 equity
fill(p["entry_order"], 99.5)
B["open"] = True; trader.watch()                   # sees the fill
assert trader.load()["positions"]["AAA"]["status"] == "open"
trader.morning.__globals__["today_et"]             # (stop is placed by the next morning run)
B["open"] = False; trader.morning()
assert any(o["type"] == "stop" and o["status"] == "new" for o in B["orders"].values()), "session stop missing"
B["open"] = True; B["px"] = 104.5; trader.watch()  # target hit -> stop cancelled, limit sell
L = trader.load(); assert L["positions"]["AAA"]["status"] == "exiting"
assert not any(o["type"] == "stop" and o["status"] == "new" for o in B["orders"].values()), "stop not cancelled"
fill(L["positions"]["AAA"]["exit_order"], 104.0)
trader.watch()                                     # books the close
L = trader.load(); c = L["closed"][-1]
assert not L["positions"] and c["reason"] == "target" and c["ret"] > 4, c
open(trader.STOP, "w").close(); B["open"] = False; trader.morning()
assert "kill switch" in trader.load()["log"][-1][1]
print("trader ok:", f"{c['ret']:+.2f}%", f"${c['pnl']:+.2f}")
