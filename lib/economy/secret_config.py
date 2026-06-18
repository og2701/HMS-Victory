"""Secret-tier badges, kept OUT of the open source.

The secret badges' names/icons and their trigger constants live in an encrypted blob
(``secret_badges.json.enc``, committed) and are decrypted at runtime with the
``BADGE_SECRET_KEY`` environment variable (set in the systemd unit, like the Discord token).
The plaintext ``secret_badges.json`` is gitignored and never committed - so reading the open
repo, or feeding it to an AI, reveals nothing about what the secret badges are or how to earn
them. (Descriptions are deliberately kept as "[REDACTED]" even to holders.)

Absent a valid key the secret badges simply don't load: they won't seed and won't be awarded
(``badges()`` -> ``[]``, ``bid()``/``param()`` -> ``None``), so the open repo still runs - just
without the secret set.

Crypto is stdlib-only (no third-party dependency): PBKDF2-HMAC-SHA256 key derivation, an
HMAC-SHA256 keystream in CTR mode, and encrypt-then-MAC with HMAC-SHA256.
"""
import hashlib
import hmac
import json
import logging
import os

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ENC_FILE = os.path.join(_ROOT, "secret_badges.json.enc")
_ITERATIONS = 200_000
_cache = None


def _xor_ctr(enc_key: bytes, nonce: bytes, data: bytes) -> bytes:
    """XOR ``data`` with an HMAC-SHA256(enc_key, nonce||counter) keystream (CTR mode)."""
    out = bytearray(len(data))
    counter = 0
    for i in range(0, len(data), 32):
        block = hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[i:i + 32]
        for j in range(len(chunk)):
            out[i + j] = chunk[j] ^ block[j]
        counter += 1
    return bytes(out)


def _derive(password: bytes, salt: bytes):
    dk = hashlib.pbkdf2_hmac("sha256", password, salt, _ITERATIONS, dklen=64)
    return dk[:32], dk[32:]      # (enc_key, mac_key)


def encrypt(plaintext: bytes, password: bytes) -> bytes:
    salt = os.urandom(16)
    nonce = os.urandom(16)
    enc_key, mac_key = _derive(password, salt)
    ct = _xor_ctr(enc_key, nonce, plaintext)
    mac = hmac.new(mac_key, salt + nonce + ct, hashlib.sha256).digest()
    return salt + nonce + ct + mac


def decrypt(blob: bytes, password: bytes) -> bytes:
    if len(blob) < 16 + 16 + 32:
        raise ValueError("blob too short")
    salt, nonce, rest = blob[:16], blob[16:32], blob[32:]
    ct, mac = rest[:-32], rest[-32:]
    enc_key, mac_key = _derive(password, salt)
    expected = hmac.new(mac_key, salt + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise ValueError("bad key or corrupt data")
    return _xor_ctr(enc_key, nonce, ct)


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    password = os.environ.get("BADGE_SECRET_KEY")
    if not password:
        logger.warning("BADGE_SECRET_KEY not set - secret badges are disabled.")
        return _cache
    try:
        with open(_ENC_FILE, "rb") as f:
            blob = f.read()
        _cache = json.loads(decrypt(blob, password.encode()))
    except FileNotFoundError:
        logger.warning("%s missing - secret badges are disabled.", _ENC_FILE)
    except Exception:
        logger.error("Could not decrypt secret badges (wrong BADGE_SECRET_KEY?) - disabled.",
                     exc_info=True)
    return _cache


def reload() -> dict:
    """Drop the cache and re-read (for tests / after rotating the key)."""
    global _cache
    _cache = None
    return _load()


def badges():
    """Seed rows for the secret badges: ``(id, name, '[REDACTED]', icon, 'Secret')``.
    Empty list if there's no valid key (so the open repo seeds nothing secret)."""
    return [(b["id"], b["name"], "[REDACTED]", b["icon"], "Secret")
            for b in _load().get("badges", [])]


def bid(key: str):
    """The real badge id for a neutral config key (e.g. ``'a6'`` -> ``'lucky_7s'``), or None."""
    for b in _load().get("badges", []):
        if b.get("key") == key:
            return b["id"]
    return None


def param(name: str):
    """A secret trigger constant by name (e.g. ``'lucky_balance'`` -> 777), or None."""
    return _load().get("params", {}).get(name)
