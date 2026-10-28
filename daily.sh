#!/bin/bash
# Daily desk run, started by launchd Mon-Fri 07:30 Israel time:
# market data -> Claude Opus research (ai.json) -> page -> Vercel -> git backup.
# A failed research step still publishes the page, just without AI verdicts.
set -uo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p logs
exec >>"logs/$(date +%F).log" 2>&1
echo "== start $(date)"

git pull -q --rebase || true
python3 report.py || { osascript -e 'display notification "Market data step failed, see logs" with title "Swing Desk"'; exit 1; }

# Lean run: no MCP servers, skills, user hooks or default system prompt (53K -> 9K tokens of context per turn).
# Measured 2026-10-21: ~240K tokens total vs ~7.2M for the first Opus+subagents version, same findings.
# Hard cap 15 min (a hung web fetch held 2026-10-28 for 48 min); on timeout the page publishes without AI.
perl -e 'alarm 900; exec @ARGV' claude -p "$(cat research.md)" --model sonnet --output-format json --permission-mode acceptEdits \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' --disable-slash-commands --setting-sources project \
  --tools "WebSearch,WebFetch,Read,Write,Bash" --allowedTools "WebSearch WebFetch Read Write Bash(python3:*)" \
  --system-prompt "You are a precise financial research assistant. Use tools efficiently. Never invent facts." \
  --max-turns 40 > logs/research.json \
  && python3 -c "import json;d=json.load(open('logs/research.json'));u=d['usage'];print('research turns',d.get('num_turns'),'tokens',sum(u.get(k,0) for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens','output_tokens')))" \
  || echo "research failed: publishing without AI"

python3 report.py --notify  # final render: push new Golden trades to the phone
[ -f .alpaca ] && python3 trader.py morning  # Claude's own account: stops for the session, exits, new entries
cd site
url=$(npx vercel deploy --prod --yes || NODE_OPTIONS="--require $PWD/../dnsfix.js" npx vercel deploy --prod --yes)
# promote explicitly: after any manual promote/rollback Vercel stops auto-assigning the production domain
url=$(echo "$url" | grep -oE 'https://swing-desk-[a-z0-9]+-[a-z0-9-]+\.vercel\.app' | head -1)
[ -n "$url" ] && { npx vercel promote "$url" --yes || NODE_OPTIONS="--require $PWD/../dnsfix.js" npx vercel promote "$url" --yes; }
cd ..
asof=$(python3 -c "import json;print(json.load(open('picks.json'))['asof'])")
code=$(curl -s https://swing-desk-five.vercel.app/ | grep -q "prices as of $asof" && echo "fresh $asof" || echo "STALE: live page is not $asof")
echo "live: $code"

git add ai.json history.json picks.json && git commit -qm "desk $(date +%F)" && git push -q || true
osascript -e "display notification \"Desk updated (site $code)\" with title \"Swing Desk\""
echo "== done $(date)"
