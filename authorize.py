#!/usr/bin/env python3
"""
One-time Spotify authorization helper for nowplaying.py.

No domain, no Docker, no permanent server required. Uses Spotify's standard
authorization-code flow with a loopback redirect URI. Supports two modes:

  1. Same machine as your browser: a tiny local server catches the redirect
     automatically - just click through and you're done.
  2. Headless/remote machine (e.g. a homelab box you're SSH'd into): open the
     link on any device, then paste the final URL back into this script.

Usage:
    python3 authorize.py
"""

import base64
import http.server
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = "user-read-currently-playing user-read-playback-state user-read-recently-played"

SUCCESS_HTML = b"""<!doctype html>
<html><body style="background:#121212;color:#fff;font-family:sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
<div style="text-align:center;">
<h2 style="color:#1db954;">Authorized!</h2>
<p>You can close this tab and return to the terminal.</p>
</div></body></html>"""

ERROR_HTML = b"""<!doctype html>
<html><body style="background:#121212;color:#fff;font-family:sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
<div style="text-align:center;">
<h2 style="color:#e74c3c;">Authorization failed</h2>
<p>You can close this tab and check the terminal for details.</p>
</div></body></html>"""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        self.server.auth_code = query.get("code", [None])[0]
        self.server.auth_error = query.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(SUCCESS_HTML if self.server.auth_code else ERROR_HTML)

    def log_message(self, format, *args):
        pass  # silence default request logging


def build_auth_url(client_id):
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
    }
    return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)


def exchange_code(client_id, client_secret, code):
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
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def save_env(client_id, client_secret, refresh_token):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, ".env")
    with open(env_path, "w") as f:
        f.write(f"SPOTIFY_CLIENT_ID={client_id}\n")
        f.write(f"SPOTIFY_CLIENT_SECRET={client_secret}\n")
        f.write(f"SPOTIFY_REFRESH_TOKEN={refresh_token}\n")
    os.chmod(env_path, 0o600)
    return env_path


def extract_code_from_pasted_url(pasted):
    parsed = urllib.parse.urlparse(pasted.strip())
    query = urllib.parse.parse_qs(parsed.query)
    if "error" in query:
        print(f"\nSpotify returned an error: {query['error'][0]}")
        sys.exit(1)
    if "code" in query:
        return query["code"][0]
    return pasted.strip()  # allow pasting just the bare code


def main():
    print("=== Spotify authorization for nowplaying.py ===\n")
    print(
        "IMPORTANT: since Spotify's February 2026 policy change, the account "
        "that creates the developer app below must have an active Premium "
        "subscription - the Web API won't work in Development Mode without "
        "it, even for simple read-only calls. If you don't have Premium on "
        "this account, this won't work no matter how correctly everything "
        "else below is set up.\n"
    )
    print("Create a Spotify app (free, takes a minute):")
    print("  1. Go to https://developer.spotify.com/dashboard")
    print("  2. Click 'Create app'")
    print("  3. Fill in any name/description")
    print(f"  4. Under 'Redirect URIs', add exactly: {REDIRECT_URI}")
    print("     IMPORTANT: after saving, reload the page and confirm it still")
    print("     shows '127.0.0.1' and not 'localhost' - Spotify's dashboard has")
    print("     a known bug where it can silently revert this on save. If it")
    print("     reverted, delete it and re-add it, then reload again to check.")
    print("  5. Under 'Which API/SDKs are you planning to use?', tick 'Web API'")
    print("  6. Save, then open the app and copy its Client ID and Client Secret\n")

    client_id = input("Client ID: ").strip()
    client_secret = input("Client Secret: ").strip()

    auth_url = build_auth_url(client_id)

    print(
        "\nIs the browser you'll use to log in running on THIS SAME machine?"
    )
    same_machine = input("[y/N]: ").strip().lower().startswith("y")

    code = None

    if same_machine:
        server = http.server.HTTPServer(("127.0.0.1", 8888), _CallbackHandler)
        server.auth_code = None
        server.auth_error = None
        server.timeout = 120

        def serve_once():
            server.handle_request()

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()

        print(f"\nOpening your browser...\nIf it doesn't open, visit:\n{auth_url}\n")
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

        print("Waiting for you to approve in the browser (2 minute timeout)...")
        thread.join(timeout=125)

        if server.auth_error:
            print(f"\nSpotify returned an error: {server.auth_error}")
            print(
                "If this says something about the redirect URI, double-check step 4 "
                "above - the dashboard bug mentioned there is the most common cause."
            )
            sys.exit(1)
        code = server.auth_code
        if not code:
            print(
                "\nTimed out waiting for the redirect. This usually means Spotify's "
                "dashboard silently changed your Redirect URI - go back and re-check "
                "step 4 above, then try again."
            )
            sys.exit(1)
    else:
        print("\nOpen this URL in any browser (your phone is fine) and log in:\n")
        print(auth_url)
        print(
            "\nAfter you approve, the page will fail to load (nothing is listening "
            "on that address from where you're opening it - that's expected). Copy "
            "the FULL URL from the address bar afterwards and paste it below."
        )
        pasted = input("\nPaste the redirected URL here: ").strip()
        code = extract_code_from_pasted_url(pasted)

    try:
        token_data = exchange_code(client_id, client_secret, code)
    except urllib.error.HTTPError as e:
        print(f"\nToken exchange failed: {e.read().decode()}")
        sys.exit(1)

    env_path = save_env(client_id, client_secret, token_data["refresh_token"])
    print(f"\nDone! Wrote credentials to {env_path}")
    print("You can now run: python3 nowplaying.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
