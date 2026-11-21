import httpx
import pytest

from proposals_api.main import create_app
from tests.helpers import RAW_GROUPS


async def test_fake_mode_requires_tokens(monkeypatch):
    monkeypatch.setenv("ADAPTERS", "fake")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p") as c:
        assert (await c.post("/drafts", json={})).status_code == 401
        assert (await c.get("/proposals")).status_code == 401


AZURE = {
    "ADAPTERS": "azure",
    "TENANT_ID": "11111111-1111-1111-1111-111111111111",
    "CLIENT_ID": "proposals-api-client-id",
    "AZURE_CLIENT_ID": "managed-identity-client-id",
    "DRAFT_CALLERS": "action-agent-client-id",
    "REVIEW_CALLERS": "cli-client-id",
    "REGION_GROUPS": RAW_GROUPS,
    "SQL_SERVER": "sql-example.database.windows.net",
    "SQL_DATABASE": "riad",
    "TABLE_ENDPOINT": "https://stexample.table.core.windows.net",
    "TABLE_NAME": "proposals",
}


@pytest.fixture
def azure_env(monkeypatch):
    for name, value in AZURE.items():
        monkeypatch.setenv(name, value)
    # Both reach out at construction: msal discovers the tenant, the driver loads native code.
    monkeypatch.setattr("proposals_api.main.OboClient", lambda *args: None)
    monkeypatch.setattr("proposals_api.adapters.outbound.azure_sql.default_connect", lambda: None)


def test_azure_mode_composes(azure_env):
    assert create_app() is not None


@pytest.mark.parametrize("missing", sorted(set(AZURE) - {"ADAPTERS"}))
def test_azure_mode_has_no_defaults(azure_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(KeyError):
        create_app()


def test_region_groups_are_checked_at_start(azure_env, monkeypatch):
    monkeypatch.setenv("REGION_GROUPS", RAW_GROUPS.replace("directors:", "director:"))
    with pytest.raises(ValueError):
        create_app()


def test_adapters_must_be_set_explicitly(monkeypatch):
    monkeypatch.delenv("ADAPTERS", raising=False)
    with pytest.raises(KeyError):
        create_app()
    monkeypatch.setenv("ADAPTERS", "prod")
    with pytest.raises(ValueError, match="azure or fake"):
        create_app()
