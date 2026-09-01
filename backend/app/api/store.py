"""Async-safe transient state for live runs.

Completed results remain durable in results/eval_run_*.json.
This store only tracks live execution state while the FastAPI process is alive.
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
    records_processed: int = 0
    fast_path_resolved_so_far: int = 0
    agent_resolved_so_far: int = 0

    results: Optional[Dict[str, Any]] = None
    exceptions: List[Dict[str, Any]] = field(default_factory=list)
    dead_letter_queue: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    idempotency_key: Optional[str] = None

    def to_status_payload(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "records_processed": self.records_processed,
            "total_records": self.total_records,
            "fast_path_resolved_so_far": self.fast_path_resolved_so_far,
            "agent_resolved_so_far": self.agent_resolved_so_far,
        }


class RunStore:
    def __init__(self) -> None:
        self._runs: Dict[str, RunState] = {}
        self._idempotency_index: Dict[str, str] = {}
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
        self,
        idempotency_key: str,
        window_seconds: float,
    ) -> Optional[RunState]:
        async with self._lock:
            run_id = self._idempotency_index.get(idempotency_key)
            if run_id is None:
                return None

            state = self._runs.get(run_id)
            if state is None:
                return None

            if state.status == RunStatus.FAILED:
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
                if not hasattr(state, key):
                    raise AttributeError(f"Unknown RunState field: {key}")

                setattr(state, key, value)

            state.updated_at = time.time()

    async def record_fast_path_progress(
        self,
        run_id: str,
        total: int,
        result: Optional[Any],
    ) -> None:
        """Record one fast matcher terminal result.

        Only confirmed/flagged records count as processed. Ambiguous/unresolved
        records are handed to the agent stage and count when that stage finishes.
        """
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return

            state.total_records = max(state.total_records, int(total))

            result_status = getattr(result, "status", None) if result is not None else None
            if result_status not in ("confirmed", "flagged"):
                return

            state.records_processed += 1

            if result_status == "confirmed":
                state.fast_path_resolved_so_far += 1

            state.updated_at = time.time()

    async def bump_agent_progress(self, run_id: str) -> None:
        """Record one terminal agent-stage result."""
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return

            state.agent_resolved_so_far += 1
            state.records_processed += 1
            state.updated_at = time.time()

    async def append_exception(self, run_id: str, exc_record: Dict[str, Any]) -> None:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.exceptions.append(exc_record)
            state.updated_at = time.time()

    async def append_dlq(self, run_id: str, dlq_record: Dict[str, Any]) -> None:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.dead_letter_queue.append(dlq_record)
            state.updated_at = time.time()

    async def find_latest_completed(self) -> Optional[RunState]:
        async with self._lock:
            completed = [
                state
                for state in self._runs.values()
                if state.status == RunStatus.COMPLETED
            ]
            if not completed:
                return None
            return max(completed, key=lambda state: state.updated_at)


run_store = RunStore()
