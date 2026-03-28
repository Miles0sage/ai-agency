"""Centralized configuration. All env vars read here, nowhere else."""
import os

# ── Supabase ──
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    import warnings
    warnings.warn("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set. API will start but tasks will fail.")

# ── LiteLLM Model Routing Table ──
# Active routing — uses MiniMax (only working key right now)
# TODO: Restore multi-provider routing once API keys are set:
#   default:     deepseek/deepseek-chat ($0.028/M) — cheapest workhorse
#   coding:      openrouter/moonshot/kimi-k2.5 ($0.445/M) — 76.8% SWE-bench
#   boilerplate: dashscope/qwen-turbo ($0.05/M)
#   research:    gemini/gemini-2.0-flash ($0.10/M, 1M context)
#   fast:        groq/llama-3.1-8b-instant ($0.05/M, 840 TPS)
MODEL_ROUTING = {
    "default":     {"model": "minimax/MiniMax-M2.7", "cost_input": 0.15, "cost_output": 1.20},
    "coding":      {"model": "minimax/MiniMax-M2.7", "cost_input": 0.15, "cost_output": 1.20},
    "boilerplate": {"model": "minimax/MiniMax-M2.7", "cost_input": 0.15, "cost_output": 1.20},
    "research":    {"model": "minimax/MiniMax-M2.7", "cost_input": 0.15, "cost_output": 1.20},
    "fast":        {"model": "minimax/MiniMax-M2.7", "cost_input": 0.15, "cost_output": 1.20},
}

# ── Model Fallback Chains ──
# If primary model fails, try fallbacks in order
MODEL_FALLBACKS = {
    "minimax/MiniMax-M2.7": ["dashscope/qwen-turbo", "groq/llama-3.1-8b-instant"],
    "deepseek/deepseek-chat": ["minimax/MiniMax-M2.7", "groq/llama-3.1-8b-instant"],
    "openrouter/moonshot/kimi-k2.5": ["minimax/MiniMax-M2.7", "deepseek/deepseek-chat"],
    "gemini/gemini-2.0-flash": ["minimax/MiniMax-M2.7", "deepseek/deepseek-chat"],
    "groq/llama-3.1-8b-instant": ["minimax/MiniMax-M2.7", "dashscope/qwen-turbo"],
}

# ── Budget ──
MAX_TASK_BUDGET_USD = float(os.environ.get("MAX_TASK_BUDGET_USD", "0.10"))

# ── Watchdog ──
WATCHDOG_POLL_INTERVAL = int(os.environ.get("WATCHDOG_POLL_INTERVAL", "30"))
STUCK_TIMEOUT_SECONDS = int(os.environ.get("STUCK_TIMEOUT_SECONDS", "180"))

# ── Browser ──
ENABLE_BROWSER = os.environ.get("ENABLE_BROWSER", "false").lower() == "true"

# ── Redis / Celery ──
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# ── Worker ──
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "20"))
MAX_RETRIES = int(os.environ.get("MAX_STAGE_RETRIES", "3"))
MAX_SUBTASKS = int(os.environ.get("MAX_DECOMPOSED_SUBTASKS", "5"))

# ── SOP ──
SOP_STAGES = ["requirements", "plan", "execute", "verify", "deliver"]

# ── Departments ──
DEPARTMENTS = {
    "coding": {
        "name": "Engineering",
        "system": "You are a senior software engineer. Write clean, tested, production-ready code.",
        "route": "coding",
    },
    "research": {
        "name": "Research",
        "system": "You are a research analyst. Provide thorough analysis with actionable insights.",
        "route": "research",
    },
    "writing": {
        "name": "Writing",
        "system": "You are a professional writer. Produce clear, engaging content.",
        "route": "default",
    },
    "qa": {
        "name": "QA",
        "system": "You are a QA engineer. Find bugs, edge cases, provide test cases.",
        "route": "default",
    },
    "marketing": {
        "name": "Marketing",
        "system": "You are a growth marketer. Write compelling copy that drives conversions.",
        "route": "default",
    },
}
