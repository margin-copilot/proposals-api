import httpx
import pytest
from margin_copilot_shared.testing.issuer import LocalIssuer

from proposals_api.adapters.inbound.http_api import create_http_app
from proposals_api.adapters.outbound.fakes import FakePriceBook, MemoryProposalStore
from tests.helpers import G_CENTER, G_DIRECTORS, G_NORTH, GROUPS, PRICES, PRODUCTS

AUDIENCE = "proposals-api"


@pytest.fixture
def issuer():
    return LocalIssuer()


@pytest.fixture
def client(issuer):
    app = create_http_app(
        FakePriceBook(PRODUCTS, PRICES),
        MemoryProposalStore(),
        GROUPS,
        issuer.validator(audience=AUDIENCE, allowed_callers=frozenset({"action-agent"})),
        issuer.validator(audience=AUDIENCE, allowed_callers=frozenset({"cli"})),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p")


def bearer(issuer, azp, *groups, oid="oid-user"):
    token = issuer.issue(audience=AUDIENCE, azp=azp, oid=oid, groups=list(groups))
    return {"Authorization": f"Bearer {token}"}


UHT = {"product": "UHT milk 1 L", "region": "N", "new_price": "7.25"}


async def make_draft(client, issuer, body=UHT, *groups):
    return await client.post("/drafts", json=body, headers=bearer(issuer, "action-agent", *groups))


async def test_a_draft_is_201_and_pending(client, issuer):
    response = await make_draft(client, issuer, UHT, G_NORTH)
    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["approver"], body["new_price"]) == ("pending", "manager", "7.25")


async def test_an_agent_token_cannot_decide(client, issuer):
    proposal = (await make_draft(client, issuer, UHT, G_NORTH)).json()
    agent = bearer(issuer, "action-agent", G_DIRECTORS)
    for method, path in [
        ("POST", f"/proposals/N/{proposal['id']}/approve"),
        ("POST", f"/proposals/N/{proposal['id']}/reject"),
        ("GET", "/proposals"),
    ]:
        response = await client.request(method, path, headers=agent)
        assert (response.status_code, response.json()) == (
            403,
            {"error": "calling app not allowed"},
        )


async def test_a_review_token_cannot_draft(client, issuer):
    response = await client.post("/drafts", json=UHT, headers=bearer(issuer, "cli", G_DIRECTORS))
    assert (response.status_code, response.json()) == (403, {"error": "calling app not allowed"})


async def test_people_list_and_decide(client, issuer):
    proposal = (await make_draft(client, issuer, UHT, G_NORTH)).json()
    north = bearer(issuer, "cli", G_NORTH, oid="oid-north")
    listed = (await client.get("/proposals", headers=north)).json()["proposals"]
    assert [p["id"] for p in listed] == [proposal["id"]]
    approved = await client.post(f"/proposals/N/{proposal['id']}/approve", headers=north)
    assert (approved.status_code, approved.json()["status"]) == (200, "approved")
    again = await client.post(f"/proposals/N/{proposal['id']}/reject", headers=north)
    assert (again.status_code, again.json()["error"]) == (409, "already decided")
    assert (await client.get("/proposals?status=approved", headers=north)).json()["proposals"]


async def test_problems_carry_their_status_and_clause(client, issuer):
    fresh = {"product": "Fresh milk 1 L", "region": "N", "change_pct": "5"}
    capped = await make_draft(client, issuer, fresh, G_DIRECTORS)
    assert capped.status_code == 422
    assert capped.json()["clause"] == "PP-04 §3.1"
    several = await make_draft(client, issuer, {**UHT, "product": "milk"}, G_NORTH)
    assert (several.status_code, several.json()["matches"]) == (
        409,
        ["Fresh milk 1 L", "UHT milk 1 L"],
    )
    elsewhere = await make_draft(client, issuer, {**UHT, "region": "C"}, G_NORTH)
    assert (elsewhere.status_code, elsewhere.json()["error"]) == (403, "region not allowed")


async def test_director_level_drafts_need_the_director(client, issuer):
    olive = {"product": "Olive oil 1 L", "region": "C", "change_pct": "7"}
    proposal = (await make_draft(client, issuer, olive, G_CENTER)).json()
    center = bearer(issuer, "cli", G_CENTER)
    denied = await client.post(f"/proposals/C/{proposal['id']}/approve", headers=center)
    assert (denied.status_code, denied.json()["error"]) == (403, "needs the pricing director")
    director = bearer(issuer, "cli", G_DIRECTORS)
    assert (
        await client.post(f"/proposals/C/{proposal['id']}/approve", headers=director)
    ).is_success


@pytest.mark.parametrize(
    "path", ["/proposals/X/" + "a" * 32 + "/approve", "/proposals/N/xyz/approve"]
)
async def test_unknown_region_or_id_is_404(client, issuer, path):
    response = await client.post(path, headers=bearer(issuer, "cli", G_DIRECTORS))
    assert (response.status_code, response.json()["error"]) == (404, "no such proposal")


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b"not json", 400),
        (b'{"product": "UHT milk 1 L", "tier": "C", "new_price": "1"}', 400),
        (b"{" + b" " * 5000 + b"}", 413),
    ],
)
async def test_bad_drafts_are_refused_without_details(client, issuer, body, status):
    response = await client.post(
        "/drafts", content=body, headers=bearer(issuer, "action-agent", G_NORTH)
    )
    assert response.status_code == status
    assert "tier" not in response.text


async def test_an_unknown_status_is_400(client, issuer):
    response = await client.get("/proposals?status=all", headers=bearer(issuer, "cli", G_NORTH))
    assert response.status_code == 400
