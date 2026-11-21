from decimal import Decimal

import pytest
from margin_copilot_shared.contracts import DraftRequest

from proposals_api.adapters.outbound.fakes import FakePriceBook, MemoryProposalStore
from proposals_api.application.proposals import Product, decide, draft, listing
from proposals_api.domain.rules import Ambiguous, Conflict, Forbidden, Invalid, NotFound
from tests.helpers import (
    G_CENTER,
    G_DIRECTORS,
    G_NORTH,
    G_SOUTH,
    GROUPS,
    NOW,
    PRICES,
    PRODUCTS,
    caller,
)

NORTH = caller(G_NORTH, oid="oid-north")


def book(products=PRODUCTS, prices=PRICES):
    return FakePriceBook(products, prices)


async def make(request, who=NORTH, store=None, prices=None):
    store = store or MemoryProposalStore()
    return await draft(request, who, GROUPS, prices or book(), store, NOW), store


async def test_tier_and_price_come_from_the_price_book():
    proposal, store = await make(DraftRequest(product="UHT milk 1 L", new_price=Decimal("7.25")))
    assert (proposal.region, proposal.sku, proposal.tier) == ("N", "DAI-002", "B")
    assert (proposal.current_price, proposal.new_price) == (Decimal("7.00"), Decimal("7.25"))
    assert proposal.change_pct == Decimal("3.57")
    assert (proposal.approver, proposal.clause) == ("manager", "PP-04 §3.2")
    assert (proposal.status, proposal.proposed_by) == ("pending", "oid-north")
    assert await store.get("N", proposal.id) is not None


async def test_a_percent_becomes_a_rounded_price():
    center = caller(G_CENTER)
    proposal, _ = await make(
        DraftRequest(product="olive oil 1 l", change_pct=Decimal("7")), who=center
    )
    assert (proposal.new_price, proposal.change_pct) == (Decimal("85.87"), Decimal("7.00"))
    assert proposal.approver == "director"


async def test_a_manager_cannot_draft_for_another_region():
    with pytest.raises(Forbidden, match="region not allowed"):
        await make(DraftRequest(product="UHT milk 1 L", region="S", change_pct=Decimal("2")))


async def test_a_director_must_name_the_region():
    with pytest.raises(Invalid, match="Say which region"):
        await make(DraftRequest(product="UHT milk", new_price=Decimal("7.25")), caller(G_DIRECTORS))


async def test_an_exact_name_beats_partial_matches():
    products = [*PRODUCTS, Product("DAI-099", "UHT milk 1 L lactose-free", "B")]
    proposal, _ = await make(
        DraftRequest(product="UHT milk 1 L", new_price=Decimal("7.25")), prices=book(products)
    )
    assert proposal.sku == "DAI-002"


async def test_several_matches_are_ambiguous():
    with pytest.raises(Ambiguous) as several:
        await make(DraftRequest(product="milk", new_price=Decimal("7.25")))
    assert several.value.matches == ("Fresh milk 1 L", "UHT milk 1 L")


async def test_no_match_is_not_found():
    with pytest.raises(NotFound, match="no product matches"):
        await make(DraftRequest(product="caviar", new_price=Decimal("7.25")))


async def test_no_visible_price_is_not_found():
    with pytest.raises(NotFound, match="no current price"):
        await make(DraftRequest(product="olive oil", new_price=Decimal("80.00")))


async def test_a_decided_proposal_cannot_be_decided_again():
    proposal, store = await make(DraftRequest(product="UHT milk 1 L", new_price=Decimal("7.25")))
    approved = await decide("N", proposal.id, True, NORTH, GROUPS, store, NOW)
    assert (approved.status, approved.decided_by) == ("approved", "oid-north")
    with pytest.raises(Conflict, match="already decided"):
        await decide("N", proposal.id, False, NORTH, GROUPS, store, NOW)


async def test_a_concurrent_change_is_a_conflict():
    proposal, store = await make(DraftRequest(product="UHT milk 1 L", new_price=Decimal("7.25")))
    _, etag = await store.get("N", proposal.id)
    await store.replace(proposal.model_copy(update={"status": "rejected"}), etag)
    with pytest.raises(Conflict, match="changed meanwhile"):
        await store.replace(proposal.model_copy(update={"status": "approved"}), etag)


async def test_another_region_learns_nothing_about_a_proposal():
    proposal, store = await make(DraftRequest(product="UHT milk 1 L", new_price=Decimal("7.25")))
    for proposal_id in (proposal.id, "f" * 32):
        with pytest.raises(Forbidden, match="region not allowed"):
            await decide("N", proposal_id, True, caller(G_SOUTH), GROUPS, store, NOW)


async def test_managers_list_only_their_region():
    store = MemoryProposalStore()
    await make(DraftRequest(product="UHT milk 1 L", new_price=Decimal("7.25")), store=store)
    await make(DraftRequest(product="olive oil", change_pct=Decimal("3")), caller(G_CENTER), store)
    assert [p.region for p in await listing("pending", NORTH, GROUPS, store)] == ["N"]
    assert await listing("pending", caller(G_SOUTH), GROUPS, store) == []
    everything = await listing("pending", caller(G_DIRECTORS), GROUPS, store)
    assert sorted(p.region for p in everything) == ["C", "N"]
