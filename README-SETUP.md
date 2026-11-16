# MusicProfile — weekly Spotify automation

This package runs in `jlilleby/MusicProfile`, branch `main`.

## Install

Copy `.github/workflows/spotify-weekly.yml`, `scripts/spotify_refresh.py`,
`scripts/spotify_auth.py`, and `config/playlists.json` into your repo at the same paths.
Commit those files (NOT any credentials).

## Create a Spotify Web API app

1. Visit https://developer.spotify.com/dashboard and create an app. In development mode,
   Spotify requires Premium and may restrict who can use the app.
2. Add this Redirect URI: `http://127.0.0.1:8888/callback`.
3. Save your app's client ID and client secret securely.
4. Run locally (Python 3.12+):

   ```bash
   export SPOTIFY_CLIENT_ID='...'
   export SPOTIFY_CLIENT_SECRET='...'
   python scripts/spotify_auth.py
   ```

   Open its link, authorize your own Spotify account, and paste the full redirect URL.
   The script prints your refresh token. **Do not share or commit that token.**
5. In GitHub -> MusicProfile -> Settings -> Secrets and variables -> Actions,
   add three **repository secrets**:
   - `SPOTIFY_CLIENT_ID`
   - `SPOTIFY_CLIENT_SECRET`
   - `SPOTIFY_REFRESH_TOKEN`

## Review playlist IDs

`config/playlists.json` includes three IDs observed in your Spotify account.
Focus and Driving are discovered by matching the names of playlists you own. If
there are zero or multiple matches, set exact playlist IDs in the JSON config.

## Try it

Go to **Actions -> Weekly Spotify Playlist Refresh -> Run workflow**.
Leave **apply_changes=false** for the first run. It lists candidate additions but
never changes Spotify or writes output files.

When the output looks right, run it again with **apply_changes=true**.
Scheduled runs apply changes. This script *only adds* new songs; it does not remove
or replace existing playlist tracks. It selects tracks you've played at least twice
recently AND whose artists already appear in that playlist. Maximum seven additions
per playlist per run, and no duplicates. A playlist below two hours is reported, but
never padded with unsuitable songs to reach a target duration.

After a successful apply, the action commits:

```
playlists/*.json    current verified track lists
history/YYYY-MM-DD.json  aggregate change summary
reports/YYYY-MM-DD.md   human-readable change report
```

## Time zone and reliability

GitHub Actions cron schedules use UTC. This workflow has two Monday cron entries
(02:00 and 03:00 UTC), and skips the one not equal to **04:00 Europe/Oslo**, handling
DST. GitHub's scheduler may start a run late or even drop one; check the Actions tab.
Manual runs bypass the time guard. `workflow_dispatch` is read-only by default.

## Safety and API limitations

- This repository is **public**. Published snapshots disclose the tracks in your
  playlists, but not timestamps of individual listening events or personal API data.
- The Spotify recently-played API can return a limited playback window. The report
  indicates whether the 7-day cutoff was observed. It does **not** claim complete
  listening history when Spotify has no older results.
- API calls use Spotify's current playlist `/items` endpoints (2026), not legacy
  `/tracks` paths.
- If the playlist's owner doesn't match your Spotify account, it won't be edited.
- If token refresh fails, or Spotify rejects a request, workflow fails rather than
  inventing success.
- The script uses standard Python libraries and never prints credentials.
- Authorization is separate from the ChatGPT Spotify connector. GitHub Actions
  cannot reuse ChatGPT's Spotify tokens.
- A token rotation may require updating the `SPOTIFY_REFRESH_TOKEN` secret.

For questions about API access, see https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
