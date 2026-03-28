# browser_agent.py
"""
Web browsing capability for AI Agency agents.
Uses browser-use library (85k stars) for vision-based web automation.
Agents can browse, click, extract data, fill forms.
"""
import asyncio
import os
from typing import Optional


async def browse_and_extract(
    task: str,
    url: Optional[str] = None,
    max_steps: int = 10,
) -> dict:
    """
    Use browser-use to complete a web browsing task.

    Args:
        task: Natural language description of what to do
        url: Optional starting URL
        max_steps: Maximum browser actions before stopping

    Returns: {success, output, error, steps_taken}
    """
    try:
        from browser_use import Agent, Browser, BrowserConfig
        from langchain_openai import ChatOpenAI

        # Use whatever LLM is available -- prefer cheap models for browsing
        llm = ChatOpenAI(
            model="gpt-4o-mini",  # cheap, fast, good at visual tasks
            api_key=os.environ.get("OPENAI_API_KEY", ""),
        )

        browser_config = BrowserConfig(
            headless=True,  # no GUI needed on server
        )
        browser = Browser(config=browser_config)

        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            max_actions_per_step=3,
        )

        result = await agent.run(max_steps=max_steps)

        # Extract final result
        output = ""
        if result and hasattr(result, 'final_result'):
            output = str(result.final_result)
        elif result:
            output = str(result)

        return {
            "success": True,
            "output": output[:5000],
            "error": "",
            "steps_taken": max_steps,
        }

    except ImportError:
        return {
            "success": False,
            "output": "",
            "error": "browser-use not installed. Run: pip install browser-use",
            "steps_taken": 0,
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e)[:500],
            "steps_taken": 0,
        }


def browse_sync(task: str, url: Optional[str] = None, max_steps: int = 10) -> dict:
    """Synchronous wrapper for browse_and_extract."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context -- create new loop in thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, browse_and_extract(task, url, max_steps))
                return future.result(timeout=300)
        else:
            return loop.run_until_complete(browse_and_extract(task, url, max_steps))
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e)[:500],
            "steps_taken": 0,
        }


# Simple web fetch fallback (no browser needed, just HTTP)
def web_fetch(url: str, extract_text: bool = True) -> dict:
    """Simple HTTP fetch -- fallback when browser-use isn't needed."""
    import requests
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "AI-Factory/2.0"})
        if extract_text:
            # Strip HTML tags for clean text
            import re
            text = re.sub(r'<[^>]+>', ' ', r.text)
            text = re.sub(r'\s+', ' ', text).strip()
            return {"success": True, "output": text[:5000], "error": "", "url": url}
        return {"success": True, "output": r.text[:5000], "error": "", "url": url}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)[:500], "url": url}
