import os
from unittest.mock import patch

from code_reviewer.github_app_auth import (
    _generate_jwt,
    is_github_app_auth,
    refresh_github_token,
)


def test_is_github_app_auth_true(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")
    assert is_github_app_auth() is True


def test_is_github_app_auth_false_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    assert is_github_app_auth() is False


def test_is_github_app_auth_false_when_partial(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    assert is_github_app_auth() is False


def test_generate_jwt_returns_string() -> None:
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    token = _generate_jwt("12345", pem)
    assert isinstance(token, str)

    # Decode without verification to check claims
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == "12345"
    assert "iat" in claims
    assert "exp" in claims


def test_refresh_github_token_noop_when_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    refresh_github_token()

    assert "GH_TOKEN" not in os.environ


def test_refresh_github_token_sets_gh_token(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")
    monkeypatch.delenv("GH_TOKEN", raising=False)

    with patch(
        "code_reviewer.github_app_auth._create_installation_token",
        return_value="ghs_fake_token_123",
    ):
        refresh_github_token()

    assert os.environ["GH_TOKEN"] == "ghs_fake_token_123"


def test_refresh_github_token_warns_on_failure(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")
    monkeypatch.delenv("GH_TOKEN", raising=False)

    with patch(
        "code_reviewer.github_app_auth._create_installation_token",
        side_effect=RuntimeError("network error"),
    ):
        # Should not raise — just warn
        refresh_github_token()

    assert "GH_TOKEN" not in os.environ
