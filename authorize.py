#!/usr/bin/env python3
"""
One-time Spotify authorization helper for nowplaying.py.

No web server, no domain, no Docker required. This uses Spotify's standard
authorization-code flow with a loopback redirect URI - you open one URL in
any browser (on any device), log in, then paste the URL you land on back
into this script. That's it.

Usage:
    python3 authorize.py
"""

import base64
import json
import os
import sys
import urllib.parse
import urllib.request

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = "user-read-currently-playing user-read-playback-state user-read-recently-played"


def main():
    print("=== Spotify authorization for nowplaying.py ===\n")
    print("First, create a Spotify app (free, takes a minute):")
    print("  1. Go to https://developer.spotify.com/dashboard")
    print("  2. Click 'Create app'")
    print("  3. Fill in any name/description")
    print(f"  4. Under 'Redirect URIs', add exactly: {REDIRECT_URI}")
    print("  5. Under 'Which API/SDKs are you planning to use?', tick 'Web API'")
    print("  6. Save, then open the app and copy its Client ID and Client Secret\n")

    client_id = input("Client ID: ").strip()
    client_secret = input("Client Secret: ").strip()

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
    }
    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)

    print("\nOpen this URL in any browser (your phone is fine) and log in:\n")
    print(auth_url)
    print(
        "\nAfter you approve, the page will fail to load (that's expected - nothing is "
        "listening on that address). Copy the FULL URL from the address bar afterwards "
        "and paste it below."
    )

    pasted = input("\nPaste the redirected URL here: ").strip()

    parsed = urllib.parse.urlparse(pasted)
    query = urllib.parse.parse_qs(parsed.query)
    if "code" not in query:
        # allow pasting just the bare code too
        code = pasted
    else:
        code = query["code"][0]

    if "error" in query:
        print(f"\nSpotify returned an error: {query['error'][0]}")
        sys.exit(1)

    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    ).encode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=data,
        headers={"Authorization": f"Basic {auth_header}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"\nToken exchange failed: {e.read().decode()}")
        sys.exit(1)

    refresh_token = body["refresh_token"]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, ".env")
    with open(env_path, "w") as f:
        f.write(f"SPOTIFY_CLIENT_ID={client_id}\n")
        f.write(f"SPOTIFY_CLIENT_SECRET={client_secret}\n")
        f.write(f"SPOTIFY_REFRESH_TOKEN={refresh_token}\n")
    os.chmod(env_path, 0o600)

    print(f"\nDone! Wrote credentials to {env_path}")
    print("You can now run: python3 nowplaying.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
