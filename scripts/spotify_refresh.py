#!/usr/bin/env python3
"""Conservative weekly Last.fm-based Spotify playlist refresher. Python stdlib only."""
import argparse
import base64
import collections
import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.spotify.com/v1"
OSLO = ZoneInfo("Europe/Oslo")
UTC = dt.timezone.utc


def call(url, method="GET", token=None, data=None, form=False):
    headers = {"Accept": "application/json", "User-Agent": "MusicProfile-Actions/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        payload = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
    else:
        payload = None
    for attempt in range(5):
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 4:
                delay = int(exc.headers.get("Retry-After", "2"))
                time.sleep(min(60, max(1, delay)))
                continue
            if exc.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            err = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Spotify API {method} {url.split('?')[0]} returned HTTP {exc.code}: {err}") from None
    raise RuntimeError("Spotify request failed after retries")


def access_token():
    credentials = [os.environ.get(x, "") for x in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN")]
    if not all(credentials):
        raise RuntimeError("Missing GitHub Secrets: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN")
    client_id, secret, refresh = credentials
    header = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh}).encode(),
        headers={"Authorization": "Basic " + header, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            payload = json.load(res)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Spotify token refresh failed (HTTP {exc.code}); check credentials and scopes") from None
    if payload.get("refresh_token") and payload["refresh_token"] != refresh:
        raise RuntimeError("Spotify rotated the refresh token. Update SPOTIFY_REFRESH_TOKEN securely before retrying.")
    return payload["access_token"]


def get(token, path, **params):
    q = "?" + urllib.parse.urlencode(params) if params else ""
    return call(API + path + q, token=token)


def pages(token, path, **params):
    url = API + path + "?" + urllib.parse.urlencode(params)
    while url:
        page = call(url, token=token)
        yield page
        url = page.get("next")


def track_data(raw):
    if not isinstance(raw, dict) or raw.get("type") != "track":
        return None
    uri = raw.get("uri")
    if not isinstance(uri, str) or not uri.startswith("spotify:track:"):
        return None
    return {
        "uri": uri,
        "name": raw.get("name") or "Unknown track",
        "artists": [x.get("name") for x in raw.get("artists", []) if x.get("name")],
        "artist_ids": [x.get("id") for x in raw.get("artists", []) if x.get("id")],
        "duration_ms": raw.get("duration_ms") or 0,
        "url": (raw.get("external_urls") or {}).get("spotify") or "",
    }


def load_playlist(token, pid, owner):
    meta = get(token, f"/playlists/{pid}")
    if (meta.get("owner") or {}).get("id") != owner:
        raise RuntimeError(f"Playlist {pid} is not owned by the authenticated account; refusing edits")
    rows = []
    for page in pages(token, f"/playlists/{pid}/items", limit=50):
        for rec in page.get("items", []):
            t = track_data(rec.get("item") or rec.get("track"))
            if t:
                rows.append(t)
    return meta, rows


def recent_listens(token, cutoff):
    # Cursor pagination backwards. The Spotify endpoint may limit history depth.
    observed = []
    before = int(dt.datetime.now(UTC).timestamp() * 1000) + 1
    reached_cutoff = False
    for _ in range(30):
        page = get(token, "/me/player/recently-played", limit=50, before=before)
        rows = page.get("items") or []
        if not rows:
            break
        oldest = before
        for row in rows:
            played = dt.datetime.fromisoformat(row["played_at"].replace("Z", "+00:00"))
            oldest = min(oldest, int(played.timestamp() * 1000))
            if played < cutoff:
                reached_cutoff = True
                continue
            item = track_data(row.get("track"))
            if item:
                observed.append(item)
        if reached_cutoff or len(rows) < 50 or oldest >= before:
            break
        before = oldest
    return observed, reached_cutoff


def propose(current, listens, max_count=7):
    """Add only proven affinity: repeated recent plays AND artists already in this playlist."""
    artist_ids = {aid for t in current for aid in t["artist_ids"]}
    known_uris = {t["uri"] for t in current}
    counts = collections.Counter(t["uri"] for t in listens)
    first = {}
    for t in listens:
        first.setdefault(t["uri"], t)
    candidates = [t for u, t in first.items()
                  if u not in known_uris and counts[u] >= 2
                  and any(aid in artist_ids for aid in t["artist_ids"])]
    candidates.sort(key=lambda t: (-counts[t["uri"]], t["name"], t["uri"]))
    return candidates[:max_count]


def snapshot(meta, tracks, today):
    return {
        "snapshot_date": today,
        "playlist_id": meta["id"],
        "playlist_uri": meta.get("uri"),
        "name": meta.get("name"),
        "track_count": len(tracks),
        "duration_ms": sum(t["duration_ms"] for t in tracks),
        "tracks": [{k: t[k] for k in ("uri", "name", "artists", "duration_ms", "url")} for t in tracks],
    }


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if not path.exists() or path.read_text() != content:
        path.write_text(content)


def save_snapshot_if_changed(path, value):
    """Preserve the prior snapshot date if track data did not change."""
    if path.exists():
        previous = json.loads(path.read_text())
        a, b = dict(previous), dict(value)
        a.pop("snapshot_date", None)
        b.pop("snapshot_date", None)
        if a == b:
            return False
    save_json(path, value)
    return True


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    today = dt.datetime.now(OSLO).date().isoformat()
    token = access_token()
    me = get(token, "/me")
    my_id = me["id"]
    configs = json.loads((ROOT / "config/playlists.json").read_text())
    available = []
    for page in pages(token, "/me/playlists", limit=50):
        available += page.get("items", [])
    from lastfm_history import fetch_scrobbles, resolve_spotify
    scrobble_counts, scrobble_names, source_stats = fetch_scrobbles(days=7)
    listens, mapping_stats = resolve_spotify(token, get, track_data, scrobble_counts, scrobble_names)
    source_stats.update(mapping_stats)
    print(f"Spotify authenticated. {len(listens)} resolved scrobble observations from Last.fm; no Spotify listening-history polling.")
    results = {}
    snapshots_changed = False
    for label, conf in configs.items():
        try:
            pid = conf.get("playlist_id", "")
            if not pid:
                matches = [p for p in available if (p.get("owner") or {}).get("id") == my_id
                           and conf["name_contains"].casefold() in p.get("name", "").casefold()]
                if len(matches) != 1:
                    raise RuntimeError(f"Found {len(matches)} matching owned playlists; set exact ID in config/playlists.json")
                pid = matches[0]["id"]
            meta, original = load_playlist(token, pid, my_id)
            if conf["name_contains"].casefold() not in meta.get("name", "").casefold():
                raise RuntimeError("Configured ID refers to a playlist with an unexpected name")
            suggestions = propose(original, listens)
            applied = []
            if args.apply and suggestions:
                # Only append, never delete. Re-read after mutation and verify exact changes.
                call(API + f"/playlists/{pid}/items", method="POST", token=token,
                     data={"uris": [t["uri"] for t in suggestions]})
                meta, after = load_playlist(token, pid, my_id)
                before_uris = {t["uri"] for t in original}
                after_uris = {t["uri"] for t in after}
                applied = [t for t in suggestions if t["uri"] in after_uris and t["uri"] not in before_uris]
                if len(applied) != len(suggestions):
                    raise RuntimeError("Post-write verification failed; do not trust additions. Inspect playlist manually")
            else:
                after = original
            if args.apply:
                snapshots_changed |= save_snapshot_if_changed(
                    ROOT / "playlists" / f"{label}.json", snapshot(meta, after, today))
            results[label] = {
                "name": meta.get("name"), "playlist_url": (meta.get("external_urls") or {}).get("spotify"),
                "before_count": len(original), "after_count": len(after),
                "duration_minutes": round(sum(t["duration_ms"] for t in after) / 60000, 1),
                "added": [{k: t[k] for k in ("uri", "name", "artists", "url")} for t in applied],
                "suggested": [{k: t[k] for k in ("uri", "name", "artists", "url")} for t in suggestions] if args.dry_run else [],
                "removed": [],
                "note": "Below 120 minutes; no safe matching candidates" if sum(t["duration_ms"] for t in after) < 7200000 else "",
            }
        except Exception as ex:
            results[label] = {"error": str(ex)}
            print(f"ERROR {label}: {ex}", file=sys.stderr)
    report = {
        "date": today, "mode": "apply" if args.apply else "dry-run",
        "listening_source": source_stats,
        "recent_events_observed": source_stats["scrobbles_observed"],
        "seven_day_coverage_verified": False,
        "playlists": results,
    }
    # Dry-run never creates artifacts or commits. No individual scrobble timestamps are persisted.
    if args.apply and snapshots_changed:
        # Only persist evidence of actual state changes (or a first baseline).
        save_json(ROOT / "history" / f"{today}.json", report)
        lines = [f"# Spotify playlist update — {today}", "",
                 "Listening source: Last.fm user littletown92, seven-day paginated scrobble window.",
                 "Spotify API: playlist inspection, catalogue resolution and verified edits.",
                 "Last.fm scrobbling completeness cannot be independently guaranteed.",
                 f"Observed Last.fm scrobbles: {source_stats['scrobbles_observed']}; Spotify catalogue mappings: {source_stats['repeated_titles_matched']}.", ""]
        for key, info in results.items():
            lines += [f"## {key}"]
            if "error" in info:
                lines += [f"- Error: {info['error']}", ""]
                continue
            lines += [f"- {info['name']} — {info['playlist_url']}",
                      f"- {info['before_count']} → {info['after_count']} tracks, {info['duration_minutes']} minutes",
                      f"- Added: {len(info['added'])}; removed: 0"]
            for t in info["added"]:
                lines.append(f"  - {t['name']} — {', '.join(t['artists'])}: {t['url']}")
            if info["note"]:
                lines.append(f"- Note: {info['note']}")
            lines.append("")
        report_path = ROOT / "reports" / f"{today}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(lines) + "\n"
        if not report_path.exists() or report_path.read_text() != content:
            report_path.write_text(content)
    if args.apply and not snapshots_changed:
        print("No playlist snapshot changes; no history files or commit needed")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if all("error" in r for r in results.values()):
        raise RuntimeError("No target playlists were readable; refusing to publish results")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
