#!/usr/bin/env python3
"""Collect recent Spotify listening into an encrypted rolling history.

The repository is public. Exact play events and timestamps NEVER appear in plaintext
files, workflow logs, or reports. Encryption uses the existing Spotify client secret;
rotating that secret requires migrating or resetting the encrypted history.
"""
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "listening-history.enc.json"
UTC = dt.timezone.utc
RETENTION_DAYS = 14


def _key():
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    if not secret:
        raise RuntimeError("SPOTIFY_CLIENT_SECRET is required to decrypt listening history")
    return hashlib.sha256(b"MusicProfile-listening-v1\x00" + secret.encode()).digest()


def _load():
    if not STATE_PATH.exists():
        return {"events": [], "polls": []}
    obj = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if obj.get("version") != 1:
        raise RuntimeError("Unknown encrypted listening-history format")
    try:
        plaintext = AESGCM(_key()).decrypt(
            base64.b64decode(obj["nonce"]),
            base64.b64decode(obj["ciphertext"]),
            b"MusicProfile-history-v1",
        )
    except Exception as exc:
        raise RuntimeError("Cannot decrypt history: was SPOTIFY_CLIENT_SECRET rotated? Preserve the old secret to recover it.") from exc
    state = json.loads(plaintext)
    if not isinstance(state.get("events"), list) or not isinstance(state.get("polls"), list):
        raise RuntimeError("Invalid decrypted history")
    return state


def _save(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(12)
    encrypted = AESGCM(_key()).encrypt(
        nonce,
        json.dumps(state, separators=(",", ":"), ensure_ascii=False).encode(),
        b"MusicProfile-history-v1",
    )
    obj = {
        "version": 1,
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(encrypted).decode(),
    }
    STATE_PATH.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _utc(timestamp):
    return dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(UTC)


def _fetch(token, get, track_data):
    """Read available Spotify pages, handling providers that return only 50 total."""
    before = int(dt.datetime.now(UTC).timestamp() * 1000) + 1
    found = []
    seen = set()
    for _ in range(12):
        page = get(token, "/me/player/recently-played", limit=50, before=before)
        rows = page.get("items") or []
        if not rows:
            break
        oldest = before
        new = 0
        for row in rows:
            played_at = row.get("played_at")
            if not played_at:
                continue
            ms = int(_utc(played_at).timestamp() * 1000)
            oldest = min(oldest, ms)
            track = track_data(row.get("track"))
            if track and (played_at, track["uri"]) not in seen:
                found.append({"played_at": played_at, "track": track})
                seen.add((played_at, track["uri"]))
                new += 1
        if len(rows) < 50 or oldest >= before or new == 0:
            break
        before = oldest
    return found


def collect(token, get, track_data, persist=False, days=7):
    """Return (track events for last days, coverage_verified).

    Persist only in apply/collector runs. A seven-day interval is considered
    covered only if polling spans the period without a >90-minute gap.
    """
    now = dt.datetime.now(UTC)
    cutoff = now - dt.timedelta(days=days)
    retain_after = now - dt.timedelta(days=RETENTION_DAYS)
    state = _load()
    old_count = len(state["events"])
    current = _fetch(token, get, track_data)
    by_key = {}
    for event in state["events"] + current:
        if _utc(event["played_at"]) >= retain_after:
            by_key[(event["played_at"], event["track"]["uri"])] = event
    events = sorted(by_key.values(), key=lambda r: r["played_at"])
    polls = sorted(set(p for p in state["polls"] if _utc(p) >= retain_after))
    if persist:
        polls.append(now.isoformat())
        state = {"events": events, "polls": polls}
        _save(state)
    recent = [event["track"] for event in events if _utc(event["played_at"]) >= cutoff]
    polling = sorted(_utc(p) for p in polls)
    in_window = [p for p in polling if p >= cutoff]
    coverage = bool(polling and polling[0] <= cutoff and in_window)
    if coverage:
        points = [max(p for p in polling if p <= cutoff)] + in_window + [now]
        coverage = all((b - a) <= dt.timedelta(minutes=90) for a, b in zip(points, points[1:]))
    if persist:
        print(f"Encrypted collection saved: {len(current)} fetched, {len(events) - old_count} net retained events, {len(recent)} events in last {days} days; continuous coverage: {coverage}")
    return recent, coverage


def main():
    import spotify_refresh
    token = spotify_refresh.access_token()
    collect(token, spotify_refresh.get, spotify_refresh.track_data, persist=True)


if __name__ == "__main__":
    main()
