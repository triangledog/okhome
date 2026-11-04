"""Exceptions raised by the OK Hjem client."""


class OKHomeError(Exception):
    """Base exception for the package."""


class AuthenticationError(OKHomeError):
    """Authentication failed or credentials are unavailable."""


class ConfigurationError(OKHomeError):
    """The client is missing required local configuration."""


class APIError(OKHomeError):
    """An API returned an unsuccessful response."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"OK Hjem API returned HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class NetworkError(OKHomeError):
    """A request failed before an HTTP response was received."""


class ProtocolError(OKHomeError):
    """An API response does not match the documented protocol."""
