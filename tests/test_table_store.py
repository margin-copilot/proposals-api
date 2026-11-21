from datetime import UTC, datetime
from decimal import Decimal

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import UpdateMode
from margin_copilot_shared.contracts import Proposal

from proposals_api.adapters.outbound.table_store import TableProposalStore
from proposals_api.domain.rules import Conflict

PROPOSAL = Proposal(
    id="b" * 32,
    region="N",
    sku="DAI-002",
    product="UHT milk 1 L",
    tier="B",
    current_price=Decimal("7.00"),
    new_price=Decimal("7.25"),
    change_pct=Decimal("3.57"),
    approver="manager",
    clause="PP-04 §3.2",
    status="pending",
    proposed_by="oid-north",
    proposed_at=datetime(2026, 11, 21, 9, 0, tzinfo=UTC),
)


class Entity(dict):
    def __init__(self, values, etag="W/1"):
        super().__init__(values)
        self.metadata = {"etag": etag}


class FakeTableClient:
    def __init__(self, modified=False):
        self.entities, self.updates, self.queries, self.modified = {}, [], [], modified

    async def create_entity(self, entity):
        self.entities[(entity["PartitionKey"], entity["RowKey"])] = entity

    async def get_entity(self, partition_key, row_key):
        if (partition_key, row_key) not in self.entities:
            raise ResourceNotFoundError("missing")
        return Entity(self.entities[(partition_key, row_key)])

    async def update_entity(self, entity, **kwargs):
        if self.modified:
            raise ResourceModifiedError("etag mismatch")
        self.updates.append((entity, kwargs))

    def query_entities(self, query_filter, parameters):
        self.queries.append((query_filter, parameters))

        async def matches():
            for entity in self.entities.values():
                if entity["PartitionKey"] == parameters["region"]:
                    yield Entity(entity)

        return matches()


async def test_a_proposal_round_trips_through_entities():
    client = FakeTableClient()
    store = TableProposalStore(client)
    await store.add(PROPOSAL)
    assert client.entities[("N", "b" * 32)]["new_price"] == "7.25"
    assert await store.get("N", "b" * 32) == (PROPOSAL, "W/1")
    assert await store.get("N", "c" * 32) is None
    assert await store.list(("N", "C"), "pending") == [PROPOSAL]
    assert client.queries[0] == (
        "PartitionKey eq @region and status eq @status",
        {"region": "N", "status": "pending"},
    )


async def test_replace_sends_the_etag():
    client = FakeTableClient()
    await TableProposalStore(client).replace(PROPOSAL, "W/7")
    entity, kwargs = client.updates[0]
    assert kwargs == {
        "mode": UpdateMode.REPLACE,
        "etag": "W/7",
        "match_condition": MatchConditions.IfNotModified,
    }
    assert entity["RowKey"] == "b" * 32 and "decided_by" not in entity


async def test_an_etag_mismatch_is_a_conflict():
    with pytest.raises(Conflict, match="changed meanwhile"):
        await TableProposalStore(FakeTableClient(modified=True)).replace(PROPOSAL, "W/7")
