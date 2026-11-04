"""Tests for device bootstrap, request-token renewal, and login."""

import base64
import json
from pathlib import Path

import httpx
import pytest

from okhome.auth import AuthConfig, FileAuthStore, OKHomeAuth
from okhome.exceptions import ProtocolError


def encoded_token(*, write: bool) -> str:
    """Build a token with the wire format observed in the app."""
    payload = {
        "Version": 1,
        "Expires": "2099-01-01T00:00:00Z",
        "HasWritePermission": write,
        "Data": "opaque",
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_auth_config_reads_owner_local_apk_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extracted APK component can remain outside source and environment variables."""
    monkeypatch.delenv("OKHOME_HMAC_KEY_PREFIX", raising=False)
    config_path = tmp_path / "apk.json"
    config_path.write_text('{"hmac_key_prefix":"local-test-value"}', encoding="utf-8")
    assert AuthConfig.from_env(config_path).hmac_key_prefix == "local-test-value"


@pytest.mark.asyncio
async def test_bootstrap_login_and_cached_read_token(tmp_path: Path) -> None:
    """The client registers once, renews by type, and clears tokens on login."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert "test-prefix" not in request.content.decode(errors="ignore")
        if request.url.path == "/api/v2/RegisterDevice":
            assert "X-App-Device-Identification" not in request.headers
            return httpx.Response(200, json={"device": {"deviceIdentification": "device-1"}})
        if request.url.path == "/api/v1/GetRequestToken":
            is_write = request.url.params["writePermission"] == "true"
            return httpx.Response(200, text=encoded_token(write=is_write))
        if request.url.path == "/api/v1/Login":
            assert request.headers["X-App-Request-Token"] == encoded_token(write=True)
            body = json.loads(request.content)
            assert body == {"emailAddress": "me@example.test", "password": "secret"}
            return httpx.Response(
                200,
                json={
                    "userProfile": {"userProfileNumber": 42, "loginToken": "not-persisted"},
                    "device": {"deviceIdentification": "device-1"},
                },
            )
        raise AssertionError(f"Unexpected path {request.url.path}")

    http = httpx.AsyncClient(
        base_url="https://ok-home-app-service.okcloud.dk",
        transport=httpx.MockTransport(handler),
    )
    store_path = tmp_path / "auth.json"
    auth = OKHomeAuth(AuthConfig("test-prefix"), FileAuthStore(store_path), http_client=http)

    await auth.login("me@example.test", "secret")
    read = await auth.get_token()
    assert read == encoded_token(write=False)
    assert store_path.stat().st_mode & 0o777 == 0o600
    assert "secret" not in store_path.read_text()
    assert "not-persisted" not in store_path.read_text()
    assert [request.url.path for request in calls] == [
        "/api/v2/RegisterDevice",
        "/api/v1/GetRequestToken",
        "/api/v1/Login",
        "/api/v1/GetRequestToken",
    ]
    await http.aclose()


@pytest.mark.asyncio
async def test_register_device_rejects_invalid_json(tmp_path: Path) -> None:
    """Malformed successful responses become stable protocol errors."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = OKHomeAuth(
        AuthConfig("test-prefix"),
        FileAuthStore(tmp_path / "auth.json"),
        http_client=http,
    )
    with pytest.raises(ProtocolError, match="RegisterDevice returned invalid JSON"):
        await auth.ensure_device()
    await http.aclose()
