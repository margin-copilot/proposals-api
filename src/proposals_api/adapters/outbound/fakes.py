"""In-memory adapters for ADAPTERS=fake and tests."""

from decimal import Decimal

from margin_copilot_shared.contracts import Proposal

from proposals_api.application.proposals import Product
from proposals_api.domain.rules import Conflict

SAMPLE_PRODUCTS = [
    Product("DAI-001", "Fresh milk 1 L", "A"),
    Product("DAI-002", "UHT milk 1 L", "B"),
    Product("DAI-007", "Cheese spread, 8 portions", "C"),
    Product("GRO-008", "Olive oil 1 L", "B"),
]
SAMPLE_PRICES = {
    (sku, region): price
    for sku, price in [
        ("DAI-001", Decimal("6.50")),
        ("DAI-002", Decimal("7.00")),
        ("DAI-007", Decimal("14.00")),
        ("GRO-008", Decimal("80.25")),
    ]
    for region in ("N", "C", "S")
}


class FakePriceBook:
    def __init__(
        self,
        products: list[Product] = SAMPLE_PRODUCTS,
        prices: dict[tuple[str, str], Decimal] = SAMPLE_PRICES,  # noqa: B006 - never mutated
    ) -> None:
        self._products, self._prices = products, prices

    async def find(self, term: str) -> list[Product]:
        return [p for p in self._products if term.strip().casefold() in p.name.casefold()]

    async def current_price(self, sku: str, region: str) -> Decimal | None:
        return self._prices.get((sku, region))


class MemoryProposalStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], tuple[Proposal, int]] = {}

    async def add(self, proposal: Proposal) -> None:
        self._items[(proposal.region, proposal.id)] = (proposal, 1)

    async def get(self, region: str, proposal_id: str) -> tuple[Proposal, str] | None:
        found = self._items.get((region, proposal_id))
        return (found[0], str(found[1])) if found else None

    async def replace(self, proposal: Proposal, etag: str) -> None:
        current = self._items.get((proposal.region, proposal.id))
        if current is None or str(current[1]) != etag:
            raise Conflict("changed meanwhile")
        self._items[(proposal.region, proposal.id)] = (proposal, current[1] + 1)

    async def list(self, regions: tuple[str, ...], status: str) -> list[Proposal]:
        return [p for p, _ in self._items.values() if p.region in regions and p.status == status]
