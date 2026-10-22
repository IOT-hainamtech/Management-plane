from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class StatusEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=180)
    org_code: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    state: Literal["online", "offline"]
    since: AwareDatetime | None = None
    last_seen: AwareDatetime | None = None
    fw_version: str | None = None
    reason: str | None = None


class SourceEvent(BaseModel):
    """The sensor/rule event that caused an IF-05 alarm message."""

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1, max_length=180)
    event_type: str | None = None
    occurred_at: AwareDatetime | None = None
    data_point: str | None = None
    value: object | None = None


class AlarmEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=180)
    org_code: str = Field(min_length=1)
    alarm_key: str = Field(min_length=1, max_length=180)
    kind: Literal["occurrence", "cleared"]
    rule_id: str = Field(min_length=1)
    rule_version: int | None = Field(default=None, ge=1)
    device_id: str = Field(min_length=1)
    zone_code: str | None = None
    episode_key: str | None = Field(default=None, min_length=1, max_length=180)
    severity: str | None = None
    episode_start: AwareDatetime | None = None
    raised_at: AwareDatetime | None = None
    cleared_at: AwareDatetime | None = None
    occurrence_seq: int = Field(default=1, ge=1)
    event: SourceEvent | None = None

    @model_validator(mode="after")
    def timestamps_match_kind(self):
        if self.kind == "occurrence" and self.raised_at is None:
            raise ValueError("raised_at required for occurrence")
        if self.kind == "cleared" and self.cleared_at is None:
            raise ValueError("cleared_at required for cleared")
        return self
