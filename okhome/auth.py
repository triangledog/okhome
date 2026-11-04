"""Device bootstrap, request-token renewal, and email/password login."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from okhome.exceptions import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    NetworkError,
    ProtocolError,
)

APP_SERVICE_URL = "https://ok-home-app-service.okcloud.dk"
APP_HMAC_KEY_PREFIX = "71809991-DAA1-4E68-9165-A9AC2C06AEDE"


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Authentication configuration that is never logged by this package."""

    hmac_key_prefix: str
    caller: str = "okhome-python"
    app_version: str = "2.10.0"
    build_number: str = "1144"

    @classmethod
    def for_app(cls) -> AuthConfig:
        """Return the protocol profile verified for OK Hjem 2.10.0."""
        return cls(hmac_key_prefix=APP_HMAC_KEY_PREFIX)

    @classmethod
    def from_env(cls, local_path: Path | None = None) -> AuthConfig:
        """Load the HMAC key prefix from the environment or local config."""
        prefix = os.environ.get("OKHOME_HMAC_KEY_PREFIX")
        if prefix:
            return cls(hmac_key_prefix=prefix)
        path = local_path or Path.home() / ".config" / "okhome" / "apk.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(
                f"Set OKHOME_HMAC_KEY_PREFIX or create the local config at {path}"
            ) from exc
        prefix = data.get("hmac_key_prefix") if isinstance(data, dict) else None
        if not isinstance(prefix, str) or not prefix:
            raise ConfigurationError(f"Local config at {path} has no hmac_key_prefix")
        return cls(hmac_key_prefix=prefix)


@dataclass(slots=True)
class CachedRequestToken:
    """Persisted request-token metadata and the opaque wire value."""

    raw: str
    expires: str | None
    has_write_permission: bool

    def valid(self, *, skew_seconds: int = 30) -> bool:
        """Return whether the token remains valid beyond a small clock skew."""
        if self.expires is None:
            return False
        try:
            expires = datetime.fromisoformat(self.expires.replace("Z", "+00:00"))
        except ValueError:
            return False
        if expires.tzinfo is None:
            return False
        return expires.timestamp() > time.time() + skew_seconds


@dataclass(slots=True)
class AuthState:
    """Installation identity and renewable request tokens."""

    installation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    device_identification: str | None = None
    read_token: CachedRequestToken | None = None
    write_token: CachedRequestToken | None = None


class AuthStore(Protocol):
    """Persistence interface suitable for replacement by a HA storage adapter."""

    def load(self) -> AuthState: ...

    def save(self, state: AuthState) -> None: ...


class FileAuthStore:
    """JSON auth store protected by owner-only filesystem permissions."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> AuthState:
        """Load state or create a new installation identity."""
        if not self._path.exists():
            state = AuthState()
            self.save(state)
            return state
        data = json.loads(self._path.read_text(encoding="utf-8"))

        def token(name: str) -> CachedRequestToken | None:
            value = data.get(name)
            return CachedRequestToken(**value) if isinstance(value, dict) else None

        return AuthState(
            installation_id=str(data["installation_id"]),
            device_identification=data.get("device_identification"),
            read_token=token("read_token"),
            write_token=token("write_token"),
        )

    def save(self, state: AuthState) -> None:
        """Atomically persist state without granting group/world access."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(asdict(state), output, separators=(",", ":"))
        os.replace(temporary, self._path)
        self._path.chmod(0o600)


class MemoryAuthStore:
    """In-memory state store for hosts that persist stable fields themselves."""

    def __init__(self, state: AuthState | None = None) -> None:
        self.state = state or AuthState()

    def load(self) -> AuthState:
        """Return the current state object."""
        return self.state

    def save(self, state: AuthState) -> None:
        """Keep updated state in memory without persisting short-lived tokens."""
        self.state = state


class RequestTokenProvider(Protocol):
    """Authentication interface consumed by the electricity client."""

    @property
    def device_identification(self) -> str | None: ...

    async def get_token(self, *, write: bool = False, force: bool = False) -> str: ...


class StaticTokenAuth:
    """Use an externally supplied device ID and request token."""

    def __init__(self, device_identification: str, request_token: str) -> None:
        self._device_identification = device_identification
        self._request_token = request_token

    @property
    def device_identification(self) -> str:
        """Return the configured server-issued device identifier."""
        return self._device_identification

    async def get_token(self, *, write: bool = False, force: bool = False) -> str:
        """Return the opaque token; static tokens cannot be renewed."""
        del write, force
        return self._request_token


