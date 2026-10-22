from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for one Management Bridge organization."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    org_code: str = "demo"
    org_name: str = "Demo Organization"

    nextgrp_mode: str = "mock"
    nextgrp_url: str = ""
    nextgrp_api_key: str = ""
    nextgrp_api_secret: str = ""
    nextgrp_organization_name: str = ""

    data_plane_mode: str = "mock"
    data_plane_url: str = ""
    data_plane_token: str = ""

    inbound_token: str
    admin_token: str
    database_url: str
    poll_seconds: int = Field(default=30, ge=5, le=3600)
    sync_batch_size: int = Field(default=100, ge=1, le=1000)
    inbox_batch_size: int = Field(default=100, ge=1, le=1000)

    device_status_online: str = "Online"
    device_status_offline: str = "Offline"
    alarm_status_new: str = "New"
    alarm_status_cleared: str = "Cleared"
    alarm_ack_status: str = "Acknowledged"
    alarm_processing_status: str = "In Progress"
    alarm_closed_status: str = "Closed"

    @model_validator(mode="after")
    def validate_modes_and_credentials(self):
        if self.nextgrp_mode not in {"mock", "live"}:
            raise ValueError("NEXTGRP_MODE must be mock or live")
        if self.data_plane_mode not in {"mock", "live"}:
            raise ValueError("DATA_PLANE_MODE must be mock or live")
        if self.inbound_token == self.admin_token:
            raise ValueError("INBOUND_TOKEN and ADMIN_TOKEN must differ")
        if self.nextgrp_mode == "live" and not (
            self.nextgrp_url.startswith("https://")
            and self.nextgrp_api_key
            and self.nextgrp_api_secret
            and self.nextgrp_organization_name
        ):
            raise ValueError("Live nextGRP requires HTTPS URL, API credentials, and NEXTGRP_ORGANIZATION_NAME")
        if self.data_plane_mode == "live" and not self.data_plane_url.startswith(("http://", "https://")):
            raise ValueError("DATA_PLANE_URL must be an HTTP(S) URL")
        if not self.database_url.startswith("postgresql+"):
            raise ValueError("DATABASE_URL must use the PostgreSQL SQLAlchemy dialect")
        return self
