import asyncio
import logging

from contextlib import asynccontextmanager

import httpx

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request
)

from app.config import Settings

from app.clients.frappe import FrappeClient
from app.clients.data_plane import DataPlaneClient
from app.clients.mock import (
    MockFrappe,
    MockDataPlane
)

from app.infrastructure.store import StateStore

from app.services.config_sync import ConfigSync
from app.services.event_sync import EventSync
from app.services.alarm_state import AlarmStateSync

from app.schemas import (
    StatusEvent,
    AlarmEvent
)

from app.security import (
    admin_guard,
    event_guard
)

from app.worker import Worker


logging.basicConfig(
    level=logging.INFO
)


def create_app(
    settings: Settings | None = None
):
    cfg = settings or Settings()
    cfg.validate()

    @asynccontextmanager
    async def lifespan(app: FastAPI):

        if cfg.nextgrp_mode == "mock":
            frappe = MockFrappe()
        else:
            frappe = FrappeClient(
                cfg.nextgrp_url,
                cfg.nextgrp_key,
                cfg.nextgrp_secret
            )

        if cfg.dp_mode == "mock":
            plane = MockDataPlane()
        else:
            plane = DataPlaneClient(
                cfg.dp_url,
                cfg.dp_token
            )

        store = StateStore(
            cfg.database
        )

        await store.connect()

        config_sync = ConfigSync(
            frappe,
            plane,
            store
        )

        event_sync = EventSync(
            frappe,
            cfg
        )

        alarm_sync = AlarmStateSync(
            frappe,
            plane,
            store,
            cfg.alarm_state_map
        )

        worker = Worker(
            config_sync,
            event_sync,
            alarm_sync,
            store,
            cfg.interval
        )

        app.state.settings = cfg
        app.state.frappe = frappe
        app.state.plane = plane
        app.state.worker = worker
        app.state.store = store

        task = None

        if cfg.run_worker:
            task = asyncio.create_task(
                worker.run()
            )

        try:
            yield

        finally:
            worker.stop()

            if task:
                await task

            await frappe.close()
            await plane.close()

    app = FastAPI(
        title="NextWireless Management Bridge",
        version="1.0.0",
        lifespan=lifespan
    )

    @app.get("/health")
    async def health():
        return {
            "status": "UP",
            "nextgrp": cfg.nextgrp_mode,
            "data_plane": cfg.dp_mode
        }

    @app.get(
        "/admin/check-nextgrp",
        dependencies=[Depends(admin_guard)]
    )
    async def check_nextgrp(request: Request):

        try:
            user = await (
                request.app.state.frappe.check()
            )

            return {
                "user": user
            }

        except httpx.HTTPError as exc:
            raise HTTPException(
                502,
                "Cannot authenticate to nextGRP"
            ) from exc

    @app.post(
        "/admin/sync",
        dependencies=[Depends(admin_guard)]
    )
    async def sync_once(request: Request):

        try:
            return await (
                request.app.state.worker.once()
            )

        except httpx.HTTPError as exc:
            raise HTTPException(
                502,
                "External API temporarily unavailable"
            ) from exc

    @app.get(
        "/admin/inbox",
        dependencies=[Depends(admin_guard)]
    )
    async def inbox(
        request: Request,
        state: str = "dead"
    ):
        if state not in {
            "pending",
            "processing",
            "done",
            "dead"
        }:
            raise HTTPException(
                400,
                "Invalid state"
            )

        return await (
            request.app.state.store.list_inbox(
                state
            )
        )

    @app.post(
        "/admin/inbox/{event_id}/retry",
        dependencies=[Depends(admin_guard)]
    )
    async def retry_event(
        request: Request,
        event_id: str
    ):
        retried = await (
            request.app.state.store.retry(
                event_id
            )
        )

        if not retried:
            raise HTTPException(
                404,
                "Dead event not found"
            )

        return {
            "status": "requeued"
        }

    @app.post(
        "/internal/events/status",
        dependencies=[Depends(event_guard)],
        status_code=202
    )
    async def incoming_status(
        request: Request,
        event: StatusEvent
    ):
        eid = event.event_id

        if not eid:
            if not (
                event.since
                or event.last_seen
            ):
                raise HTTPException(
                    422,
                    "Provide event_id or "
                    "since/last_seen for stable deduplication"
                )

            eid = (
                f"status:{event.device_id}:"
                f"{event.state}:"
                f"{event.since or event.last_seen}"
            )

        try:
            inserted = await (
                request.app.state.store.enqueue(
                    eid,
                    "status",
                    event.model_dump()
                )
            )

        except ValueError as exc:
            raise HTTPException(
                409,
                str(exc)
            ) from exc

        return {
            "accepted": True,
            "duplicate": not inserted
        }

    @app.post(
        "/internal/events/alarm",
        dependencies=[Depends(event_guard)],
        status_code=202
    )
    async def incoming_alarm(
        request: Request,
        event: AlarmEvent
    ):
        eid = (
            event.event_id
            or event.event.get("event_id")
            or (
                f"alarm:{event.alarm_key}:"
                f"{event.kind}:"
                f"{event.occurrence_seq}:"
                f"{event.raised_at}"
            )
        )

        try:
            inserted = await (
                request.app.state.store.enqueue(
                    eid,
                    "alarm",
                    event.model_dump()
                )
            )

        except ValueError as exc:
            raise HTTPException(
                409,
                str(exc)
            ) from exc

        return {
            "accepted": True,
            "duplicate": not inserted
        }

    @app.get(
        "/api/devices/{device_id}/latest",
        dependencies=[Depends(admin_guard)]
    )
    async def latest(
        request: Request,
        device_id: str
    ):
        try:
            return await (
                request.app.state.plane.latest(
                    device_id
                )
            )

        except httpx.HTTPError as exc:
            raise HTTPException(
                502,
                "Data Plane is unavailable"
            ) from exc

    @app.get(
        "/api/devices/{device_id}/telemetry",
        dependencies=[Depends(admin_guard)]
    )
    async def telemetry(
        request: Request,
        device_id: str
    ):
        try:
            return await (
                request.app.state.plane.telemetry(
                    device_id
                )
            )

        except httpx.HTTPError as exc:
            raise HTTPException(
                502,
                "Data Plane is unavailable"
            ) from exc

    @app.get(
        "/api/zones/{zone_code}/summary",
        dependencies=[Depends(admin_guard)]
    )
    async def summary(
        request: Request,
        zone_code: str
    ):
        try:
            return await (
                request.app.state.plane.zone_summary(
                    zone_code
                )
            )

        except httpx.HTTPError as exc:
            raise HTTPException(
                502,
                "Data Plane is unavailable"
            ) from exc

    return app