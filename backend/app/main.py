from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.routes import router as api_router
from backend.app.api import job
from backend.app.api.store import run_store

@asynccontextmanager
async def lifespan(_app: FastAPI):
    worker = None
    if run_store.redis_enabled:
        worker = asyncio.create_task(_run_queue_worker())
    try:
        yield
    finally:
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


app = FastAPI(
    title="Financial Reconciliation API",
    description=(
        "Async job pattern around the provider-agnostic reconciliation "
        "pipeline (fast-path matcher + proposer/verifier agent resolution)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


async def _run_queue_worker() -> None:
    while True:
        run_id = await run_store.dequeue(timeout=5)
        if run_id:
            try:
                await job.execute_run(run_id)
            except Exception as exc:  # noqa: BLE001 - isolate one queued run
                await run_store.update_run(
                    run_id,
                    status="failed",
                    error=f"Queued run failed: {exc}",
                )
        else:
            await asyncio.sleep(1)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
