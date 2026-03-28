# supabase_client.py
"""Shared Supabase client — single source of truth for headers and helpers."""
import requests
from typing import Optional
from config import SUPABASE_URL, SUPABASE_KEY

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def sb_get(path: str) -> list:
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=HEADERS, timeout=10)
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[supabase] GET {path}: {e}")
        return []


def sb_post(table: str, data: dict) -> Optional[dict]:
    try:
        r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=HEADERS, json=data, timeout=10)
        result = r.json()
        if isinstance(result, list) and result:
            return result[0]
        return result if isinstance(result, dict) else None
    except Exception as e:
        print(f"[supabase] POST {table}: {e}")
        return None


def sb_patch(table: str, row_id: str, data: dict) -> Optional[dict]:
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{row_id}",
            headers=HEADERS, json=data, timeout=10
        )
        result = r.json()
        if isinstance(result, list) and result:
            return result[0]
        return result if isinstance(result, dict) else None
    except Exception as e:
        print(f"[supabase] PATCH {table}/{row_id}: {e}")
        return None
