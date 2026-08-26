"""Lightweight per-provider circuit breaker.

Tracks consecutive connection/timeout failures per provider (e.g. "local",
"groq", "gemini"). After `failure_threshold` consecutive failures for a
provider, the circuit "opens" for `cooldown_seconds": further records that
would be routed to that provider are short-circuited straight to the
Dead-Letter Queue instead of making another (likely doomed) network call.

This is intentionally simple and in-memory — one instance is created per
run (see api/job.py) so failures on one run never bleed into another.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict

# Errors that count as "the endpoint looks dead" rather than "the model
# disagreed" or "the payload was bad". Kept broad on purpose: connection
# resets, timeouts, and DNS failures all surface as OSError/TimeoutError
# subclasses or as httpx/requests exceptions whose class names contain
# these substrings.
_TRANSIENT_ERROR_MARKERS = (
    "timeout",
    "connectionerror",
    "connectionreset",
    "connectionrefused",
    "connecterror",
    "remotedisconnected",
    "readtimeout",
    "connecttimeout",
    "gaierror",
    "networkerror",
)


def is_transient_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    return any(marker in name for marker in _TRANSIENT_ERROR_MARKERS) or isinstance(
        exc, (TimeoutError, ConnectionError)
    )


@dataclass
class _ProviderState:
    consecutive_failures: int = 0
    opened_at: float = 0.0


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    _providers: Dict[str, _ProviderState] = field(default_factory=dict)

    def _state(self, provider: str) -> _ProviderState:
        return self._providers.setdefault(provider, _ProviderState())

    def is_open(self, provider: str) -> bool:
        st = self._state(provider)
        if st.consecutive_failures < self.failure_threshold:
            return False
        # Half-open after cooldown: allow one probe through.
        if time.time() - st.opened_at >= self.cooldown_seconds:
            return False
        return True

    def record_success(self, provider: str) -> None:
        st = self._state(provider)
        st.consecutive_failures = 0
        st.opened_at = 0.0

    def record_failure(self, provider: str, exc: BaseException) -> None:
        if not is_transient_error(exc):
            # A non-transient error (bad JSON, model refusal, etc.) is a
            # DLQ candidate but shouldn't trip the breaker — the endpoint
            # is reachable, the record is just hard to resolve.
            return
        st = self._state(provider)
        st.consecutive_failures += 1
        if st.consecutive_failures >= self.failure_threshold and st.opened_at == 0.0:
            st.opened_at = time.time()

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        return {
            provider: {
                "consecutive_failures": st.consecutive_failures,
                "open": self.is_open(provider),
            }
            for provider, st in self._providers.items()
        }
