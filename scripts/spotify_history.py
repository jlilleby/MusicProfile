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
        return {"days": {}, "seen": {}, "polls": []}
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
    if not isinstance(state.get("days"), dict) or not isinstance(state.get("seen"), dict):
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
    """Build track-frequency input from encrypted DAILY aggregates.

    Never persist exact play timestamps, raw listening events, or plaintext song
    listening history. Played-at is used in memory solely for deduplication and
    assignment to a UTC day. A keyed HMAC identifier prevents duplicate counting.
    """
    import hmac
    now = dt.datetime.now(UTC)
    cutoff = now - dt.timedelta(days=days)
    retain_after = now - dt.timedelta(days=RETENTION_DAYS)
    state = _load()
    state.setdefault("polls", [])
    raw = _fetch(token, get, track_data)
    existing_days = state["days"]
    seen = state["seen"]
    for event in raw:
        played = _utc(event["played_at"])
        if played < retain_after:
            continue
        day = played.date().isoformat()
        uri = event["track"]["uri"]
        fingerprint = hmac.new(
            _key(), (event["played_at"] + "\x00" + uri).encode(), hashlib.sha256
        ).hexdigest()
        day_seen = seen.setdefault(day, [])
        if fingerprint in day_seen:
            continue
        day_seen.append(fingerprint)
        bucket = existing_days.setdefault(day, {})
        if uri not in bucket:
            bucket[uri] = {"track": event["track"], "count": 0}
        bucket[uri]["count"] += 1
    for day in list(existing_days):
        if dt.date.fromisoformat(day) < retain_after.date():
            del existing_days[day]
    for day in list(seen):
        if dt.date.fromisoformat(day) < retain_after.date():
            del seen[day]
    polls = sorted(set(p for p in state["polls"] if _utc(p) >= retain_after))
    if persist:
        polls.append(now.isoformat())
        state["polls"] = polls
        _save(state)
    recent = []
    for day, songs in existing_days.items():
        if dt.date.fromisoformat(day) < cutoff.date():
            continue
        for row in songs.values():
            recent.extend([row["track"]] * int(row["count"]))
    polling = sorted(_utc(p) for p in polls)
    in_window = [p for p in polling if p >= cutoff]
    coverage = bool(polling and polling[0] <= cutoff and in_window)
    if coverage:
        points = [max(p for p in polling if p <= cutoff)] + in_window + [now]
        coverage = all((later - earlier) <= dt.timedelta(minutes=90)
                       for earlier, later in zip(points, points[1:]))
    if persist:
        print(f"Saved encrypted daily aggregates; retrieved {len(raw)} Spotify events; "
              f"accumulated {len(recent)} plays in the rolling window; "
              f"poll continuity verified: {coverage}")
    return recent, coverage


def main():
    import spotify_refresh
    token = spotify_refresh.access_token()
    collect(token, spotify_refresh.get, spotify_refresh.track_data, persist=True)


if __name__ == "__main__":
    main()
