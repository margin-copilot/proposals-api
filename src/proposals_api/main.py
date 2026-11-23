"""Composition root. ADAPTERS=azure|fake picks the adapters; there is no default."""

import os

from azure.data.tables.aio import TableClient
from azure.identity.aio import ManagedIdentityCredential
from margin_copilot_shared.security.obo import OboClient, managed_identity_assertion
from margin_copilot_shared.security.tokens import entra_validator
from margin_copilot_shared.telemetry import configure_telemetry, instrument_app
from margin_copilot_shared.testing.issuer import LocalIssuer
from starlette.applications import Starlette
from starlette.types import ASGIApp

from proposals_api.adapters.inbound.http_api import create_http_app
from proposals_api.adapters.outbound.azure_sql import AzureSqlPriceBook
from proposals_api.adapters.outbound.fakes import FakePriceBook, MemoryProposalStore
from proposals_api.adapters.outbound.table_store import TableProposalStore
from proposals_api.domain.rules import parse_region_groups

FAKE_GROUPS = (
    "north:11111111-0000-0000-0000-000000000001,center:11111111-0000-0000-0000-000000000002,"
    "south:11111111-0000-0000-0000-000000000003,directors:11111111-0000-0000-0000-000000000004"
)


def create_app() -> ASGIApp:
    configure_telemetry("proposals-api", os.environ)
    return instrument_app(_app())


def _app() -> Starlette:
    env = os.environ
    adapters = env["ADAPTERS"]
    if adapters == "azure":
        tenant_id, client_id, identity = env["TENANT_ID"], env["CLIENT_ID"], env["AZURE_CLIENT_ID"]
        draft_callers = frozenset(env["DRAFT_CALLERS"].split(","))
        review_callers = frozenset(env["REVIEW_CALLERS"].split(","))
        groups = parse_region_groups(env["REGION_GROUPS"])
        obo = OboClient(tenant_id, client_id, managed_identity_assertion(identity))
        prices = AzureSqlPriceBook(env["SQL_SERVER"], env["SQL_DATABASE"], obo)
        table = TableClient(
            endpoint=env["TABLE_ENDPOINT"],
            table_name=env["TABLE_NAME"],
            credential=ManagedIdentityCredential(client_id=identity),
        )
        return create_http_app(
            prices,
            TableProposalStore(table),
            groups,
            entra_validator(tenant_id, client_id, draft_callers),
            entra_validator(tenant_id, client_id, review_callers),
            allowed_origins=[o.strip() for o in env["ALLOWED_ORIGINS"].split(",") if o.strip()],
        )
    if adapters == "fake":
        # A fresh in-memory key per process: nobody outside this process can mint a token for it.
        issuer = LocalIssuer()
        return create_http_app(
            FakePriceBook(),
            MemoryProposalStore(),
            parse_region_groups(FAKE_GROUPS),
            issuer.validator(audience="proposals-api", allowed_callers=frozenset({"action-agent"})),
            issuer.validator(audience="proposals-api", allowed_callers=frozenset({"cli"})),
            allowed_origins=["http://localhost:5173"],
        )
    raise ValueError(f"ADAPTERS must be azure or fake, not {adapters!r}")
