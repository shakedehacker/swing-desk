Write ai.json for a weekly swing-trading desk (US stocks, holds 3-6 days). Be efficient: batch independent tool calls in parallel, never repeat a search, keep reasoning short.

STRATEGY: the desk buys strong stocks after sharp drops and sells the bounce. The edge holds when the drop is sector-wide or macro fear. It fails on company-specific damage: guidance cut, earnings miss, fraud/accounting, regulatory action, lawsuit, share offering, CEO exit, downgrade on fundamentals.

1. Read picks.json. Research only picks whose "ed" is null or greater than 6 (others are already vetoed for earnings by code; skip them entirely).
2. Per researched ticker, budget: at most 2 WebSearch + 2 WebFetch. Find: why it fell in the last 10 days (check dates), company damage vs sector/macro fear, events in the next 7 trading days, analyst changes in the last 7 days. Run the first searches for all tickers in one parallel batch.
3. Market note: at most 2 WebSearch on overnight drivers (futures, Asia/Europe, oil, yields, data and earnings due today).
4. Write ai.json:
{"asof": "<picks.json asof>", "model": "Claude", "generated_utc": "<now ISO>",
 "note": {"mood": "risk-on|risk-off|mixed", "bullets": ["4 bullets, max 30 words each"]},
 "picks": {"<TICKER>": {"verdict": "CONFIRM|CAUTION|VETO", "cause": "sector|macro|company|unclear", "why": "max 45 words, factual", "catalysts": "next 7 trading days or 'none found'", "flags": ["max 3"], "sources": [{"title": "...", "url": "..."}]}}}
VETO = company damage explains the drop. CAUTION = unclear, mixed, or a major risk event in the hold window. CONFIRM = sector/macro drop, no company damage. Sources: 2-3 URLs you opened. Never invent facts, dates or URLs; if research fails, CAUTION with why 'research unavailable'.
5. Check with one python3 command that ai.json parses and asof matches. Reply only: "done".
