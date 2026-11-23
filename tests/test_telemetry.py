import httpx
from margin_copilot_shared.security.context import request_scope
from margin_copilot_shared.testing.telemetry import recorded_spans
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

from proposals_api.main import create_app
from tests.test_azure_sql import USER, FakeDriver, price_book


async def test_a_price_lookup_is_a_span():
    with recorded_spans() as spans, request_scope(USER):
        await price_book(FakeDriver([("7.00",)])).current_price("DAI-002", "N")
    (looked_up,) = [s for s in spans if s.name == "proposals_api.sql"]
    assert looked_up.attributes["margin.sql.rows"] == 1


async def test_the_app_is_wrapped_and_untraced_without_a_connection_string(monkeypatch):
    monkeypatch.setenv("ADAPTERS", "fake")
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    app = create_app()
    assert isinstance(app, OpenTelemetryMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p") as c:
        assert (await c.get("/proposals")).status_code == 401