class OKHomeAuth:
    """Implements the device/HMAC/request-token flow observed in app 2.10.0."""

    def __init__(
        self,
        config: AuthConfig,
        store: AuthStore,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._state = store.load()
        self._http = http_client or httpx.AsyncClient(base_url=APP_SERVICE_URL, timeout=20.0)
        self._owns_http = http_client is None
        self._device_lock = asyncio.Lock()
        self._token_locks = {False: asyncio.Lock(), True: asyncio.Lock()}

    @property
    def device_identification(self) -> str | None:
        """Return the server-issued identifier, if registration has completed."""
        return self._state.device_identification

    def _headers(self, path: str, *, request_token: str | None = None) -> dict[str, str]:
        timestamp = int(time.time())
        fields: dict[str, str | int] = {"endpoint": path, "timestamp": timestamp}
        if self._state.device_identification:
            fields["deviceIdentification"] = self._state.device_identification
        canonical = json.dumps(
            [{key: fields[key]} for key in sorted(fields)],
            separators=(",", ":"),
        )
        key = f"{self._config.hmac_key_prefix}{self._state.installation_id}".encode()
        signature = hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()
        headers = {
            "caller": self._config.caller,
            "correlationId": str(uuid.uuid4()),
            "X-App-Hmac": signature,
            "X-App-Timestamp": str(timestamp),
            "X-App-Version": self._config.app_version,
            "X-App-Build-Number": self._config.build_number,
            "X-App-Hardware-Model": "Python",
            "X-App-Platform": "Android",
            "X-App-Platform-Version": "unknown",
            "X-App-Device-Name": "okhome-python",
        }
        if self._state.device_identification:
            headers["X-App-Device-Identification"] = self._state.device_identification
        if request_token:
            headers["X-App-Request-Token"] = request_token
        return headers

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.is_success:
            return
        message = response.text[:500] or response.reason_phrase
        if response.status_code in (401, 403):
            raise AuthenticationError(f"Authentication rejected with HTTP {response.status_code}")
        raise APIError(response.status_code, message)

    async def ensure_device(self) -> str:
        """Register this installation and return its server device ID."""
        if self._state.device_identification:
            return self._state.device_identification
        async with self._device_lock:
            if self._state.device_identification:
                return self._state.device_identification
            path = "/api/v2/RegisterDevice"
            try:
                response = await self._http.post(
                    f"{APP_SERVICE_URL}{path}",
                    headers=self._headers(path),
                    json={"deviceSecret": self._state.installation_id},
                )
            except httpx.RequestError as exc:
                raise NetworkError("Device registration request failed") from exc
            self._raise(response)
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProtocolError("RegisterDevice returned invalid JSON") from exc
            device = payload.get("device") if isinstance(payload, dict) else None
            identifier = device.get("deviceIdentification") if isinstance(device, dict) else None
            if not isinstance(identifier, str):
                raise ProtocolError("RegisterDevice response has no deviceIdentification")
            self._state.device_identification = identifier
            self._store.save(self._state)
            return identifier

    async def get_token(self, *, write: bool = False, force: bool = False) -> str:
        """Return a valid request token, renewing it when expired or forced."""
        await self.ensure_device()
        cached = self._state.write_token if write else self._state.read_token
        if not force and cached and cached.valid():
            return cached.raw
        async with self._token_locks[write]:
            cached = self._state.write_token if write else self._state.read_token
            if not force and cached and cached.valid():
                return cached.raw
            path = "/api/v1/GetRequestToken"
            try:
                response = await self._http.get(
                    f"{APP_SERVICE_URL}{path}",
                    headers=self._headers(path),
                    params={"writePermission": str(write).lower()},
                )
            except httpx.RequestError as exc:
                raise NetworkError("Request-token renewal failed") from exc
            self._raise(response)
            raw = response.text.strip()
            try:
                decoded = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
            except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtocolError(
                    "GetRequestToken did not return the expected Base64 token"
                ) from exc
            if not isinstance(decoded, dict) or not isinstance(decoded.get("Data"), str):
                raise ProtocolError("Decoded request token has an unexpected shape")
            token = CachedRequestToken(
                raw=raw,
                expires=decoded.get("Expires") if isinstance(decoded.get("Expires"), str) else None,
                has_write_permission=decoded.get("HasWritePermission") is True,
            )
            if write:
                self._state.write_token = token
            else:
                self._state.read_token = token
            self._store.save(self._state)
            return raw

    async def login(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate a user without persisting or logging the password."""
        token = await self.get_token(write=True)
        path = "/api/v1/Login"
        try:
            response = await self._http.post(
                f"{APP_SERVICE_URL}{path}",
                headers=self._headers(path, request_token=token),
                json={"emailAddress": email, "password": password},
            )
        except httpx.RequestError as exc:
            raise NetworkError("Login request failed") from exc
        self._raise(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProtocolError("Login returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProtocolError("Login response must be an object")
        device = payload.get("device")
        identifier = device.get("deviceIdentification") if isinstance(device, dict) else None
        if isinstance(identifier, str):
            self._state.device_identification = identifier
        # The app clears both installation tokens after the account association changes.
        self._state.read_token = None
        self._state.write_token = None
        self._store.save(self._state)
        return payload

    async def aclose(self) -> None:
        """Close the internally owned HTTP client."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> OKHomeAuth:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
