import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from app.presentation.http import create_app


def test_http_layer_receives_injected_bridge_factory():
    cfg = SimpleNamespace(org_code="demo", nextgrp_mode="mock", data_plane_mode="mock")
    bridge = SimpleNamespace(store=SimpleNamespace(health=lambda: None))

    @contextmanager
    def bridge_factory(received_cfg):
        assert received_cfg is cfg
        yield bridge

    app = create_app(cfg, bridge_factory)

    async def check_lifecycle():
        async with app.router.lifespan_context(app):
            assert app.state.bridge is bridge
            assert app.state.store is bridge.store

    asyncio.run(check_lifecycle())
    assert any(route.path == "/internal/events/status" for route in app.routes)
