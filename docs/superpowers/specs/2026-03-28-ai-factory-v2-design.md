# AI Factory v2 + Agency — Design Spec

**Date:** 2026-03-28
**Status:** Approved (brainstorming complete, 28 sources in NotebookLM)

## Vision

Build the world's first "AI Fiverr" with outcome-based fixed pricing. Clients submit work → autonomous AI agents research, execute, verify, deliver — 24/7, at $0.001–$0.10/task cost vs competitors charging $2–$30/task.

## Current System

- `agency.py` — 748 LOC, FastAPI on Railway, Supabase queue
- 5-stage SOP pipeline: requirements → plan → execute → verify → deliver
- Thompson Sampling bandit routing across Alibaba/MiniMax models
- Multi-department: coding, research, writing, qa, marketing
- Known issue: tasks stuck `in_progress`, no watchdog, no sandbox, no web browsing

## Architecture — 4 Phases

### Phase 1: Reliability (kill switch + watchdog + routing)
1. **Watchdog service** — port OpenHands `StuckDetector` (400 LOC, 5 loop heuristics)
2. **Kill switch** — `stop_if_should_exit()` global flag + Celery `revoke(signal=SIGKILL)`
3. **Budget enforcement** — `BudgetExhaustedError` per-task dollar cap
4. **LiteLLM gateway** — replace hardcoded DashScope/MiniMax with unified router
5. **DeepSeek V3.2** as default workhorse ($0.028/M cached)

### Phase 2: Capabilities (sandbox + browser + tools)
6. **E2B sandboxes** — agent outside, code execution inside Firecracker microVM
7. **browser-use** — web browsing for research agents (parallel async instances)
8. **Composio** — 500+ tool integrations (GitHub, Slack, Linear, Gmail)

### Phase 3: Memory + Collaboration
9. **pgvector on Supabase** — scoped namespaces per agent + shared namespace
10. **Scoped context checkpoints** — summarize between SOP stages
11. **A2A agent cards** — agents advertise capabilities, hire each other, max 3 hops

### Phase 4: Dashboard + Launch
12. **Cyberpunk dashboard** — live task feed, SOP viz, model routing viz, cost tracker
13. **AgentOps** — 2-line observability, session replays, cost tracking
14. **Fixed-price intake** — client submits work, gets deliverable

## Safety Architecture (non-negotiable)

```
Layer 1: NeMo Guardrails — wraps every LLM call, blocks jailbreaks
Layer 2: StuckDetector — 5 heuristics, catches loops before they burn budget
Layer 3: BudgetExhaustedError — hard dollar cap per task
Layer 4: Celery revoke(signal=SIGKILL) — hard process kill
Layer 5: E2B sandbox — Firecracker VM isolation, agent can't escape
Layer 6: stop_if_should_exit() — SIGTERM kills all retries globally
Layer 7: Tripwired Rust sidecar — catastrophic escape: kill -9
```

## Model Routing (via LiteLLM)

| Task | Model | $/M input | Provider |
|------|-------|-----------|----------|
| Default workhorse | DeepSeek V3.2 cached | $0.028 | DeepSeek |
| Complex coding | Kimi K2.5 | $0.445 | Moonshot/OpenRouter |
| Boilerplate/CRUD | Qwen Turbo | $0.050 | Alibaba |
| Long docs/RAG | Gemini 2.0 Flash | $0.100 | Google |
| Real-time/fast | Groq Llama 8B | $0.050 | Groq |

## Open Source Stack

| Component | Tool | Stars | License |
|-----------|------|-------|---------|
| Task queue | Celery + Redis | 25k | BSD |
| Sandbox | E2B | — | Apache 2.0 |
| Browser | browser-use | 85k | MIT |
| Integrations | Composio | 15k | MIT (SDK) |
| Observability | AgentOps | 3k | MIT |
| Guardrails | NeMo Guardrails | 4.5k | Apache 2.0 |
| Kill switch | Tripwired | — | Apache 2.0 |
| Model gateway | LiteLLM | — | MIT |
| Loop detection | OpenHands StuckDetector | 45k | MIT |
| Sandbox mgmt | DeerFlow AioSandboxProvider | 51k | Apache 2.0 |

## Patterns Ported

| Pattern | Source | LOC |
|---------|--------|-----|
| StuckDetector (5 heuristics) | OpenHands | ~400 |
| AioSandboxProvider (warm pool) | DeerFlow | ~400 |
| SubagentExecutor (timeout) | DeerFlow | ~400 |
| stop_if_should_exit() | OpenHands | ~20 |
| Metrics + BudgetExhaustedError | OpenHands | ~100 |
| Self-healing (error→diagnosis→retry) | Figaro | ~200 |

## Pricing Strategy

- Competitors: Devin $2.25/ACU, Manus $10-30/task
- Our cost: $0.001–$0.10/task
- Client price: $1–5/guaranteed deliverable
- Margin: 95%+

## Success Criteria

1. Zero stuck tasks (watchdog catches within 3 minutes)
2. All code execution sandboxed (no host filesystem access)
3. Hard kill works within 5 seconds at any layer
4. Budget never exceeded (reserve-commit pattern)
5. End-to-end task completion in <15 minutes for standard tasks
6. Cost per task <$0.10 for 90% of tasks
