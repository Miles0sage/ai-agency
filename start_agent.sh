#!/bin/bash
# start_agent.sh — Launch a persistent Claude Code agent session in tmux
# Survives MacBook closure. Re-attach any time with: tmux attach -t claude-agent
#
# Usage:
#   ./start_agent.sh          # start fresh session
#   ./start_agent.sh attach   # attach to existing session
#   ./start_agent.sh kill     # kill session

SESSION="claude-agent"
ENV_FILE="/root/.env.agency"

# ── Load env vars ──────────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
fi

# ── Commands ───────────────────────────────────────────────────────────────────
case "${1:-start}" in
  attach)
    tmux attach -t "$SESSION" 2>/dev/null || echo "No session '$SESSION' running. Use: ./start_agent.sh"
    exit 0
    ;;
  kill)
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "Session killed."
    exit 0
    ;;
  status)
    tmux ls 2>/dev/null | grep "$SESSION" || echo "No session running."
    exit 0
    ;;
esac

# ── Check if already running ───────────────────────────────────────────────────
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session '$SESSION' already running. Attaching..."
  tmux attach -t "$SESSION"
  exit 0
fi

# ── Bootstrap env file if missing ─────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  echo "⚠️  No env file at $ENV_FILE — creating template..."
  cat > "$ENV_FILE" << 'ENVEOF'
# AI Agency environment — fill in your keys
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
GROQ_API_KEY=
DISCORD_WEBHOOK_URL=
GITHUB_REPO=Miles0sage/ai-agency
SELFHEAL_ENABLED=true
RATE_LIMIT_PER_MIN=30
DAILY_BUDGET_ALERT_USD=1.00
ENVEOF
  echo "Edit $ENV_FILE then re-run."
  exit 1
fi

# ── Start tmux session ─────────────────────────────────────────────────────────
echo "Starting persistent Claude Code agent session..."

tmux new-session -d -s "$SESSION" -x 220 -y 50

# Pane 0: Claude Code agent (wakes up on demand via watchdog)
tmux send-keys -t "$SESSION:0" "cd /root/ai-agency && source $ENV_FILE 2>/dev/null; echo '🤖 Claude Code agent ready. Watchdog will wake me when tasks fail.'; echo 'Commands: claude -p \"prompt\" | python3 api.py | ./start_agent.sh status'" Enter

# Split — Pane 1: live task monitor
tmux split-window -v -t "$SESSION:0" -l 15
tmux send-keys -t "$SESSION:0.1" "watch -n 10 'curl -s https://web-production-7100d.up.railway.app/tasks?limit=5 | python3 -c \"import json,sys; [print(t[\\\"status\\\"][:12], t[\\\"id\\\"][:8], t[\\\"title\\\"][:55]) for t in json.load(sys.stdin)]\"'" Enter

tmux select-pane -t "$SESSION:0.0"

echo ""
echo "✅ Session started: tmux attach -t $SESSION"
echo ""
echo "Pane layout:"
echo "  Top  — Claude Code agent (ready for watchdog triggers)"
echo "  Bottom — Live task monitor (refreshes every 10s)"
echo ""
tmux attach -t "$SESSION"
