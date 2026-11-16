#!/usr/bin/env python3
"""Pure-function smoke tests; no Spotify credentials needed."""
import importlib.util
import json
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("refresh", Path(__file__).with_name("spotify_refresh.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def track(uri, artist, name="Song"):
    return {"uri": uri, "artist_ids": [artist], "name": name,
            "artists": [artist], "duration_ms": 200000, "url": ""}

old = [track("spotify:track:old", "artist-a")]
recent = [track("spotify:track:new", "artist-a"), track("spotify:track:new", "artist-a"),
          track("spotify:track:wrong", "artist-b"), track("spotify:track:old", "artist-a")]
assert [x["uri"] for x in mod.propose(old, recent)] == ["spotify:track:new"]
assert mod.propose(old, [recent[0]]) == []
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / 'playlist.json'
    s = {"snapshot_date": "2026-11-16", "track_count": 1}
    assert mod.save_snapshot_if_changed(p, s)
    assert not mod.save_snapshot_if_changed(p, {**s, "snapshot_date": "2026-11-23"})
    assert json.loads(p.read_text())["snapshot_date"] == "2026-11-16"
print("Offline smoke tests passed")
