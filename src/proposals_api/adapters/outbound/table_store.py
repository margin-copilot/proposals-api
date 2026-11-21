"""ProposalStore on Azure Table Storage (D-010): PartitionKey = region, RowKey = proposal ID.
Keyless: the service's managed identity holds Storage Table Data Contributor on this table only."""

from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import UpdateMode
from margin_copilot_shared.contracts import Proposal

from proposals_api.domain.rules import Conflict


def _entity(proposal: Proposal) -> dict[str, Any]:
    values = proposal.model_dump(mode="json", exclude_none=True)  # money and times as strings
    return {"PartitionKey": values.pop("region"), "RowKey": values.pop("id"), **values}


def _proposal(entity: dict[str, Any]) -> Proposal:
    values = dict(entity)
    return Proposal(region=values.pop("PartitionKey"), id=values.pop("RowKey"), **values)


class TableProposalStore:
    def __init__(self, client: Any) -> None:  # azure.data.tables.aio.TableClient
        self._client = client

    async def add(self, proposal: Proposal) -> None:
        await self._client.create_entity(entity=_entity(proposal))

    async def get(self, region: str, proposal_id: str) -> tuple[Proposal, str] | None:
        try:
            entity = await self._client.get_entity(partition_key=region, row_key=proposal_id)
        except ResourceNotFoundError:
            return None
        return _proposal(entity), entity.metadata["etag"]

    async def replace(self, proposal: Proposal, etag: str) -> None:
        try:
            await self._client.update_entity(
                entity=_entity(proposal),
                mode=UpdateMode.REPLACE,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except ResourceModifiedError:
            raise Conflict("changed meanwhile") from None

    async def list(self, regions: tuple[str, ...], status: str) -> list[Proposal]:
        found = []
        for region in regions:
            entities = self._client.query_entities(
                query_filter="PartitionKey eq @region and status eq @status",
                parameters={"region": region, "status": status},
            )
            found.extend([_proposal(entity) async for entity in entities])
        return found
