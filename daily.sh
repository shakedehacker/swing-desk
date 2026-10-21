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

claude -p "$(cat research.md)" --model opus --permission-mode acceptEdits \
  --allowedTools "WebSearch WebFetch Read Write Edit Agent Bash(python3:*)" \
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
