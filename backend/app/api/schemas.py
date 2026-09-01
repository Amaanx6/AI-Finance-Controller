from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RunStartResponse(BaseModel):
    run_id: str
    status: str
    provider_mode: str


class RunStatusResponse(BaseModel):
    status: str = Field(..., description="pending | running | completed | failed")
    records_processed: int
    total_records: int
    fast_path_resolved_so_far: int
    agent_resolved_so_far: int
    error: Optional[str] = None


class ScorePattern(BaseModel):
    total_records: int
    accuracy: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None


class ScoreBlock(BaseModel):
    overall: Dict[str, Any]
    by_pattern: Dict[str, ScorePattern]
    detail_rows: List[Dict[str, Any]]


class RunResultsResponse(BaseModel):
    # New runs persist this value. Optional preserves compatibility with older
    # eval_run_*.json files created before run_id was added.
    run_id: Optional[str] = None
    run_started_at: str
    timestamp: str
    provider_mode: str
    total_records: int
    overall_match_rate: Optional[float] = None
    breakdown: Dict[str, int]
    financial_impact: Dict[str, Any] = Field(default_factory=dict)
    full_pipeline_scores: Dict[str, Any]
    baseline_scores: Dict[str, Any]
    performance: Dict[str, Any]
    ground_truth_orphan_rows_excluded: int
    dead_letter_queue: List[Dict[str, Any]] = Field(default_factory=list)
    caveats: List[str] = Field(default_factory=list)
    reproducibility: Dict[str, Any] = Field(default_factory=dict)


class RunResultsSummary(BaseModel):
    """Compact durable run record used by the dashboard archive."""
    run_id: Optional[str] = None
    run_started_at: Optional[str] = None
    timestamp: Optional[str] = None
    provider_mode: Optional[str] = None
    total_records: Optional[int] = None
    overall_match_rate: Optional[float] = None
    breakdown: Dict[str, int] = Field(default_factory=dict)


class ReasoningTraceResponse(BaseModel):
    record_id: str
    handled_by_key: Optional[str] = None
    provider: Optional[str] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)
    final_status: Optional[str] = None
    final_decision: Optional[Dict[str, Any]] = None
    wall_clock_time_sec: Optional[float] = None
    active_processing_time_sec: Optional[float] = None
    reactive_throttle_wait_sec: Optional[float] = None
    self_paced_wait_sec: Optional[float] = None
    other_pacing_wait_sec: Optional[float] = None


class ExceptionRecord(BaseModel):
    record_id: str
    stage: str = Field(..., description="fast_path | agent_resolution | circuit_breaker")
    reason: str
    provider: Optional[str] = None
    detail: Optional[str] = None


class RunExceptionsResponse(BaseModel):
    run_id: str
    exceptions: List[ExceptionRecord]
    dead_letter_queue: List[ExceptionRecord]


class ErrorResponse(BaseModel):
    detail: str
