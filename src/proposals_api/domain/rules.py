"""Who may draft and approve (D-012, PP-01 §2, PP-04 §3), from the token's groups and from facts
proposals-api read itself. Pure logic, no I/O."""

import uuid
from dataclasses import dataclass, fields
from decimal import ROUND_HALF_UP, Decimal

from margin_copilot_shared.contracts import Proposal

REGIONS = ("N", "C", "S")
CENT = Decimal("0.01")
# PP-04 §3.4: repriced weekly to follow market cost; §2.2 and §3.1–3.3 don't apply to them.
FRESH_PRODUCE = ("tomatoes", "potatoes", "onions", "oranges")
LIMITS = {Decimal(n) for n in (0, 3, 4, 8, 10)}  # where an approver or a cap changes (PP-04 §3)


class ProposalProblem(Exception):
    """A request proposals-api refuses; `status` and the message become the error response."""

    status = 400

    def __init__(self, message: str, clause: str | None = None, matches: tuple[str, ...] = ()):
        super().__init__(message)
        self.message, self.clause, self.matches = message, clause, matches


class Forbidden(ProposalProblem):
    status = 403


class NotFound(ProposalProblem):
    status = 404


class Ambiguous(ProposalProblem):
    status = 409


class Conflict(ProposalProblem):
    status = 409


class Invalid(ProposalProblem):
    status = 422


@dataclass(frozen=True)
class RegionGroups:
    north: str
    center: str
    south: str
    directors: str


def parse_region_groups(raw: str) -> RegionGroups:
    """`north:<id>,center:<id>,south:<id>,directors:<id>`. One ID shared by two keys would widen
    access (directors = north makes every North manager a director), so all four must differ."""
    keys = [field.name for field in fields(RegionGroups)]
    pairs = [item.split(":", 1) for item in raw.split(",") if item.strip()]
    if any(len(pair) != 2 for pair in pairs) or sorted(k.strip() for k, _ in pairs) != sorted(keys):
        raise ValueError(f"REGION_GROUPS needs exactly {', '.join(keys)}")
    ids = {key.strip(): str(uuid.UUID(value.strip())) for key, value in pairs}
    if len(set(ids.values())) != len(keys):
        raise ValueError("REGION_GROUPS needs four distinct group IDs")
    return RegionGroups(**ids)


@dataclass(frozen=True)
class Authority:
    director: bool
    region: str | None  # a manager's one region; None for the director, who acts in all


def authority(groups: frozenset[str], overage: bool, region_groups: RegionGroups) -> Authority:
    if overage:
        raise Forbidden("no pricing role in your token")
    canonical = set()
    for group in groups:
        try:
            canonical.add(str(uuid.UUID(group)))
        except ValueError:
            continue
    if region_groups.directors in canonical:
        return Authority(director=True, region=None)
    regions = [
        code
        for code, group in zip(
            REGIONS, (region_groups.north, region_groups.center, region_groups.south), strict=True
        )
        if group in canonical
    ]
    if len(regions) != 1:
        raise Forbidden("no pricing role in your token")
    return Authority(director=False, region=regions[0])


def may_access(a: Authority, region: str) -> bool:
    return a.director or a.region == region


def change_pct(current: Decimal, new: Decimal) -> Decimal:
    return (new - current) / current * 100  # exact: rules compare this, never a rounded figure


def price_after(current: Decimal, pct: Decimal) -> Decimal:
    return (current * (1 + pct / 100)).quantize(CENT, rounding=ROUND_HALF_UP)


def shown_pct(pct: Decimal) -> Decimal:
    """Two decimals for people. Rounding never lands on a limit the exact value isn't on, so the
    figure can't contradict the approver (+4.004% shows +4.01%, +3.996% shows +3.99%)."""
    shown = pct.quantize(CENT, rounding=ROUND_HALF_UP)
    if shown != pct and abs(shown) in LIMITS:
        shown += CENT if pct > shown else -CENT
    return shown


def is_fresh_produce(product_name: str) -> bool:
    words = product_name.split()
    return bool(words) and words[0].casefold() in FRESH_PRODUCE


def required_approver(tier: str, pct: Decimal, fresh_produce: bool = False) -> tuple[str, str]:
    """(approver, clause). Stricter than PP-04 where its checks need data we don't read yet: every
    cut needs the director (§3.5's margin floor), and so does every fresh-produce increase (§3.4's
    price index ≤ 105), without the tier caps §3.4 lifts."""
    if pct == 0:
        raise Invalid("That's no change from the current price.")
    if pct < 0:
        return "director", "PP-04 §3.5"
    if fresh_produce:
        return "director", "PP-04 §3.4"
    if tier == "A":
        if pct > 3:
            raise Invalid(_capped("PP-04 §3.1", "A", 3, pct), clause="PP-04 §3.1")
        return "director", "PP-04 §3.1"
    if tier == "B":
        if pct <= 4:
            return "manager", "PP-04 §3.2"
        if pct <= 10:
            return "director", "PP-04 §3.2"
        raise Invalid(_capped("PP-04 §3.2", "B", 10, pct), clause="PP-04 §3.2")
    if tier == "C":
        return ("manager" if pct <= 8 else "director"), "PP-04 §3.3"
    raise ValueError(f"unknown tier {tier!r}")


def _capped(clause: str, tier: str, cap: int, pct: Decimal) -> str:
    return f"{clause} caps Tier {tier} increases at {cap}%; this would be {shown_pct(pct):+.2f}%."


def check_decision(a: Authority, proposal: Proposal, approve: bool) -> None:
    if not may_access(a, proposal.region):
        raise Forbidden("region not allowed")
    if approve and proposal.approver == "director" and not a.director:
        raise Forbidden("needs the pricing director")
