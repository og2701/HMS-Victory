"""Who is playing: Discord sign-in, the activity instance check, and our own session tokens.

1. The page asks Discord to authorise it and gets a one-time code. We swap that code for an
   access token here, with the app's client secret (which never leaves this server), and
   ask Discord whose token it is. That user id is the same one the UKPence ledger uses.
2. We ask Discord, with the app's bot token, about the activity instance the page says it
   is in: which channel it was launched in and who is in it. A page can claim anything; this
   answer comes from Discord, so the channel allow-list can't be talked around.
3. We hand back a session token: the user id and channel, signed with a secret only this
   server knows. Every later request carries it, and it is the only thing that decides
   whose game a guess counts toward.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time

log = logging.getLogger(__name__)

API = "https://discord.com/api/v10"
SESSION_TTL = 7 * 24 * 3600      # remembered on the player's device so reopening skips Discord's slow sign-in


class AuthError(Exception):
    """A sign-in step failed; the message is safe to show the player."""


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def configured() -> bool:
    return all(_env(k) for k in ("ACTIVITIES_CLIENT_ID", "ACTIVITIES_CLIENT_SECRET",
                                 "ACTIVITIES_BOT_TOKEN", "ACTIVITIES_SESSION_SECRET"))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), payload, hashlib.sha256).digest())


def make_session(user_id: int, channel_id: int | None, *, secret: str | None = None,
                 now: float | None = None) -> str:
    secret = secret or _env("ACTIVITIES_SESSION_SECRET")
    body = json.dumps({"uid": str(user_id), "ch": str(channel_id or ""),
                       "exp": int((now or time.time()) + SESSION_TTL)},
                      separators=(",", ":")).encode()
    return f"{_b64(body)}.{_sign(body, secret)}"


def read_session(token: str, *, secret: str | None = None, now: float | None = None) -> dict | None:
    """{'uid': int, 'ch': int|None} for a genuine, unexpired token, else None."""
    secret = secret or _env("ACTIVITIES_SESSION_SECRET")
    try:
        body_b64, sig = token.split(".", 1)
        body = _unb64(body_b64)
        if not hmac.compare_digest(sig, _sign(body, secret)):
            return None
        data = json.loads(body)
        if int(data["exp"]) < (now or time.time()):
            return None
        return {"uid": int(data["uid"]), "ch": int(data["ch"]) if data.get("ch") else None}
    except Exception:
        return None


async def exchange_code(session, code: str) -> str:
    """The access token for a one-time authorisation code from the page."""
    async with session.post(f"{API}/oauth2/token", data={
        "client_id": _env("ACTIVITIES_CLIENT_ID"),
        "client_secret": _env("ACTIVITIES_CLIENT_SECRET"),
        "grant_type": "authorization_code",
        "code": code,
    }) as r:
        data = await r.json(content_type=None)
        if r.status != 200 or "access_token" not in data:
            log.warning("activity code exchange failed: %s %s", r.status, data.get("error"))
            raise AuthError("Discord didn't accept the sign-in. Close the activity and open it again.")
        return data["access_token"]


async def fetch_user(session, access_token: str) -> dict:
    async with session.get(f"{API}/users/@me", headers={"Authorization": f"Bearer {access_token}"}) as r:
        if r.status != 200:
            raise AuthError("Couldn't read your Discord account. Try opening the activity again.")
        return await r.json()


async def fetch_instance(session, instance_id: str) -> dict | None:
    """Discord's own record of an activity instance: where it was launched and who is in it."""
    app_id = _env("ACTIVITIES_CLIENT_ID")
    async with session.get(f"{API}/applications/{app_id}/activity-instances/{instance_id}",
                           headers={"Authorization": f"Bot {_env('ACTIVITIES_BOT_TOKEN')}"}) as r:
        if r.status != 200:
            log.info("activity instance lookup %s -> %s", instance_id, r.status)
            return None
        return await r.json()


def instance_channel(instance: dict | None, user_id: int) -> int | None:
    """The channel an instance was launched in, if this user is genuinely in it."""
    if not instance or str(user_id) not in {str(u) for u in instance.get("users", [])}:
        return None
    ch = (instance.get("location") or {}).get("channel_id")
    return int(ch) if ch else None
