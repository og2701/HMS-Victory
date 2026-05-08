"""Thin async wrapper around the Gemini generateContent REST API."""

import asyncio
import logging
import os

import aiohttp

log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_TOKEN")
GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)


async def gemini_generate(
    session,
    system_prompt,
    user_parts,
    temperature=0.4,
    max_output_tokens=600,
    timeout=90,
):
    """
    Call Gemini and return (text, error).

    user_parts is a list of dicts in Gemini API shape, e.g.
    [{"text": "..."}, {"inline_data": {"mime_type": "image/png", "data": "<base64>"}}]
    """
    if not GEMINI_API_KEY:
        return None, "GEMINI_TOKEN not configured"

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": user_parts}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        },
    }

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            body = await resp.json()
            if resp.status != 200:
                return None, f"HTTP {resp.status}: {body}"
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return None, f"request failed: {exc}"
    finally:
        if own_session:
            await session.close()

    try:
        return body["candidates"][0]["content"]["parts"][0]["text"].strip(), None
    except (KeyError, IndexError, TypeError):
        return None, f"unexpected response: {body}"
