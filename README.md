# Margin Copilot: proposals-api

Pending price-change drafts and the approval rules (D-012). No model: who may approve is decided by code, from the validated token and from facts this service reads itself.

Part of **Margin Copilot**, a secure multi-agent pricing assistant on Azure, built as a personal learning project.

**Start here:** [margin-copilot/docs](https://github.com/margin-copilot/docs) explains how the whole system works, the security model and the decisions behind it.

## Routes

| Route | Who may call | What |
|---|---|---|
| `POST /drafts` | the action agent's app only (`DRAFT_CALLERS`) | `DraftRequest` → 201 `Proposal`, status `pending` |
| `GET /proposals?status=pending\|approved\|rejected` | apps people use (`REVIEW_CALLERS`) | `{"proposals": [...]}`: a manager's region, or every region for the director; newest first, at most 100 |
| `POST /proposals/{region}/{id}/approve`, `…/reject` | apps people use | 200 `Proposal` |

Each route group has its own instance of the shared token check, so an agent's token can never reach a decision.

**Where each fact comes from:**
- **Role and region:** the token's `groups` claim, mapped by `REGION_GROUPS`. A group overage, no region group, or two region groups without the directors group are refused.
- **Tier and current price:** Azure SQL, read as the user (on-behalf-of, row-level security). The current price is the shelf price on the last day with sales. A draft can't carry either.
- **The percent:** computed exactly. The approver needed is decided at draft time and stored.

**Rules:**
- **Managers** approve Tier B up to +4% and Tier C up to +8% in their region.
- **The director** approves everything else: every Tier A change, every cut, and every fresh-produce increase (PP-04 §3.4: tomatoes, potatoes, onions and oranges, without the tier caps).
- **Percents** are shown with two decimals, and rounding never lands on a limit (0, 3, 4, 8, 10%) that the exact value isn't on. Prices are stored in cents.
- **Hard caps:** Tier A over +3% (PP-04 §3.1) and Tier B over +10% (PP-04 §3.2) can't be drafted.
- **Rejecting** needs access to the region only.

**Errors** come as `{"error", "clause"?, "matches"?}`:

| Status | Error |
|---|---|
| 403 | `region not allowed`, `needs the pricing director`, `no pricing role in your token` |
| 404 | `no product matches`, `no current price`, `no such proposal` |
| 409 | `several products match` (with `matches`), `already decided`, `changed meanwhile` |
| 422 | the hard caps (with `clause`), no change, `Say which region: …` for the director |

## Settings

Names in [.env.example](.env.example); no defaults in azure mode. `ALLOWED_ORIGINS` lists the exact browser origins (the web app) allowed to call the routes, comma-separated: CORS only, the token checks still decide. Proposals live in Table Storage (PartitionKey = region, RowKey = ID), written keylessly with the service's managed identity.

## Run and test

```bash
uv sync
uv run pytest -q
ADAPTERS=fake uv run uvicorn --factory proposals_api.main:create_app
```

## Deploy

A push to `main` runs the tests, publishes `ghcr.io/margin-copilot/proposals-api:sha-<commit>` after a check that the SQL driver loads in the image, then deploys the image to the container app through GitHub OIDC (no stored Azure secret). The deploy waits up to 5 minutes for the new revision to be ready and serve all traffic, or fails while the old revision keeps serving, then checks that `GET /proposals` without a token is 401. Runs on `main` go one at a time: a push waits until the running one has finished, and a newer waiting run replaces an older waiting one, so the latest commit ends up live. Other branches and pull requests get no Azure token. Repository secrets: `AZURE_CLIENT_ID` (this repo's deploy identity), `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `CONTAINER_APP_NAME`. The infra repo creates the app and the identity; how it all fits: [docs](https://github.com/margin-copilot/docs).

## License

MIT. See [LICENSE](LICENSE).
