from datetime import UTC, datetime
from decimal import Decimal

from margin_copilot_shared.security.tokens import Caller

from proposals_api.application.proposals import Product
from proposals_api.domain.rules import RegionGroups

G_NORTH = "11111111-0000-0000-0000-000000000001"
G_CENTER = "11111111-0000-0000-0000-000000000002"
G_SOUTH = "11111111-0000-0000-0000-000000000003"
G_DIRECTORS = "11111111-0000-0000-0000-000000000004"
GROUPS = RegionGroups(north=G_NORTH, center=G_CENTER, south=G_SOUTH, directors=G_DIRECTORS)
RAW_GROUPS = f"north:{G_NORTH},center:{G_CENTER},south:{G_SOUTH},directors:{G_DIRECTORS}"
NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)

UHT = Product("DAI-002", "UHT milk 1 L", "B")
FRESH = Product("DAI-001", "Fresh milk 1 L", "A")
OLIVE = Product("GRO-008", "Olive oil 1 L", "B")
PRODUCTS = [UHT, FRESH, OLIVE]
PRICES = {
    ("DAI-002", "N"): Decimal("7.00"),
    ("DAI-001", "N"): Decimal("6.50"),
    ("GRO-008", "C"): Decimal("80.25"),
}


def caller(*groups: str, oid: str = "oid-user", overage: bool = False) -> Caller:
    return Caller(oid, "tid", "action-agent", frozenset(), frozenset(groups), overage)
