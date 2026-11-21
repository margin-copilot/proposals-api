from datetime import UTC, datetime
from decimal import Decimal

import pytest
from margin_copilot_shared.contracts import Proposal

from proposals_api.domain.rules import (
    Forbidden,
    Invalid,
    authority,
    change_pct,
    check_decision,
    parse_region_groups,
    price_after,
    required_approver,
)
from tests.helpers import G_CENTER, G_DIRECTORS, G_NORTH, G_SOUTH, GROUPS, RAW_GROUPS

D = Decimal


@pytest.mark.parametrize(
    ("tier", "current", "new", "expected"),
    [
        ("B", "7.00", "7.28", ("manager", "PP-04 §3.2")),
        ("B", "7.00", "7.29", ("director", "PP-04 §3.2")),
        ("B", "70.00", "77.00", ("director", "PP-04 §3.2")),
        ("B", "70.00", "77.01", "PP-04 §3.2"),
        ("C", "10.00", "10.80", ("manager", "PP-04 §3.3")),
        ("C", "10.00", "10.81", ("director", "PP-04 §3.3")),
        ("A", "10.00", "10.30", ("director", "PP-04 §3.1")),
        ("A", "10.00", "10.31", "PP-04 §3.1"),
        ("B", "7.00", "6.90", ("director", "PP-04 §3.5")),
        ("C", "10.00", "9.00", ("director", "PP-04 §3.5")),
    ],
)
def test_approval_boundaries(tier, current, new, expected):
    pct = change_pct(D(current), D(new))
    if isinstance(expected, tuple):
        assert required_approver(tier, pct) == expected
    else:
        with pytest.raises(Invalid) as refused:
            required_approver(tier, pct)
        assert refused.value.clause == expected
        assert refused.value.status == 422


def test_no_change_is_refused():
    with pytest.raises(Invalid, match="no change"):
        required_approver("B", change_pct(D("7.00"), D("7.00")))


def test_a_cap_message_shows_a_value_just_over_the_cap():
    with pytest.raises(Invalid) as refused:
        required_approver("B", change_pct(D("70.00"), D("77.01")))
    assert "+10.01%" in refused.value.message


def test_percent_is_exact_and_prices_round_half_up():
    assert change_pct(D("7.00"), D("7.25")) > D("3.5714")
    assert price_after(D("80.25"), D("7")) == D("85.87")
    assert price_after(D("10.00"), D("0.05")) == D("10.01")


@pytest.mark.parametrize(
    "raw",
    [
        RAW_GROUPS.replace(G_DIRECTORS, G_NORTH),
        RAW_GROUPS.rsplit(",", 1)[0],
        RAW_GROUPS + ",east:11111111-0000-0000-0000-000000000005",
        RAW_GROUPS.replace(G_SOUTH, "not-a-guid"),
        RAW_GROUPS.replace("north:", "north"),
    ],
)
def test_region_groups_must_be_four_distinct_ids(raw):
    with pytest.raises(ValueError):
        parse_region_groups(raw)


def test_region_groups_parse():
    assert parse_region_groups(RAW_GROUPS) == GROUPS


def test_overage_is_forbidden():
    with pytest.raises(Forbidden):
        authority(frozenset(), True, GROUPS)


def test_no_region_group_is_forbidden():
    with pytest.raises(Forbidden):
        authority(frozenset({"99999999-0000-0000-0000-000000000000"}), False, GROUPS)


def test_two_region_groups_without_director_is_forbidden():
    with pytest.raises(Forbidden):
        authority(frozenset({G_NORTH, G_SOUTH}), False, GROUPS)


def test_a_manager_has_one_region_and_a_director_every_region():
    north = authority(frozenset({G_NORTH, "99999999-0000-0000-0000-000000000000"}), False, GROUPS)
    assert (north.director, north.region) == (False, "N")
    director = authority(frozenset({G_DIRECTORS, G_CENTER}), False, GROUPS)
    assert (director.director, director.region) == (True, None)


def _proposal(region="N", approver="manager"):
    return Proposal(
        id="a" * 32,
        region=region,
        sku="GRO-008",
        product="Olive oil 1 L",
        tier="B",
        current_price=D("80.25"),
        new_price=D("85.87"),
        change_pct=D("7.00"),
        approver=approver,
        clause="PP-04 §3.2",
        status="pending",
        proposed_by="oid-center",
        proposed_at=datetime(2026, 11, 21, tzinfo=UTC),
    )


def test_a_director_reaches_every_region():
    director = authority(frozenset({G_DIRECTORS}), False, GROUPS)
    for region in ("N", "C", "S"):
        check_decision(director, _proposal(region, "director"), approve=True)


def test_a_manager_rejects_but_cannot_approve_a_director_level_proposal():
    center = authority(frozenset({G_CENTER}), False, GROUPS)
    check_decision(center, _proposal("C", "director"), approve=False)
    with pytest.raises(Forbidden, match="needs the pricing director"):
        check_decision(center, _proposal("C", "director"), approve=True)


def test_a_manager_cannot_decide_for_another_region():
    south = authority(frozenset({G_SOUTH}), False, GROUPS)
    with pytest.raises(Forbidden, match="region not allowed"):
        check_decision(south, _proposal("N", "manager"), approve=False)


def test_a_shown_percent_never_lands_on_a_limit_by_rounding():
    from proposals_api.domain.rules import shown_pct

    assert shown_pct(change_pct(D("250.00"), D("260.01"))) == D("4.01")  # exact +4.004%
    assert shown_pct(change_pct(D("250.00"), D("259.99"))) == D("3.99")  # exact +3.996%
    assert shown_pct(D("-0.004")) == D("-0.01")
    assert shown_pct(change_pct(D("7.00"), D("7.25"))) == D("3.57")
    assert shown_pct(change_pct(D("7.00"), D("7.28"))) == D("4.00")  # exactly 4%


def test_a_cap_message_never_rounds_onto_the_cap():
    with pytest.raises(Invalid) as refused:
        required_approver("B", change_pct(D("1000.00"), D("1100.04")))  # exact +10.004%
    assert "+10.01%" in refused.value.message


def test_fresh_produce_needs_the_director_without_the_tier_caps():
    tomatoes_5 = change_pct(D("7.00"), D("7.35"))
    assert required_approver("A", tomatoes_5, fresh_produce=True) == ("director", "PP-04 §3.4")
    oranges_3 = change_pct(D("6.00"), D("6.18"))
    assert required_approver("B", oranges_3, fresh_produce=True) == ("director", "PP-04 §3.4")
    cut = change_pct(D("7.00"), D("6.50"))
    assert required_approver("A", cut, fresh_produce=True) == ("director", "PP-04 §3.5")


def test_fresh_produce_is_the_four_products_pp04_names():
    from proposals_api.domain.rules import is_fresh_produce

    for name in ("Tomatoes 1 kg", "Potatoes 1 kg", "Onions 1 kg", "Oranges 1 kg"):
        assert is_fresh_produce(name)
    for name in ("Orange juice 1 L", "Medjool dates 500 g", "Tomato paste 400 g"):
        assert not is_fresh_produce(name)
