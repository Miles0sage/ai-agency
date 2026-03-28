"""Discord webhook notifications for task events."""
import os
import requests
from typing import Optional

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


def notify_task_complete(
    task_id: str,
    title: str,
    status: str,
    confidence: float = 0.0,
    cost_usd: float = 0.0,
    department: str = "",
):
    """Send task completion notification to Discord."""
    if not DISCORD_WEBHOOK_URL:
        return

    color = 0x00ff41 if status == "completed" else 0xff4141 if status == "failed" else 0xffaa00

    embed = {
        "title": f"{'✅' if status == 'completed' else '❌' if status == 'failed' else '⚠️'} {title}",
        "description": f"**Status:** {status}\n**Department:** {department}\n**Confidence:** {confidence:.0%}\n**Cost:** ${cost_usd:.4f}",
        "color": color,
        "footer": {"text": f"Task {task_id[:8]} | AI Factory v2"},
    }

    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=5,
        )
    except Exception:
        pass  # Discord notifications are best-effort


def notify_system_event(message: str):
    """Send system event to Discord (startup, shutdown, errors)."""
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": f"🏭 **AI Factory:** {message}"},
            timeout=5,
        )
    except Exception:
        pass
