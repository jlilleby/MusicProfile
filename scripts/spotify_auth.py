#!/usr/bin/env python3
"""One-time Spotify Authorization Code bootstrap. Do not commit printed token."""
import base64
import json
import os
import secrets
import urllib.parse
import urllib.request

CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-recently-played playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public"
state = secrets.token_urlsafe(20)
auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": CLIENT_ID, "scope": SCOPES,
    "redirect_uri": REDIRECT, "state": state, "show_dialog": "true"
})
print("1. Register exactly this redirect URI in your Spotify developer app:", REDIRECT)
print("2. Open the following link and approve access:\n", auth_url)
print("3. Paste the *full* browser redirect URL. A connection-refused page is expected.")
response_url = input("Redirect URL: ").strip()
query = urllib.parse.parse_qs(urllib.parse.urlsplit(response_url).query)
if query.get("state", [""])[0] != state:
    raise SystemExit("Invalid authorization state")
if "code" not in query:
    raise SystemExit("Authorization code missing: " + str(query.get("error")))
auth_header = base64.b64encode((CLIENT_ID + ":" + CLIENT_SECRET).encode()).decode()
req = urllib.request.Request("https://accounts.spotify.com/api/token",
    data=urllib.parse.urlencode({"grant_type": "authorization_code", "code": query["code"][0],
                                 "redirect_uri": REDIRECT}).encode(),
    headers={"Authorization": "Basic " + auth_header,
             "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
with urllib.request.urlopen(req) as resp:
    token = json.load(resp)
print("\nSet this as GitHub secret SPOTIFY_REFRESH_TOKEN (never commit or share it):")
print(token["refresh_token"])
