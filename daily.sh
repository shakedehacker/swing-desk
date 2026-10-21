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
claude -p "$(cat research.md)" --model sonnet --output-format json --permission-mode acceptEdits \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' --disable-slash-commands --setting-sources project \
  --tools "WebSearch,WebFetch,Read,Write,Bash" --allowedTools "WebSearch WebFetch Read Write Bash(python3:*)" \
  --system-prompt "You are a precise financial research assistant. Use tools efficiently. Never invent facts." \
  --max-turns 40 > logs/research.json \
  && python3 -c "import json;d=json.load(open('logs/research.json'));u=d['usage'];print('research turns',d.get('num_turns'),'tokens',sum(u.get(k,0) for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens','output_tokens')))" \
  || echo "research failed: publishing without AI"

python3 report.py
cd site
npx vercel deploy --prod --yes || NODE_OPTIONS="--require $PWD/../dnsfix.js" npx vercel deploy --prod --yes
cd ..
code=$(curl -s -o /dev/null -w '%{http_code}' https://swing-desk-five.vercel.app/)
echo "live: $code"

git add ai.json history.json picks.json && git commit -qm "desk $(date +%F)" && git push -q || true
osascript -e "display notification \"Desk updated (site $code)\" with title \"Swing Desk\""
echo "== done $(date)"
