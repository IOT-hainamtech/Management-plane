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
    event_type: str = Field(min_length=1)
    ts: AwareDatetime
    data: dict = Field(default_factory=dict)


class AlarmEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=180)
    org_code: str = Field(min_length=1)
    alarm_key: str = Field(min_length=1, max_length=180)
    kind: Literal["occurrence", "cleared"]
    rule_id: str = Field(min_length=1)
    rule_version: int = Field(ge=1)
    device_id: str = Field(min_length=1)
    zone_code: str | None = None
    episode_key: str = Field(min_length=1, max_length=180)
    severity: str | None = None
    episode_start: AwareDatetime
    raised_at: AwareDatetime | None = None
    cleared_at: AwareDatetime | None = None
    occurrence_seq: int = Field(ge=1)
    event: SourceEvent

    @model_validator(mode="after")
    def timestamps_match_kind(self):
        if self.episode_key != self.alarm_key:
            raise ValueError("episode_key must equal alarm_key")
        if self.kind == "occurrence" and self.raised_at is None:
            raise ValueError("raised_at required for occurrence")
        if self.kind == "cleared" and self.cleared_at is None:
            raise ValueError("cleared_at required for cleared")
        return self
