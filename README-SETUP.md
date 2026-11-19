# MusicProfile — Weekly Last.fm → Spotify playlist refresh

This project runs in the public GitHub repository `jlilleby/MusicProfile` on `main`.

## Current setup

- **History:** Last.fm user [littletown92](https://www.last.fm/user/littletown92), using the GitHub Actions secret `LASTFM_API_KEY`.
- **Playlist edits:** Spotify Web API, using the existing `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, and `SPOTIFY_REFRESH_TOKEN` secrets.
- **Schedule:** Every Monday at 04:00 Europe/Oslo. GitHub Actions uses UTC cron entries and an Oslo daylight-saving-time guard. The exact start time can be delayed by GitHub.
- **Manual test:** Actions → Weekly Spotify Playlist Refresh → Run workflow; leave `apply_changes=false` for dry-run. Use `true` only when you want to write verified Spotify updates.
- **Collection:** No 30-minute polling workflow is scheduled anymore. Last.fm is queried once per weekly refresh.

## How weekly scrobbles are used

`scripts/lastfm_history.py` requests a paginated, seven-day scrobble window using Last.fm's `user.getrecenttracks` API, with pages of up to 200 tracks. It excludes unconfirmed "now playing" tracks, deduplicates repeated pages, and fails rather than silently truncating unexpectedly large or changing pagination.

Last.fm provides track and artist names, not Spotify URIs. For songs played at least twice, the script searches Spotify's catalogue and only accepts an **unambiguous exact artist + track-title match**, with a limit on the number of catalogue searches. Ambiguous remasters, different versions, and unmatched songs are skipped. Spotify is used separately to inspect the five existing playlists, verify ownership, and append up to seven qualifying new tracks per playlist. There are no removals or replacement playlists.

All playlist edits are verified by re-reading Spotify. Successful changes are saved to `playlists/*.json`, with dated reports in `reports/` and dated change summaries in `history/`. The workflow avoids commits when nothing changed. Song choices remain conservative: a repeated track must be by an artist already on the target playlist.

The report states the observed scrobble count and matching diagnostics. **A complete seven-day API query is not proof Last.fm captured every Spotify play.** No individual Last.fm timestamps or raw scrobble events are committed to this public repository.

## Secrets

In [GitHub Actions secrets](https://github.com/jlilleby/MusicProfile/settings/secrets/actions), configure:

- `LASTFM_API_KEY` — Last.fm API key, read-only for public listening history.
- `SPOTIFY_CLIENT_ID` — Spotify developer app client ID.
- `SPOTIFY_CLIENT_SECRET` — Spotify developer app client secret.
- `SPOTIFY_REFRESH_TOKEN` — Spotify OAuth refresh token with the required playlist scopes.

Never put values in workflow YAML, commits, issues, or logs. Last.fm username `littletown92` is not a secret.

## Removed polling workflow and retained archive

The old `.github/workflows/spotify-collect.yml` schedule has been deleted. The archived `scripts/spotify_history.py` and encrypted `data/listening-history.enc.json` are retained for historical continuity but **are not used by the new weekly workflow**. They can be archived or removed later if desired; deleting the collector does not retroactively remove old commits.

## Limits and troubleshooting

- Last.fm starts capturing Spotify scrobbles when you connect Spotify scrobbling, not retroactively. Verify the Recent Tracks list in Last.fm.
- Last.fm syncing interruptions or tracks not scrobbled may create gaps; the workflow cannot guarantee completeness.
- If `LASTFM_API_KEY` is absent or invalid, the workflow fails safely before writing to Spotify.
- Spotify's 2026 developer API uses `/playlists/{id}/items` for playlist changes and a maximum search limit of 10.
- Spotify search matching deliberately skips ambiguous recordings. The weekly run may find many Last.fm scrobbles but no safe additions.
- Your GitHub repository is public. Playlist snapshots and weekly aggregate reports are public, but raw scrobble histories, exact listen timestamps, and all access secrets are excluded.
- Manual runs default to dry-run, so they do not change playlists or create commits.
- Schedule can be delayed or occasionally skipped by GitHub Actions.

Official API references: https://www.last.fm/api/show/user.getRecentTracks and https://developer.spotify.com/documentation/web-api/reference/search
