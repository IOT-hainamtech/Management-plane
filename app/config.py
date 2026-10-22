import json
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {
        "true", "1", "yes"
    }


def _map(name: str, default: str) -> dict[str, str]:
    value = json.loads(os.getenv(name, default))

    if not isinstance(value, dict):
        raise ValueError(f"{name} must be JSON object")

    return value


@dataclass
class Settings:
    env: str = field(
        default_factory=lambda: os.getenv(
            "APP_ENV", "development"
        )
    )

    nextgrp_mode: str = field(
        default_factory=lambda: os.getenv(
            "NEXTGRP_MODE", "mock"
        )
    )

    nextgrp_url: str = field(
        default_factory=lambda: os.getenv(
            "NEXTGRP_BASE_URL", ""
        )
    )

    nextgrp_key: str = field(
        default_factory=lambda: os.getenv(
            "NEXTGRP_API_KEY", ""
        )
    )

    nextgrp_secret: str = field(
        default_factory=lambda: os.getenv(
            "NEXTGRP_API_SECRET", ""
        )
    )

    dp_mode: str = field(
        default_factory=lambda: os.getenv(
            "DATA_PLANE_MODE", "mock"
        )
    )

    dp_url: str = field(
        default_factory=lambda: os.getenv(
            "DATA_PLANE_BASE_URL", ""
        )
    )

    dp_token: str = field(
        default_factory=lambda: os.getenv(
            "DATA_PLANE_API_TOKEN", ""
        )
    )

    admin_token: str = field(
        default_factory=lambda: os.getenv(
            "ADMIN_TOKEN", ""
        )
    )

    event_token: str = field(
        default_factory=lambda: os.getenv(
            "EVENT_TOKEN", ""
        )
    )

    database: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_PATH", "./var/integration.db"
        )
    )

    run_worker: bool = field(
        default_factory=lambda: _bool(
            "RUN_WORKER", "true"
        )
    )

    interval: int = field(
        default_factory=lambda: int(
            os.getenv("SYNC_INTERVAL_SECONDS", "30")
        )
    )

    online_map: dict[str, str] = field(
        default_factory=lambda: _map(
            "FRAPPE_ONLINE_STATE_MAP",
            '{"online":"Online","offline":"Offline"}'
        )
    )

    alarm_new: str = field(
        default_factory=lambda: os.getenv(
            "FRAPPE_ALARM_NEW_STATUS", ""
        )
    )

    alarm_cleared: str = field(
        default_factory=lambda: os.getenv(
            "FRAPPE_ALARM_CLEARED_STATUS", ""
        )
    )

    alarm_state_map: dict[str, str] = field(
        default_factory=lambda: _map(
            "FRAPPE_ALARM_TO_DP_STATE_MAP", "{}"
        )
    )

    def validate(self) -> None:
        if self.nextgrp_mode not in {"mock", "live"}:
            raise ValueError("Invalid NEXTGRP_MODE")

        if self.dp_mode not in {"mock", "live"}:
            raise ValueError("Invalid DATA_PLANE_MODE")

        if not 5 <= self.interval <= 3600:
            raise ValueError(
                "SYNC_INTERVAL_SECONDS must be 5..3600"
            )

        for key, token in {
            "ADMIN_TOKEN": self.admin_token,
            "EVENT_TOKEN": self.event_token
        }.items():
            if len(token) < 24 or token.startswith("replace-"):
                raise ValueError(
                    f"Set a strong {key} (>=24 characters)"
                )

        if self.admin_token == self.event_token:
            raise ValueError(
                "ADMIN_TOKEN and EVENT_TOKEN must differ"
            )

        if self.nextgrp_mode == "live":
            if not (
                self.nextgrp_url.startswith("https://")
                and self.nextgrp_key
                and self.nextgrp_secret
            ):
                raise ValueError(
                    "NextGRP live requires HTTPS and credentials"
                )

        if self.dp_mode == "live":
            if not self.dp_url.startswith(
                ("http://", "https://")
            ):
                raise ValueError(
                    "Invalid DATA_PLANE_BASE_URL"
                )