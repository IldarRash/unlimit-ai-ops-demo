from pydantic import BaseModel, ConfigDict, Field

from apm_demo.common.contracts import ProviderId
from apm_demo.incidents.domain import (
    FeedbackVerdict,
    IncidentRecord,
    IncidentStatus,
)


class AnalyzeIncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderId
    window_seconds: int | None = Field(default=None, ge=15, le=3_600)


class AnalyzeIncidentResponse(BaseModel):
    detected: bool
    incident: IncidentRecord | None


class UpdateIncidentStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: IncidentStatus


class IncidentFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: FeedbackVerdict
    note: str | None = Field(default=None, max_length=1_000)
