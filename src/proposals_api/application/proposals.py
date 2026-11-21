"""Use cases: draft, decide, list. The price book reads as the signed-in user; the store keeps
proposals. Who may do what comes from domain.rules, with the caller from the validated token."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from margin_copilot_shared.contracts import DraftRequest, Proposal
from margin_copilot_shared.security.tokens import Caller

from proposals_api.domain.rules import (
    CENT,
    REGIONS,
    Ambiguous,
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    RegionGroups,
    authority,
    change_pct,
    check_decision,
    may_access,
    price_after,
    required_approver,
)

MAX_LISTED = 100
MAX_MATCHES = 10


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    tier: str


class PriceBook(Protocol):
    async def find(self, term: str) -> list[Product]: ...

    async def current_price(self, sku: str, region: str) -> Decimal | None: ...


class ProposalStore(Protocol):
    async def add(self, proposal: Proposal) -> None: ...

    async def get(self, region: str, proposal_id: str) -> tuple[Proposal, str] | None: ...

    async def replace(self, proposal: Proposal, etag: str) -> None: ...

    async def list(self, regions: tuple[str, ...], status: str) -> list[Proposal]: ...


async def draft(
    request: DraftRequest,
    caller: Caller,
    groups: RegionGroups,
    prices: PriceBook,
    store: ProposalStore,
    now: datetime,
) -> Proposal:
    a = authority(caller.groups, caller.groups_overage, groups)
    region = request.region or a.region
    if region is None:
        raise Invalid("Say which region: North, Center or South.")
    if not may_access(a, region):
        raise Forbidden("region not allowed")
    product = _one_product(await prices.find(request.product), request.product)
    current = await prices.current_price(product.sku, region)
    if current is None:
        raise NotFound("no current price")
    if request.new_price is not None:
        new = request.new_price
    else:
        new = price_after(current, request.change_pct)
    pct = change_pct(current, new)
    approver, clause = required_approver(product.tier, pct)
    proposal = Proposal(
        id=uuid.uuid4().hex,
        region=region,
        sku=product.sku,
        product=product.name,
        tier=product.tier,
        current_price=current,
        new_price=new,
        change_pct=pct.quantize(CENT, rounding=ROUND_HALF_UP),
        approver=approver,
        clause=clause,
        status="pending",
        proposed_by=caller.oid,
        proposed_at=now,
    )
    await store.add(proposal)
    return proposal


def _one_product(found: list[Product], term: str) -> Product:
    exact = [p for p in found if p.name.casefold() == term.strip().casefold()]
    if len(exact) == 1:
        return exact[0]
    if len(found) == 1:
        return found[0]
    if not found:
        raise NotFound("no product matches")
    names = tuple(sorted(p.name[:80] for p in found))[:MAX_MATCHES]
    raise Ambiguous("several products match", matches=names)


async def decide(
    region: str,
    proposal_id: str,
    approve: bool,
    caller: Caller,
    groups: RegionGroups,
    store: ProposalStore,
    now: datetime,
) -> Proposal:
    a = authority(caller.groups, caller.groups_overage, groups)
    # Before the lookup: another region's manager learns nothing, not even whether it exists.
    if not may_access(a, region):
        raise Forbidden("region not allowed")
    found = await store.get(region, proposal_id)
    if found is None:
        raise NotFound("no such proposal")
    proposal, etag = found
    if proposal.status != "pending":
        raise Conflict("already decided")
    check_decision(a, proposal, approve)
    decided = proposal.model_copy(
        update={
            "status": "approved" if approve else "rejected",
            "decided_by": caller.oid,
            "decided_at": now,
        }
    )
    await store.replace(decided, etag)
    return decided


async def listing(
    status: str, caller: Caller, groups: RegionGroups, store: ProposalStore
) -> list[Proposal]:
    a = authority(caller.groups, caller.groups_overage, groups)
    regions = REGIONS if a.director else (a.region,)
    found = await store.list(regions, status)
    return sorted(found, key=lambda p: p.proposed_at, reverse=True)[:MAX_LISTED]
