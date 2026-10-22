"""HTTP composition root; keeps ``uvicorn app.api:app`` stable."""

from app.bootstrap.runtime import open_bridge
from app.infrastructure.settings import Settings
from app.presentation.http import create_app as build_http_app


def create_app(settings: Settings | None = None):
    return build_http_app(settings or Settings(), open_bridge)


app = create_app()

__all__ = ["app", "create_app"]
