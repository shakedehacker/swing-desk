"""AI layer: news check per pick + morning desk note. Free Groq models, stdlib HTTP.

Needs GROQ_API_KEY in the environment; without it every function returns None and the
report renders without AI. The model only sees headlines we fetched, never invents news.
"""
import os, json, re, urllib.request

URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]  # second model has its own free quota

def ask(prompt, max_tokens=900):
    key = os.environ.get("GROQ_API_KEY")
    if not key: return None
    for model in MODELS:
        try:
            body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                               "max_tokens": max_tokens, "temperature": 0}).encode()
            req = urllib.request.Request(URL, data=body, headers={"Authorization": f"Bearer {key}",
                                         "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            txt = json.load(urllib.request.urlopen(req, timeout=60))["choices"][0]["message"]["content"]
            return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        except Exception:
            continue
    return None

def check_pick(p, headlines):
    """Is the drop sector/macro fear (bounce setup) or company-specific damage (veto)?"""
    news = "\n".join(f"{i + 1}) {h}" for i, h in enumerate(headlines)) or "(no headlines found)"
    setup = (f"{p['t']} ({p['name']}) fell {p['drop']:.1f}% in 5 days while still above its 200-day average. "
             f"Plan: buy limit {p['buy']:.2f}, target {p['tgt']:.2f}, stop {p['stop']:.2f}, hold under a week."
             if p["kind"] == "SIGNAL" else
             f"{p['t']} ({p['name']}) is down {p['drop']:.1f}% in 5 days, on a watchlist for a possible dip-bounce trade.")
    r = ask(f"""You are a risk officer on a swing-trading desk. A mean-reversion strategy buys strong stocks after sharp drops.
Its edge holds when the drop is sector-wide or macro fear, and fails when the cause is company-specific damage:
guidance cut, earnings miss, fraud or accounting issue, regulatory action, lawsuit, share offering, CEO exit, or a fundamental downgrade.

{setup}

Recent headlines (the only evidence you may use):
{news}

Return only JSON: {{"verdict": "CONFIRM" or "CAUTION" or "VETO", "cause": "sector" or "macro" or "company" or "unclear",
"why": "max 30 words, cite headline numbers like (2)", "flags": ["short risk flags, max 3"]}}
VETO only for clear company-specific damage in the headlines. CAUTION if mixed or unclear. Never invent facts.""")
    if not r or r.get("verdict") not in ("CONFIRM", "CAUTION", "VETO"): return None
    r["flags"] = [str(f) for f in r.get("flags", [])][:3]
    return r

def desk_note(macro, sectors, headlines, calendar, picks):
    m = ", ".join(f"{n} {v:,.2f} ({c:+.2f}%)" for n, v, c in macro)
    s = ", ".join(f"{n} {a:+.1f}% 5d" for a, n, _ in sectors)
    h = "\n".join(f"- {t}" for t, *_ in headlines[:15])
    c = "\n".join(f"- {w:%a %H:%M} UTC {t} ({imp})" for w, t, imp, *_ in calendar[:6]) or "- none major"
    p = ", ".join(f"{x['t']} ({x['kind'].lower()})" for x in picks) or "none"
    r = ask(f"""You write the 9am note for a weekly swing trader (holds 3-6 days, buys sharp dips in strong stocks).
Use ONLY the data below. No price predictions, no invented facts, plain English.

Markets: {m}
Sector moves: {s}
Today's setups: {p}
Economic calendar:
{c}
Headlines:
{h}

Return only JSON: {{"mood": "risk-on" or "risk-off" or "mixed", "bullets": ["4 bullets, max 25 words each: what drives markets,
where money is flowing, today's key event or risk, what it means for dip-buying"]}}""", max_tokens=1200)
    if not r or not isinstance(r.get("bullets"), list): return None
    return r
