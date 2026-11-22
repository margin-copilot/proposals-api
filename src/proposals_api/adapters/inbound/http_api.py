"""HTTP routes. Two route groups, each behind its own instance of the shared token check: drafts
accept only the action agent's app; listing and decisions accept only apps people use (the CLI
now, the web app in milestone 8). No agent can approve or reject."""

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from functools import partial

from margin_copilot_shared.contracts import DraftRequest, ProposalError
from margin_copilot_shared.security.context import current_request
from margin_copilot_shared.security.middleware import AuthMiddleware
from margin_copilot_shared.security.tokens import TokenValidator
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from proposals_api.application.proposals import PriceBook, ProposalStore, decide, draft, listing
from proposals_api.domain.rules import REGIONS, NotFound, ProposalProblem, RegionGroups

MAX_BODY = 4_096
STATUSES = ("pending", "approved", "rejected")
PROPOSAL_ID = re.compile(r"[0-9a-f]{32}")


def _problem(problem: ProposalProblem) -> JSONResponse:
    body = ProposalError(
        error=problem.message, clause=problem.clause, matches=list(problem.matches)
    )
    return JSONResponse(body.model_dump(mode="json", exclude_none=True), status_code=problem.status)


def create_http_app(
    prices: PriceBook,
    store: ProposalStore,
    groups: RegionGroups,
    draft_validator: TokenValidator,
    review_validator: TokenValidator,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    allowed_origins: Sequence[str] = (),
) -> Starlette:
    async def create_draft(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse({"error": "request too large"}, status_code=413)
        try:
            draft_request = DraftRequest.model_validate_json(body)
        except ValidationError:
            return JSONResponse({"error": "invalid draft"}, status_code=400)
        caller = current_request().caller
        proposal = await draft(draft_request, caller, groups, prices, store, clock())
        return JSONResponse(proposal.model_dump(mode="json"), status_code=201)

    async def list_proposals(request: Request) -> JSONResponse:
        status = request.query_params.get("status", "pending")
        if status not in STATUSES:
            return JSONResponse({"error": "unknown status"}, status_code=400)
        found = await listing(status, current_request().caller, groups, store)
        return JSONResponse({"proposals": [p.model_dump(mode="json") for p in found]})

    async def decide_proposal(request: Request, approve: bool) -> JSONResponse:
        region, proposal_id = request.path_params["region"], request.path_params["proposal_id"]
        if region not in REGIONS or not PROPOSAL_ID.fullmatch(proposal_id):
            raise NotFound("no such proposal")
        caller = current_request().caller
        decided = await decide(region, proposal_id, approve, caller, groups, store, clock())
        return JSONResponse(decided.model_dump(mode="json"))

    async def on_problem(request: Request, problem: Exception) -> JSONResponse:
        assert isinstance(problem, ProposalProblem)
        return _problem(problem)

    drafts = [Middleware(AuthMiddleware, validator=draft_validator)]
    people = [Middleware(AuthMiddleware, validator=review_validator)]
    decision = "/proposals/{region}/{proposal_id}"
    return Starlette(
        routes=[
            Route("/drafts", create_draft, methods=["POST"], middleware=drafts),
            Route("/proposals", list_proposals, methods=["GET"], middleware=people),
            Route(
                f"{decision}/approve",
                partial(decide_proposal, approve=True),
                methods=["POST"],
                middleware=people,
            ),
            Route(
                f"{decision}/reject",
                partial(decide_proposal, approve=False),
                methods=["POST"],
                middleware=people,
            ),
        ],
        exception_handlers={ProposalProblem: on_problem},
        # App-wide and outside the per-route token checks, so the web app's preflights are
        # answered first; CORS is a convenience, the token checks still decide (D-042).
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=list(allowed_origins),
                allow_methods=["GET", "POST"],
                allow_headers=["Authorization", "Content-Type"],
                max_age=600,
            )
        ]
        if allowed_origins
        else [],
    )
