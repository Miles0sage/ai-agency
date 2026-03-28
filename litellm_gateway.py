"""Unified LLM gateway via LiteLLM. Routes to cheapest capable model per task type."""
import re
import os
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import litellm
from litellm import completion as litellm_completion
from config import MODEL_ROUTING, DEPARTMENTS
from kill_switch import should_exit

litellm.suppress_debug_info = True
litellm.set_verbose = False


def strip_thinking_tags(text: str) -> str:
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)


def get_model_for_task(task_type: str) -> str:
    dept = DEPARTMENTS.get(task_type)
    route_key = dept["route"] if dept else "default"
    route = MODEL_ROUTING.get(route_key, MODEL_ROUTING["default"])
    return route["model"]


def get_cost_for_model(model: str) -> tuple:
    for route in MODEL_ROUTING.values():
        if route["model"] == model:
            return route["cost_input"], route["cost_output"]
    return 0.028, 0.42


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((litellm.RateLimitError, litellm.Timeout)),
    reraise=True,
)
def _completion_with_retry(**kwargs):
    if should_exit():
        raise InterruptedError("Shutdown requested")
    return litellm_completion(**kwargs)


DASHSCOPE_BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
MINIMAX_BASE = "https://api.minimax.io/v1"


def _get_provider_kwargs(model: str) -> dict:
    """Return extra kwargs for providers that need custom api_base."""
    if model.startswith("dashscope/"):
        real_model = model.replace("dashscope/", "")
        return {
            "model": f"openai/{real_model}",
            "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
            "api_base": DASHSCOPE_BASE,
        }
    if model.startswith("minimax/"):
        real_model = model.replace("minimax/", "")
        return {
            "model": f"openai/{real_model}",
            "api_key": os.environ.get("MINIMAX_API_KEY", ""),
            "api_base": MINIMAX_BASE,
        }
    return {"model": model}


def call_llm(
    prompt: str,
    system: str = "You are a helpful assistant.",
    task_type: str = "default",
    model_override: Optional[str] = None,
    max_tokens: int = 2000,
    temperature: float = 0.3,
) -> dict:
    model = model_override or get_model_for_task(task_type)
    provider_kwargs = _get_provider_kwargs(model)
    try:
        response = _completion_with_retry(
            **provider_kwargs,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = response.choices[0].message.content or ""
        content = strip_thinking_tags(content).strip()
        prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        completion_tokens = response.usage.completion_tokens if response.usage else 0
        cost_in, cost_out = get_cost_for_model(model)
        cost_usd = (prompt_tokens * cost_in / 1_000_000) + (completion_tokens * cost_out / 1_000_000)
        return {
            "success": True, "output": content, "error": "", "model": model,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "cost_usd": round(cost_usd, 6),
        }
    except Exception as e:
        return {
            "success": False, "output": "", "error": str(e), "model": model,
            "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
        }
