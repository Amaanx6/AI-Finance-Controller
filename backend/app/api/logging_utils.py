"""Structured logging for the reconciliation API.

Emits single-line JSON log records instead of ad-hoc print()s so logs are
parseable by any log aggregator (CloudWatch, Loki, Datadog, etc.) once this
runs in a container. Every event carries a trace_id (the run_id) so all log
lines for one run can be correlated.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("reconciliation_api")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log_event(
    event: str,
    trace_id: str,
    *,
    record_id: Optional[str] = None,
    provider: Optional[str] = None,
    latency_sec: Optional[float] = None,
    level: str = "info",
    **extra: Any,
) -> None:
    payload: Dict[str, Any] = {
        "ts": time.time(),
        "event": event,
        "trace_id": trace_id,
    }
    if record_id is not None:
        payload["record_id"] = record_id
    if provider is not None:
        payload["provider"] = provider
    if latency_sec is not None:
        payload["latency_sec"] = round(latency_sec, 3)
    payload.update(extra)

    line = json.dumps(payload, default=str)
    getattr(logger, level, logger.info)(line)
