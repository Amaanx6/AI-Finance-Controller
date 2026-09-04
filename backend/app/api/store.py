"""Async-safe transient state for live runs.

Completed results remain durable in results/eval_run_*.json.
This store only tracks live execution state while the FastAPI process is alive.
"""
from __future__ import annotations

import asyncio
import time
import uuid
import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

from dotenv import load_dotenv

_REPO_DIR = Path(__file__).resolve().parents[3]
load_dotenv(_REPO_DIR / ".env.local")
load_dotenv(_REPO_DIR / "frontend" / ".env.local")


class UpstashRest:
    def __init__(self, url: str, token: str) -> None:
        self.endpoint = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    async def command(self, *args: Any) -> Any:
        async with httpx.AsyncClient(headers=self.headers, timeout=10.0) as client:
            response = await client.post(self.endpoint, json=list(args))
            response.raise_for_status()
            body = response.json()
            return body.get("result") if isinstance(body, dict) else body

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> Any:
        return await self.command("SET", key, value, *("EX", ex) if ex else ())

    async def get(self, key: str) -> Any:
        return await self.command("GET", key)

    async def rpush(self, key: str, value: str) -> Any:
        return await self.command("RPUSH", key, value)

    async def lpush(self, key: str, value: str) -> Any:
        return await self.command("LPUSH", key, value)

    async def lpop(self, key: str) -> Any:
        return await self.command("LPOP", key)

    async def lrange(self, key: str, start: int, stop: int) -> Any:
        return await self.command("LRANGE", key, start, stop)

    async def ltrim(self, key: str, start: int, stop: int) -> Any:
        return await self.command("LTRIM", key, start, stop)

    async def lrem(self, key: str, count: int, value: str) -> Any:
        return await self.command("LREM", key, count, value)

    async def expire(self, key: str, seconds: int) -> Any:
        return await self.command("EXPIRE", key, seconds)


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    input_dir: Optional[str] = None
    dataset_manifest: Dict[str, Any] = field(default_factory=dict)

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
        self._session_runs: Dict[str, List[str]] = {}
        self._tasks: Dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        self._state_dir = Path(__file__).resolve().parents[3] / "run_state"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._redis = None
        redis_url = os.environ.get("REDIS_URL")
        rest_url = os.environ.get("UPSTASH_REDIS_REST_URL")
        rest_token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        if rest_url and rest_token:
            self._redis = UpstashRest(rest_url, rest_token)
        elif redis_url:
            try:
                from redis.asyncio import Redis
                self._redis = Redis.from_url(redis_url, decode_responses=True)
            except ImportError:
                self._redis = None

    @property
    def redis_enabled(self) -> bool:
        return self._redis is not None

    @staticmethod
    def _key(run_id: str) -> str:
        return f"reconcile:run:{run_id}"

    @staticmethod
    def _queue_key() -> str:
        return "reconcile:run-queue"

    def _payload(self, state: RunState) -> Dict[str, Any]:
        return {
            "run_id": state.run_id,
            "status": state.status.value,
            "provider_mode": state.provider_mode,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "total_records": state.total_records,
            "records_processed": state.records_processed,
            "fast_path_resolved_so_far": state.fast_path_resolved_so_far,
            "agent_resolved_so_far": state.agent_resolved_so_far,
            "results": state.results,
            "exceptions": state.exceptions,
            "dead_letter_queue": state.dead_letter_queue,
            "error": state.error,
            "idempotency_key": state.idempotency_key,
            "input_dir": state.input_dir,
            "dataset_manifest": state.dataset_manifest,
        }

    async def _persist_remote(self, state: RunState) -> None:
        if self._redis is None:
            return
        try:
            existing = await self._redis.get(self._key(state.run_id))
            if existing:
                existing_payload = json.loads(existing)
                if existing_payload.get("status") == RunStatus.CANCELLED.value and state.status != RunStatus.CANCELLED:
                    return
            await self._redis.set(self._key(state.run_id), json.dumps(self._payload(state), default=str), ex=604800)
            if state.idempotency_key:
                await self._redis.set(
                    f"reconcile:idempotency:{state.idempotency_key}",
                    state.run_id,
                    ex=86400,
                )
        except Exception:
            # The local snapshot remains the development and outage fallback.
            return

    @staticmethod
    def _from_payload(payload: Dict[str, Any]) -> RunState:
        return RunState(
            run_id=str(payload["run_id"]),
            status=RunStatus(str(payload.get("status", "pending"))),
            provider_mode=str(payload.get("provider_mode", "auto")),
            created_at=float(payload.get("created_at", time.time())),
            updated_at=float(payload.get("updated_at", time.time())),
            total_records=int(payload.get("total_records", 0)),
            records_processed=int(payload.get("records_processed", 0)),
            fast_path_resolved_so_far=int(payload.get("fast_path_resolved_so_far", 0)),
            agent_resolved_so_far=int(payload.get("agent_resolved_so_far", 0)),
            results=payload.get("results"),
            exceptions=payload.get("exceptions") or [],
            dead_letter_queue=payload.get("dead_letter_queue") or [],
            error=payload.get("error"),
            idempotency_key=payload.get("idempotency_key"),
            input_dir=payload.get("input_dir"),
            dataset_manifest=payload.get("dataset_manifest") or {},
        )

    def _persist(self, state: RunState) -> None:
        path = self._state_dir / f"{state.run_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "run_id": state.run_id,
            "status": state.status.value,
            "provider_mode": state.provider_mode,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "total_records": state.total_records,
            "records_processed": state.records_processed,
            "fast_path_resolved_so_far": state.fast_path_resolved_so_far,
            "agent_resolved_so_far": state.agent_resolved_so_far,
            "results": state.results,
            "exceptions": state.exceptions,
            "dead_letter_queue": state.dead_letter_queue,
            "error": state.error,
            "idempotency_key": state.idempotency_key,
            "input_dir": state.input_dir,
            "dataset_manifest": state.dataset_manifest,
        }, default=str), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def new_run_id() -> str:
        return f"run_{uuid.uuid4().hex[:16]}"

    async def create_run(
        self,
        provider_mode: str,
        idempotency_key: str,
        input_dir: Optional[str] = None,
        dataset_manifest: Optional[Dict[str, Any]] = None,
    ) -> RunState:
        async with self._lock:
            run_id = self.new_run_id()
            state = RunState(
                run_id=run_id,
                status=RunStatus.PENDING,
                provider_mode=provider_mode,
                idempotency_key=idempotency_key,
                input_dir=input_dir,
                dataset_manifest=dataset_manifest or {},
            )
            self._runs[run_id] = state
            self._idempotency_index[idempotency_key] = run_id
            self._persist(state)
            await self._persist_remote(state)
            return state

    async def get_run(self, run_id: str) -> Optional[RunState]:
        async with self._lock:
            state = self._runs.get(run_id)
            payload = None
            if self._redis is not None:
                try:
                    remote = await self._redis.get(self._key(run_id))
                    payload = json.loads(remote) if remote else None
                except Exception as exc:
                    print(f"[run-store] Redis read failed for {run_id}: {type(exc).__name__}: {exc}")
                    payload = None
            snapshot = self._state_dir / f"{run_id}.json"
            if payload is None and snapshot.exists():
                try:
                    payload = json.loads(snapshot.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    payload = None
            if not isinstance(payload, dict):
                return state
            state = self._from_payload(payload)
            self._runs[run_id] = state
            if state.idempotency_key:
                self._idempotency_index[state.idempotency_key] = run_id
            return state

    async def find_by_idempotency_key(
        self,
        idempotency_key: str,
        window_seconds: float,
    ) -> Optional[RunState]:
        async with self._lock:
            run_id = self._idempotency_index.get(idempotency_key)
            if run_id is None and self._redis is not None:
                try:
                    remote_id = await self._redis.get(f"reconcile:idempotency:{idempotency_key}")
                    run_id = str(remote_id) if remote_id else None
                except Exception:
                    run_id = None
            if run_id is None:
                return None

            state = self._runs.get(run_id)
            if state is None:
                snapshot = self._state_dir / f"{run_id}.json"
                try:
                    payload = json.loads(snapshot.read_text(encoding="utf-8")) if snapshot.exists() else None
                except (OSError, json.JSONDecodeError):
                    payload = None
                if payload is None and self._redis is not None:
                    try:
                        remote = await self._redis.get(self._key(run_id))
                        payload = json.loads(remote) if remote else None
                    except Exception:
                        payload = None
                if isinstance(payload, dict):
                    state = self._from_payload(payload)
                    self._runs[run_id] = state
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

            # Cancellation is terminal. A worker that finishes after the user
            # cancelled the run must never be allowed to overwrite it with
            # COMPLETED or FAILED.
            if state.status == RunStatus.CANCELLED and fields.get('status') != RunStatus.CANCELLED:
                return

            for key, value in fields.items():
                if not hasattr(state, key):
                    raise AttributeError(f"Unknown RunState field: {key}")

                setattr(state, key, value)

            state.updated_at = time.time()
            self._persist(state)
            await self._persist_remote(state)

    async def register_task(self, run_id: str, task: asyncio.Task[Any]) -> None:
        async with self._lock:
            self._tasks[run_id] = task

    async def remove_task(self, run_id: str) -> None:
        async with self._lock:
            self._tasks.pop(run_id, None)

    async def cancel_run(self, run_id: str) -> Optional[RunState]:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return None
            if state.status in (RunStatus.COMPLETED, RunStatus.FAILED):
                return state
            if state.status == RunStatus.CANCELLED:
                return state

            state.status = RunStatus.CANCELLED
            state.error = "Run cancelled by the user."
            state.updated_at = time.time()
            self._persist(state)
            await self._persist_remote(state)

            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                task.cancel()

            return state

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
            self._persist(state)
            await self._persist_remote(state)

    async def bump_agent_progress(self, run_id: str) -> None:
        """Record one terminal agent-stage result."""
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return

            state.agent_resolved_so_far += 1
            state.records_processed += 1
            state.updated_at = time.time()
            self._persist(state)
            await self._persist_remote(state)

    async def append_exception(self, run_id: str, exc_record: Dict[str, Any]) -> None:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.exceptions.append(exc_record)
            state.updated_at = time.time()
            self._persist(state)
            await self._persist_remote(state)

    async def append_dlq(self, run_id: str, dlq_record: Dict[str, Any]) -> None:
        async with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            state.dead_letter_queue.append(dlq_record)
            state.updated_at = time.time()
            self._persist(state)
            await self._persist_remote(state)

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

    async def enqueue(self, run_id: str) -> bool:
        if self._redis is None:
            return False
        try:
            await self._redis.rpush(self._queue_key(), run_id)
            return True
        except Exception:
            return False

    async def dequeue(self, timeout: int = 5) -> Optional[str]:
        if self._redis is None:
            return None
        try:
            item = await self._redis.lpop(self._queue_key())
            return str(item) if item else None
        except Exception:
            return None

    async def attach_session(self, session_id: str, run_id: str) -> None:
        if not session_id:
            return
        async with self._lock:
            runs = self._session_runs.setdefault(session_id, [])
            if run_id not in runs:
                runs.insert(0, run_id)
                del runs[20:]
            if self._redis is not None:
                try:
                    key = f"reconcile:session:{session_id}:runs"
                    await self._redis.lrem(key, 0, run_id)
                    await self._redis.lpush(key, run_id)
                    await self._redis.ltrim(key, 0, 19)
                    await self._redis.expire(key, 2592000)
                except Exception:
                    return

    async def session_run_ids(self, session_id: str) -> List[str]:
        async with self._lock:
            if self._redis is not None:
                try:
                    return [str(item) for item in await self._redis.lrange(
                        f"reconcile:session:{session_id}:runs", 0, 19
                    )]
                except Exception:
                    pass
            return list(self._session_runs.get(session_id, []))


run_store = RunStore()
