from pydantic import BaseModel, ConfigDict, Field

from apm_demo.common.contracts import ProviderId, ScenarioName
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


class DemoTrafficPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    requests_per_second: float | None = Field(default=None, gt=0, le=100)


class DemoScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderId
    scenario: ScenarioName
