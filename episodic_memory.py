# episodic_memory.py
"""
Episodic memory with pgvector — find similar past tasks via embedding similarity.
Uses Supabase's pgvector extension for vector search.
Falls back gracefully if pgvector isn't enabled.
"""
import hashlib
import requests
from typing import Optional
from config import SUPABASE_URL, SUPABASE_KEY
from supabase_client import HEADERS


def _simple_embedding(text: str, dim: int = 384) -> list[float]:
    """
    Generate a simple deterministic embedding from text.
    NOT a real embedding model — uses character frequency + hash for consistency.
    Replace with a real embedding model (e.g., sentence-transformers) for production.
    """
    # Normalize and hash
    text = text.lower().strip()[:1000]
    vec = [0.0] * dim

    # Character frequency features
    for i, char in enumerate(text):
        idx = ord(char) % dim
        vec[idx] += 1.0 / (i + 1)

    # Hash-based spreading for better distribution
    for i in range(0, len(text), 3):
        chunk = text[i:i+3]
        h = int(hashlib.md5(chunk.encode()).hexdigest()[:8], 16)
        idx = h % dim
        vec[idx] += 0.1

    # Normalize to unit vector
    magnitude = sum(v * v for v in vec) ** 0.5
    if magnitude > 0:
        vec = [v / magnitude for v in vec]

    return vec


def store_episode(
    task_type: str,
    title: str,
    prompt_summary: str,
    output_summary: str,
    model_used: str,
    confidence: float,
    cost_usd: float,
    success: bool,
) -> Optional[str]:
    """Store a task episode with its embedding for future similarity search."""
    embedding = _simple_embedding(f"{title} {prompt_summary}")

    data = {
        "task_type": task_type,
        "title": title,
        "prompt_summary": prompt_summary[:500],
        "output_summary": output_summary[:500],
        "model_used": model_used,
        "confidence": round(confidence, 3),
        "cost_usd": round(cost_usd, 6),
        "success": success,
        "embedding": embedding,
    }

    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/agency_episodes",
            headers=HEADERS, json=data, timeout=10,
        )
        if r.ok:
            result = r.json()
            if isinstance(result, list) and result:
                return result[0].get("id")
        return None
    except Exception as e:
        print(f"[memory] store_episode: {e}")
        return None


def find_similar_episodes(
    query: str,
    task_type: Optional[str] = None,
    limit: int = 3,
    success_only: bool = True,
) -> list[dict]:
    """
    Find past episodes similar to the query using pgvector cosine similarity.
    Falls back to text-based search if pgvector RPC isn't available.
    """
    embedding = _simple_embedding(query)

    # Try pgvector RPC first
    rpc_data = {
        "query_embedding": embedding,
        "match_count": limit,
    }
    if task_type:
        rpc_data["filter_task_type"] = task_type

    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/match_episodes",
            headers=HEADERS, json=rpc_data, timeout=10,
        )
        if r.ok:
            results = r.json()
            if isinstance(results, list) and results:
                return results
    except Exception:
        pass

    # Fallback: simple text-based query via learning table
    try:
        query_params = f"success=eq.true&order=confidence.desc&limit={limit}"
        if task_type:
            query_params = f"task_type=eq.{task_type}&{query_params}"

        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/agency_learnings?{query_params}",
            headers=HEADERS, timeout=10,
        )
        if r.ok:
            return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        print(f"[memory] find_similar: {e}")

    return []


def build_memory_context(query: str, task_type: str) -> str:
    """Build a context string from similar past episodes to enhance prompts."""
    episodes = find_similar_episodes(query, task_type=task_type, limit=3)
    if not episodes:
        return ""

    parts = ["## Relevant past experiences:"]
    for ep in episodes:
        title = ep.get("title", ep.get("prompt_summary", ""))
        conf = ep.get("confidence", 0)
        output = ep.get("output_summary", ep.get("output_preview", ""))
        if title:
            parts.append(f"- **{title[:80]}** (confidence: {conf:.0%})")
            if output:
                parts.append(f"  Output hint: {output[:150]}")

    return "\n".join(parts) if len(parts) > 1 else ""
