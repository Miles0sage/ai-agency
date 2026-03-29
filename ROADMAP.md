# AI Agency — Roadmap & Guidance

> Living document. Updated automatically by watchdog issues and manual sessions.
> **Current version:** 0.5.1 | **Stack:** FastAPI + Supabase + LiteLLM + Railway

---

## Current State (v0.5.1)

### What works
- 3 concurrent workers with atomic task claiming (no race conditions)
- DeepSeek V3 routing — 100% success rate, $0.007/task avg
- 5-stage SOP pipeline: requirements → plan → execute → verify → deliver
- Watchdog prevents stuck tasks (180s timeout, heartbeat per stage)
- Self-healing: Claude Code patches failures, creates GitHub issues, Discord alerts
- Rate limiting (30 req/min per IP) + optional API key auth
- Discord notifications: task complete, budget alerts, failure pings
- `/stats` endpoint: per-model cost breakdown

### Known gaps
- No persistent task memory between runs (episodic_memory.py disabled)
- Learning system disabled (was polluting prompts with stale data)
- No streaming — clients poll for results
- Single Railway instance — no horizontal scaling yet
- Webhook source field not stored in Supabase schema (minor)

---

## Roadmap

### Phase 1 — Reliability (now → 2 weeks)
**Goal: zero manual interventions needed**

- [ ] Re-enable learning system with scoped context (per task_type only)
- [ ] Add Supabase realtime subscription to watchdog (replace polling)
- [ ] Retry logic: auto-requeue failed tasks up to 2x with different model
- [ ] Add task timeout per type (coding=5min, research=3min, writing=2min)
- [ ] Health check cron: ping `/health` every 5min, alert if workers=0
- [ ] Add `source` column to Supabase tasks table

### Phase 2 — Intelligence (2–4 weeks)
**Goal: tasks get smarter over time**

- [ ] Re-enable episodic memory — store task outcomes, query similar past tasks
- [ ] Thompson Sampling bandit — route to best-performing model per task_type
- [ ] Context injection — pass relevant past results into new task prompts
- [ ] Subtask decomposition — break complex tasks into parallel subtasks
- [ ] Confidence scoring (Ralph Gate) — reject low-confidence outputs automatically

### Phase 3 — Scale (4–8 weeks)
**Goal: handle 100+ concurrent tasks**

- [ ] Move from threads to async workers (asyncio + Supabase realtime)
- [ ] Add Alibaba Qwen routing for boilerplate tasks ($0.001/task)
- [ ] Horizontal scaling on Railway (multiple instances, shared Supabase queue)
- [ ] Priority queue: critical tasks jump the queue
- [ ] Cost cap enforcement per task_type

### Phase 4 — Product (8+ weeks)
**Goal: usable by others**

- [ ] Web dashboard (live task grid, cost charts, model performance)
- [ ] API docs (OpenAPI auto-generated, hosted)
- [ ] Multi-tenant: API keys per user, cost tracking per key
- [ ] Webhook integrations: GitHub issues → auto-fix tasks, Slack slash commands
- [ ] Plugin system: custom task types with custom SOP stages

---

## Architecture

```
Client (curl / webhook / dashboard)
    │
    ▼
FastAPI (api.py) ── rate limit ── auth
    │
    ▼
Supabase tasks table (queue)
    │
    ▼
Worker Pool (agency.py) — 3 threads
    │  atomic CAS claim (sb_claim)
    │
    ▼
SOP Pipeline (5 stages)
    │  requirements → plan → execute → verify → deliver
    │  heartbeat per stage (prevents watchdog kill)
    │
    ▼
LiteLLM Gateway (litellm_gateway.py)
    │  DeepSeek V3 (default) → Groq fallback → MiniMax fallback
    │
    ▼
Supabase tasks table (result stored)
    │
    ├── Discord notification
    └── Self-heal watchdog (on failure)
            │
            ▼
        Claude Code CLI
            │  reads source, patches bug
            ▼
        git push → Railway auto-deploy
            │
            ▼
        GitHub issue created
```

---

## Working with the Agency

### Submit a task
```bash
curl -X POST https://web-production-7100d.up.railway.app/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "task name", "prompt": "full prompt", "task_type": "coding"}'
```

### Task types
| type | model | use for |
|------|-------|---------|
| `coding` | DeepSeek V3 | code, bugs, features |
| `research` | DeepSeek V3 | analysis, reports |
| `writing` | DeepSeek V3 | content, docs, copy |
| `boilerplate` | Groq Llama-70B | CRUD, scaffolding |
| `fast` | Groq Llama-8B | one-liners, quick answers |

### Monitor
```bash
# Live status
curl https://web-production-7100d.up.railway.app/tasks?limit=10

# Per-model stats
curl https://web-production-7100d.up.railway.app/stats

# Worker health
curl https://web-production-7100d.up.railway.app/debug
```

### Persistent agent session (survives MacBook closure)
```bash
# On the server — start once
cd /root/ai-agency && ./start_agent.sh

# Re-attach any time
./start_agent.sh attach

# Watchdog auto-triggers: claude -p "..." for failures
```

### Env vars on Railway
| var | purpose | default |
|-----|---------|---------|
| `DISCORD_WEBHOOK_URL` | task + budget alerts | off |
| `API_KEY` | lock down write endpoints | off (open) |
| `SELFHEAL_ENABLED` | auto-patch on failure | true |
| `DAILY_BUDGET_ALERT_USD` | spend threshold | 1.00 |
| `WORKER_COUNT` | parallel workers | 3 |
| `RATE_LIMIT_PER_MIN` | per-IP rate limit | 30 |

---

## Self-Healing Loop

When a task fails:
1. Watchdog polls Supabase every 60s for `status=failed`
2. Calls `claude -p` with error + task context
3. Claude reads source files, diagnoses root cause
4. If code bug: patches file, `git push`, Railway auto-deploys
5. `gh issue create` → GitHub issue with error + fix
6. Discord ping with issue link

**What Claude can fix autonomously:**
- Missing imports
- Wrong field names in Supabase queries
- Model routing errors
- Config key mismatches
- Simple logic bugs

**What needs human review (manual):**
- Schema migrations
- API key rotation
- Architecture changes
- External service outages

---

## Cost Reference (RSMeans 2024 rates)

| model | input | output | typical task cost |
|-------|-------|--------|-------------------|
| DeepSeek V3 | $0.27/M | $1.10/M | ~$0.007 |
| Groq Llama-70B | $0.59/M | $0.79/M | ~$0.004 |
| Groq Llama-8B | $0.05/M | $0.08/M | ~$0.0005 |
| MiniMax M2.7 | $0.30/M | $1.10/M | ~$0.008 |

**Daily budget alert:** $1.00 (configurable)
**Per-task cap:** $0.10

---

## Commit History Highlights

| commit | description |
|--------|-------------|
| `4f8b9f5` | Self-healing watchdog with Claude Code patching |
| `879b16c` | Version bump 0.5.1 |
| `0ca8ec4` | Production hardening: rate limiting, API auth, budget alerts |
| `84f2326` | Fixed missing `import os` — recovered all 3 workers |
