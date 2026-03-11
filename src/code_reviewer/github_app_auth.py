from __future__ import annotations

import os
import time

from code_reviewer.logger import info, warn


def is_github_app_auth() -> bool:
    return all(
        os.environ.get(k)
        for k in ("GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PRIVATE_KEY")
    )


def _generate_jwt(app_id: str, private_key: str) -> str:
    import jwt

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def refresh_github_token() -> None:
    if not is_github_app_auth():
        return

    app_id = os.environ["GITHUB_APP_ID"]
    installation_id = os.environ["GITHUB_APP_INSTALLATION_ID"]
    private_key = os.environ["GITHUB_APP_PRIVATE_KEY"]

    try:
        token = _create_installation_token(app_id, installation_id, private_key)
        os.environ["GH_TOKEN"] = token
        info("Refreshed GitHub App installation token")
    except Exception as exc:  # noqa: BLE001
        warn(f"Failed to refresh GitHub App token: {exc}")


def _create_installation_token(app_id: str, installation_id: str, private_key: str) -> str:
    import urllib.request

    jwt_token = _generate_jwt(app_id, private_key)
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    import json

    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data["token"]
