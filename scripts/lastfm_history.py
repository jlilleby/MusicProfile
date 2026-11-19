#!/usr/bin/env python3
"""Weekly, read-only Last.fm scrobble retrieval and conservative Spotify matching.

Last.fm has no Spotify track URIs. Search only repeatedly scrobbled tracks,
and accept a Spotify match only when BOTH artist and full track title match,
with exactly one distinct Spotify URI. Never guess an ambiguous recording.
"""
import collections
import datetime as dt
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
USERNAME = "littletown92"
UTC = dt.timezone.utc
MAX_PAGES = 150
MAX_SEARCHES = 150


def _norm(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _request(params):
    url = LASTFM_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "MusicProfile-Actions/2.0"}),
                timeout=35,
            ) as response:
                data = json.load(response)
            if "error" in data:
                raise RuntimeError("Last.fm API rejected request: " + str(data.get("message", data["error"])))
            return data
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                wait = min(45, max(2, int(exc.headers.get("Retry-After", "3"))))
                time.sleep(wait)
                continue
            raise RuntimeError(f"Last.fm request failed with HTTP {exc.code}") from None
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError("Last.fm is unreachable") from exc
    raise RuntimeError("Last.fm request retries exhausted")


def fetch_scrobbles(days=7, now=None):
    key = os.environ.get("LASTFM_API_KEY")
    if not key:
        raise RuntimeError("Missing GitHub Actions secret LASTFM_API_KEY")
    now = now or dt.datetime.now(UTC)
    end = int(now.timestamp())
    start = int((now - dt.timedelta(days=days)).timestamp())
    seen = set()
    plays = collections.Counter()
    display = {}
    pages_reported = None
    page_num = 1
    while True:
        response = _request({
            "method": "user.getrecenttracks", "user": USERNAME, "api_key": key,
            "format": "json", "from": start, "to": end,
            "limit": 200, "page": page_num,
        })
        parent = response.get("recenttracks") or {}
        attrs = parent.get("@attr") or {}
        try:
            total_pages = int(attrs.get("totalPages", 1))
        except (ValueError, TypeError):
            raise RuntimeError("Last.fm response has invalid totalPages")
        if total_pages < 1 or total_pages > MAX_PAGES:
            raise RuntimeError(f"Last.fm history has {total_pages} pages; refusing incomplete pagination")
        if pages_reported is None:
            pages_reported = total_pages
        elif total_pages != pages_reported:
            raise RuntimeError("Last.fm pagination changed during retrieval; retry before editing playlists")
        rows = parent.get("track") or []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise RuntimeError("Invalid Last.fm recenttracks response")
        for row in rows:
            date = row.get("date") or {}
            try:
                stamp = int(date.get("uts"))
            except (ValueError, TypeError):
                continue  # now-playing entries are not confirmed scrobbles
            if not start <= stamp <= end:
                continue
            artist_raw = row.get("artist") or {}
            artist = artist_raw.get("#text", "") if isinstance(artist_raw, dict) else artist_raw
            title = row.get("name") or ""
            k = (_norm(artist), _norm(title))
            if not all(k):
                continue
            unique = (stamp,) + k
            if unique in seen:
                continue
            seen.add(unique)
            plays[k] += 1
            display.setdefault(k, (str(artist), str(title)))
        if page_num >= pages_reported:
            break
        page_num += 1
    stats = {
        "source": "last.fm",
        "lastfm_user": USERNAME,
        "scrobbles_observed": sum(plays.values()),
        "unique_tracks_observed": len(plays),
        "pages_fetched": page_num,
        "seven_day_window_queried": True,
        "seven_day_coverage_verified": False,  # scrobbling outages cannot be ruled out
    }
    print(f"Last.fm: {stats['scrobbles_observed']} confirmed scrobbles, "
          f"{stats['unique_tracks_observed']} unique tracks, {page_num} pages over seven days.")
    return plays, display, stats


def resolve_spotify(token, get, track_data, counts, display):
    """Return Spotify track objects repeated per scrobble for existing propose().

    A small capped set of repeated tracks is evaluated, ranked by frequency.
    Ambiguous matches, remasters with mismatched names, and missing catalogue
    entries are excluded rather than writing possibly incorrect versions.
    """
    candidates = [(k, count) for k, count in counts.items() if count >= 2]
    candidates.sort(key=lambda item: (-item[1], item[0]))
    skipped_limit = max(0, len(candidates) - MAX_SEARCHES)
    candidates = candidates[:MAX_SEARCHES]
    listens = []
    matched = 0
    ambiguous = 0
    unmatched = 0
    for k, count in candidates:
        artist, title = display[k]
        # Spotify search supports field-qualified queries, maximum limit=10.
        safe_artist = artist.replace('"', ' ').strip()
        safe_title = title.replace('"', ' ').strip()
        query = f'track:"{safe_title}" artist:"{safe_artist}"'
        result = get(token, "/search", q=query, type="track", limit=10)
        items = ((result.get("tracks") or {}).get("items")) or []
        exact = {}
        for item in items:
            info = track_data(item)
            if info is None or _norm(info["name"]) != k[1]:
                continue
            if not any(_norm(a) == k[0] for a in info["artists"]):
                continue
            exact[info["uri"]] = info
        if len(exact) == 1:
            listens.extend([next(iter(exact.values()))] * count)
            matched += 1
        elif exact:
            ambiguous += 1
        else:
            unmatched += 1
    print(f"Spotify catalogue mapping: matched {matched}, ambiguous {ambiguous}, "
          f"unmatched {unmatched}, capped {skipped_limit} repeatedly scrobbled titles.")
    return listens, {
        "repeated_titles_matched": matched,
        "repeated_titles_ambiguous": ambiguous,
        "repeated_titles_unmatched": unmatched,
        "repeated_titles_skipped_cap": skipped_limit,
    }
