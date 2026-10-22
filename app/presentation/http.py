import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.application.errors import EventIdConflictError, IntegrationError, OrganizationMismatchError, StorageUnavailableError
from app.application.events import AlarmEvent, StatusEvent


def verify(expected: str, provided: str | None):
    if not provided or not secrets.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Unauthorized")


def admin(request: Request, x_admin_token: str | None = Header(default=None)):
    verify(request.app.state.cfg.admin_token, x_admin_token)


def inbound(request: Request, authorization: str | None = Header(default=None)):
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Unauthorized")
    verify(request.app.state.cfg.inbound_token, token)


def require_idempotency(event_id: str, idempotency_key: str | None):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    if not secrets.compare_digest(idempotency_key, event_id):
        raise HTTPException(status_code=400, detail="Idempotency-Key must equal event_id")


class ConfigRetryRequest(BaseModel):
    kind: str = Field(min_length=1)
    key: str = Field(min_length=1)


def create_app(cfg, bridge_factory) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with bridge_factory(cfg) as bridge:
            app.state.cfg, app.state.store = cfg, bridge.store
            app.state.bridge = bridge
            yield

    app = FastAPI(title="nextGRP Management Bridge", version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request):
        request.app.state.store.health()
        return {"status": "UP", "nextgrp": cfg.nextgrp_mode, "data_plane": cfg.data_plane_mode}

    @app.post("/admin/cycle", dependencies=[Depends(admin)])
    def cycle(request: Request):
        return request.app.state.bridge.cycle()

    @app.get("/internal/snapshot", dependencies=[Depends(admin)])
    def snapshot(request: Request):
        resources = [
            {"kind": kind, "key": key, "priority": priority, "version": version, "payload": payload}
            for kind, key, priority, version, payload in request.app.state.bridge.snapshot()
        ]
        return {"org_code": cfg.org_code, "count": len(resources), "resources": resources}

    @app.post("/internal/events/status", status_code=202, dependencies=[Depends(inbound)])
    def status(request: Request, event: StatusEvent, idempotency_key: str | None = Header(default=None)):
        require_idempotency(event.event_id, idempotency_key)
        try:
            return request.app.state.bridge.receive_status(event.model_dump(mode="json"))
        except OrganizationMismatchError as exc:
            raise HTTPException(status_code=403, detail="Organization mismatch") from exc
        except EventIdConflictError as exc:
            raise HTTPException(status_code=409, detail="EVENT_ID_CONFLICT") from exc
        except StorageUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Inbox temporarily unavailable") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/internal/events/alarm", status_code=202, dependencies=[Depends(inbound)])
    def alarm(request: Request, event: AlarmEvent, idempotency_key: str | None = Header(default=None)):
        require_idempotency(event.event_id, idempotency_key)
        try:
            return request.app.state.bridge.receive_alarm(event.model_dump(mode="json"))
        except OrganizationMismatchError as exc:
            raise HTTPException(status_code=403, detail="Organization mismatch") from exc
        except EventIdConflictError as exc:
            raise HTTPException(status_code=409, detail="EVENT_ID_CONFLICT") from exc
        except StorageUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Inbox temporarily unavailable") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/admin/inbox", dependencies=[Depends(admin)])
    def inbox(request: Request, state: str = "dead", limit: int = 100):
        if state not in {"pending", "processing", "done", "dead"}:
            raise HTTPException(status_code=400, detail="Invalid state")
        return request.app.state.store.list_inbox(state, min(max(limit, 1), 500))

    @app.post("/admin/inbox/{event_id}/retry", dependencies=[Depends(admin)])
    def retry_inbox(request: Request, event_id: str):
        if not request.app.state.store.retry_inbox(event_id):
            raise HTTPException(status_code=404, detail="Dead event not found")
        return {"status": "requeued"}

    @app.get("/admin/config-jobs", dependencies=[Depends(admin)])
    def config_jobs(request: Request, state: str = "dead", limit: int = 100):
        if state not in {"pending", "processing", "done", "dead"}:
            raise HTTPException(status_code=400, detail="Invalid state")
        return request.app.state.store.list_config(state, min(max(limit, 1), 500))

    @app.post("/admin/config-jobs/retry", dependencies=[Depends(admin)])
    def retry_config(request: Request, job: ConfigRetryRequest):
        if not request.app.state.store.retry_config(job.kind, job.key):
            raise HTTPException(status_code=404, detail="Dead config job not found")
        return {"status": "requeued"}

    @app.get("/api/devices/{device_id}/latest")
    def latest(request: Request, device_id: str):
        try:
            return request.app.state.bridge.plane.latest(device_id)
        except IntegrationError as exc:
            raise HTTPException(status_code=502, detail="Data Plane unavailable") from exc

    @app.get("/api/devices/{device_id}/telemetry")
    def telemetry(request: Request, device_id: str):
        try:
            return request.app.state.bridge.plane.telemetry(device_id)
        except IntegrationError as exc:
            raise HTTPException(status_code=502, detail="Data Plane unavailable") from exc

    return app
