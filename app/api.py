import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request

from app.adapters import DataPlaneClient, FrappeClient, MockDataPlane, MockFrappe
from app.db import Store
from app.service import Bridge
from app.settings import Settings


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


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(cfg.database_url)
        store.health()
        frappe = MockFrappe() if cfg.nextgrp_mode == "mock" else FrappeClient(cfg)
        plane = MockDataPlane() if cfg.data_plane_mode == "mock" else DataPlaneClient(cfg)
        app.state.cfg, app.state.store = cfg, store
        app.state.bridge = Bridge(cfg, store, frappe, plane)
        try:
            yield
        finally:
            frappe.close()
            plane.close()
            store.close()

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
        return [{"kind": kind, "key": key, "priority": priority, "version": version, "payload": payload} for kind, key, priority, version, payload in request.app.state.bridge.snapshot()]

    @app.post("/internal/events/status", status_code=202, dependencies=[Depends(inbound)])
    def status(request: Request, event: dict, idempotency_key: str | None = Header(default=None)):
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        if idempotency_key != event.get("event_id"):
            raise HTTPException(status_code=400, detail="Idempotency-Key must equal event_id")
        try:
            return request.app.state.bridge.receive_status(event)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/internal/events/alarm", status_code=202, dependencies=[Depends(inbound)])
    def alarm(request: Request, event: dict, idempotency_key: str | None = Header(default=None)):
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        if idempotency_key != event.get("event_id"):
            raise HTTPException(status_code=400, detail="Idempotency-Key must equal event_id")
        try:
            return request.app.state.bridge.receive_alarm(event)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/admin/inbox", dependencies=[Depends(admin)])
    def inbox(request: Request, state: str = "dead", limit: int = 100):
        if state not in {"pending", "processing", "done", "dead"}:
            raise HTTPException(status_code=400, detail="Invalid state")
        return request.app.state.store.list_inbox(state, min(max(limit, 1), 500))

    @app.get("/api/devices/{device_id}/latest")
    def latest(request: Request, device_id: str):
        try:
            return request.app.state.bridge.plane.latest(device_id)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Data Plane unavailable") from exc

    @app.get("/api/devices/{device_id}/telemetry")
    def telemetry(request: Request, device_id: str):
        try:
            return request.app.state.bridge.plane.telemetry(device_id)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Data Plane unavailable") from exc

    return app


app = create_app()
