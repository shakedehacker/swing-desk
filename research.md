Write ai.json for a weekly swing-trading desk (US stocks, holds 3-6 days). Be efficient: batch independent tool calls in parallel, never repeat a search, keep reasoning short.

STRATEGY: the desk buys strong stocks after sharp drops and sells the bounce. The edge holds when the drop is sector-wide or macro fear. It fails on company-specific damage: guidance cut, earnings miss, fraud/accounting, regulatory action, lawsuit, share offering, CEO exit, downgrade on fundamentals.

You run the desk like a trading firm (TradingAgents): analysts gather facts, a bull and a bear argue, a risk manager decides.

1. Read picks.json. Research only picks whose "ed" is null or greater than 6 (others are already vetoed for earnings by code; skip them entirely).
   Each pick already carries desk data from code; use it, do not re-search it: "rvol" (last-day volume vs 20-day average; 3+ usually means company news), "dd.moves" (analyst rating changes, last 14 days), "dd.short" (short % of float), "dd.imove" (options-implied % move to exit), "dd.ratings" [buy, hold, sell].
2. News analyst, per ticker: at most 2 WebSearch + 2 WebFetch. Find why it fell in the last 10 days (check dates), company damage vs sector/macro fear, events in the next 7 trading days, analyst changes. Run the first searches for all tickers in one parallel batch.
3. Sentiment (Bloomberg method): score each relevant story from the last 3 days -1..+1 for the stock, with a confidence 0..1 that it is about this company. Company sentiment = confidence-weighted average; sent_conf = average confidence.
4. Bull vs bear, per ticker: one sentence each, strongest factual case for the bounce and against it. Then risk manager: verdict.
5. Market note: at most 2 WebSearch on overnight drivers (futures, Asia/Europe, oil, yields, data and earnings due today).
6. Write ai.json:
{"asof": "<picks.json asof>", "model": "Claude", "generated_utc": "<now ISO>",
 "note": {"mood": "risk-on|risk-off|mixed", "bullets": ["4 bullets, max 30 words each"]},
 "picks": {"<TICKER>": {"verdict": "CONFIRM|CAUTION|VETO", "cause": "sector|macro|company|unclear", "why": "max 45 words, factual",
   "sentiment": -1.0..1.0, "sent_conf": 0..1, "bull": "max 25 words", "bear": "max 25 words",
   "catalysts": "next 7 trading days or 'none found'", "flags": ["max 3"], "sources": [{"title": "...", "url": "..."}]}}}
VETO = company damage explains the drop (a downgrade in dd.moves aimed at this company alone counts; a sector-wide call does not). CAUTION = unclear, mixed, rvol 3+ without a found cause, or a major risk event in the hold window. CONFIRM = sector/macro drop, no company damage. Sources: 2-3 URLs you opened. Never invent facts, dates or URLs; if research fails, CAUTION with why 'research unavailable' and omit sentiment.
7. Check with one python3 command that ai.json parses and asof matches. Reply only: "done".
