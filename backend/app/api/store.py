"""Run-state storage.

Kept as a plain in-memory dict guarded by an asyncio.Lock for the initial
version, per the spec ("while an in-memory dict is acceptable for initial
setup, structure it so it can easily map to a persistent store like Redis").

To move to Redis later: keep this same `RunStore` interface (get_run,
save_run, find_run_id_by_idempotency_key, save_idempotency_mapping) and swap
the dict-based implementation for redis-py calls (HSET/HGETALL for run
state, SET with TTL for the idempotency-key -> run_id mapping). No caller
outside this file needs to change.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class RunState:
    run_id: str
    status: RunStatus
    provider_mode: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    total_records: int = 0
    fast_path_resolved_so_far: int = 0
    agent_resolved_so_far: int = 0
    results: Optional[Dict[str, Any]] = None
    exceptions: List[Dict[str, Any]] = field(default_factory=list)
    dead_letter_queue: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    idempotency_key: Optional[str] = None

    @property
    def records_processed(self) -> int:
        return self.fast_path_resolved_so_far + self.agent_resolved_so_far

    def to_status_payload(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "records_processed": self.records_processed,
            "total_records": self.total_records,
            "fast_path_resolved_so_far": self.fast_path_resolved_so_far,
            "agent_resolved_so_far": self.agent_resolved_so_far,
        }


class RunStore:
    """Async-safe in-memory store for run state and idempotency mapping."""

    def __init__(self) -> None:
        self._runs: Dict[str, RunState] = {}
        self._idempotency_index: Dict[str, str] = {}  # idempotency_key -> run_id
        self._lock = asyncio.Lock()

    @staticmethod
    def new_run_id() -> str:
        return f"run_{uuid.uuid4().hex[:16]}"

    async def create_run(self, provider_mode: str, idempotency_key: str) -> RunState:
        async with self._lock:
            run_id = self.new_run_id()
            state = RunState(
                run_id=run_id,
                status=RunStatus.PENDING,
                provider_mode=provider_mode,
                idempotency_key=idempotency_key,
            )
            self._runs[run_id] = state
            self._idempotency_index[idempotency_key] = run_id
            return state

    async def get_run(self, run_id: str) -> Optional[RunState]:
        async with self._lock:
            return self._runs.get(run_id)

    async def find_by_idempotency_key(
        self, idempotency_key: str, window_seconds: float
    ) -> Optional[RunState]:
        async with self._lock:
            run_id = self._idempotency_index.get(idempotency_key)
            if run_id is None:
                return None
            state = self._runs.get(run_id)
            if state is None:
                return None
            if state.status == RunStatus.FAILED:
                # Let a failed run be retried rather than returning the
                # same failure forever.
                return None
            if (time.time() - state.created_at) > window_seconds:
                return None
            return state

    async def update_run(self, run_id: str, **fields: Any) -> None:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            for key, value in fields.items():
                setattr(state, key, value)
            state.updated_at = time.time()

    async def bump_fast_progress(self, run_id: str, total: int) -> None:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.fast_path_resolved_so_far += 1
            state.total_records = total
            state.updated_at = time.time()

    async def bump_agent_progress(self, run_id: str) -> None:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.agent_resolved_so_far += 1
            state.updated_at = time.time()

    async def append_exception(self, run_id: str, exc_record: Dict[str, Any]) -> None:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.exceptions.append(exc_record)

    async def append_dlq(self, run_id: str, dlq_record: Dict[str, Any]) -> None:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.dead_letter_queue.append(dlq_record)

    async def find_latest_completed(self) -> Optional[RunState]:
        async with self._lock:
            completed = [s for s in self._runs.values() if s.status == RunStatus.COMPLETED]
            if not completed:
                return None
            return max(completed, key=lambda s: s.updated_at)


# Single process-wide store instance (mirrors how a single Redis connection
# pool would be shared across requests).
run_store = RunStore()
