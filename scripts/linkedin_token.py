"""Create a LinkedIn access token via the 3-legged OAuth flow.

Opens the consent page in your browser, receives the redirect on localhost,
exchanges the code for a token and stores it in .env (never printed).

Prerequisite (once): add http://localhost:8799/callback to "Authorized
redirect URLs" on the app's Auth tab in the LinkedIn developer portal.

Usage: uv run python scripts/linkedin_token.py [--port 8799] [--set-secret]
"""
import argparse
import re
import secrets
import subprocess
import sys
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
SCOPE = "r_dma_portability_self_serve"


def update_env(text: str, values: dict[str, str]) -> str:
    """Replace existing KEY=... lines, append missing keys."""
    for key, value in values.items():
        line = f"{key}={value}"
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(lambda _m, line=line: line, text, count=1)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
    return text


def wait_for_code(port: int, state: str) -> str:
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            result.update(query)
            ok = "code" in query and query.get("state") == state
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"Token received, you can close this tab."
                if ok
                else b"Authorization failed, see the terminal."
            )

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 300
    server.handle_request()  # one request, then stop
    server.server_close()

    if "error" in result:
        sys.exit(f"LinkedIn returned an error: {result['error']} {result.get('error_description', '')}")
    if result.get("state") != state:
        sys.exit("No valid callback received (timeout or state mismatch).")
    return result["code"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--set-secret", action="store_true",
                        help="also update the GitHub secret LINKEDIN_ACCESS_TOKEN and variable LINKEDIN_TOKEN_EXPIRES")
    args = parser.parse_args()

    env = dotenv_values(ENV_FILE)
    client_id, client_secret = env.get("LINKEDIN_CLIENT_ID"), env.get("LINKEDIN_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET missing in .env")

    redirect_uri = f"http://localhost:{args.port}/callback"
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
    })
    print(f"Opening browser for consent (redirect {redirect_uri}) ...")
    print(f"If it does not open, visit:\n{url}\n")
    webbrowser.open(url)

    code = wait_for_code(args.port, state)

    response = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }, timeout=30)
    if response.status_code != 200:
        sys.exit(f"Token exchange failed: {response.status_code} {response.text[:300]}")
    data = response.json()
    token = data["access_token"]
    expires = date.today() + timedelta(seconds=int(data.get("expires_in", 0)))

    ENV_FILE.write_text(update_env(ENV_FILE.read_text(), {
        "LINKEDIN_ACCESS_TOKEN": token,
        "LINKEDIN_TOKEN_EXPIRES": expires.isoformat(),
    }))
    print(f"Saved LINKEDIN_ACCESS_TOKEN to .env (scope: {data.get('scope')}, expires {expires}).")
    if data.get("refresh_token"):
        print("Note: the response contained a refresh_token (not stored).")

    if args.set_secret:
        subprocess.run(
            ["gh", "secret", "set", "LINKEDIN_ACCESS_TOKEN", "--repo", "ping13/star-collector"],
            input=token, text=True, check=True,
        )
        subprocess.run(
            ["gh", "variable", "set", "LINKEDIN_TOKEN_EXPIRES", "--repo", "ping13/star-collector",
             "--body", expires.isoformat()],
            check=True,
        )
        print("Updated GitHub secret LINKEDIN_ACCESS_TOKEN and variable LINKEDIN_TOKEN_EXPIRES.")


if __name__ == "__main__":
    main()
