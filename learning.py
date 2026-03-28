"""
Self-improving learning system.

Records task outcomes to Supabase and builds context from past successes
to improve future executions.
"""
from datetime import datetime, timezone
from typing import Optional

from supabase_client import sb_get, sb_post


def record_outcome(
    supabase_url: str,
    supabase_key: str,
    task_type: str,
    prompt_summary: str,
    model_used: str,
    confidence: float,
    cost_usd: float,
    success: bool,
    output_preview: str = "",
) -> Optional[dict]:
    """Record a task outcome to the agency_learning table for future context.

    Accepts supabase_url and supabase_key params for backward compat,
    but uses the shared supabase_client internally.
    """
    try:
        return sb_post("agency_learning", {
            "task_type": task_type,
            "prompt_summary": prompt_summary[:500],
            "model_used": model_used,
            "confidence": round(confidence, 4),
            "cost_usd": round(cost_usd, 6),
            "success": success,
            "output_preview": output_preview[:500],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        return None


def get_past_successes(
    supabase_url: str,
    supabase_key: str,
    task_type: str,
    limit: int = 5,
) -> list:
    """Fetch recent successful outcomes for a task_type from agency_learning.

    Accepts supabase_url and supabase_key params for backward compat,
    but uses the shared supabase_client internally.
    """
    try:
        return sb_get(
            f"agency_learning?task_type=eq.{task_type}&success=eq.true"
            f"&order=created_at.desc&limit={limit}"
            f"&select=prompt_summary,model_used,confidence,output_preview"
        )
    except Exception:
        return []


def get_best_model_from_history(
    supabase_url: str,
    supabase_key: str,
    task_type: str,
) -> Optional[str]:
    """
    Return the model with the highest average confidence from past successes.
    Returns None if no history exists.
    """
    rows = get_past_successes(supabase_url, supabase_key, task_type, limit=20)
    if not rows:
        return None

    # Aggregate confidence per model
    model_scores: dict[str, list[float]] = {}
    for row in rows:
        model = row.get("model_used", "unknown")
        conf = row.get("confidence", 0.0)
        model_scores.setdefault(model, []).append(conf)

    # Pick model with highest average confidence
    best_model = None
    best_avg = -1.0
    for model, scores in model_scores.items():
        avg = sum(scores) / len(scores)
        if avg > best_avg:
            best_avg = avg
            best_model = model

    return best_model


def build_context_from_history(
    supabase_url: str,
    supabase_key: str,
    task_type: str,
    limit: int = 3,
) -> str:
    """
    Fetch recent successful outcomes for this task_type and build
    a context string to prepend to execute prompts.
    """
    rows = get_past_successes(supabase_url, supabase_key, task_type, limit=limit)
    if not rows:
        return ""

    parts = ["## Learning from past successful outcomes\n"]
    for i, row in enumerate(rows, 1):
        parts.append(
            f"### Example {i} (confidence={row.get('confidence', 0):.0%}, "
            f"model={row.get('model_used', 'unknown')})\n"
            f"Task: {row.get('prompt_summary', '')[:200]}\n"
            f"Output: {row.get('output_preview', '')[:200]}\n"
        )
    return "\n".join(parts)
