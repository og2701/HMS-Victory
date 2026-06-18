"""Encrypt secret_badges.json -> secret_badges.json.enc (the committed, opaque blob).

Usage:
    # encrypt with an existing key (e.g. the one already in systemd):
    BADGE_SECRET_KEY=... python3 tools/encrypt_secret_badges.py

    # or mint a fresh key and print it once (then put it in the systemd unit):
    python3 tools/encrypt_secret_badges.py --new-key

The .enc is safe to commit. The key must live ONLY in the systemd unit
(Environment=BADGE_SECRET_KEY=...) and never in the repo. After editing secret_badges.json,
re-run this and commit the regenerated .enc.
"""
import base64
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.economy import secret_config  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "secret_badges.json")
DST = os.path.join(ROOT, "secret_badges.json.enc")

key = os.environ.get("BADGE_SECRET_KEY")
if "--new-key" in sys.argv or not key:
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    print("Generated BADGE_SECRET_KEY (add to the systemd unit, keep it private):\n  "
          + key + "\n")

with open(SRC, "rb") as f:
    plaintext = f.read()
json.loads(plaintext)                       # fail early if the source isn't valid JSON

blob = secret_config.encrypt(plaintext, key.encode())
with open(DST, "wb") as f:
    f.write(blob)

assert json.loads(secret_config.decrypt(blob, key.encode())) == json.loads(plaintext)
print(f"Wrote {DST} ({len(blob)} bytes). Round-trip verified.")
