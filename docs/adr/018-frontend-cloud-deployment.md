# ADR-018: Frontend Cloud Deployment — Separate SWA-Hosted SPA, Infra-Here / App-There

- Status: Proposed (Sprint 24, May 2026)
- Supersedes: none
- Related: [ADR-007](007-admin-ui-technology.md) (React 19 + Vite SPA in separate repo), [ADR-016](016-multi-cloud-deployment-strategy.md) (Azure-first IaC; per-provider IaC, portable substrate), [ADR-017](017-network-hardening.md) (VNet + private endpoints — does **not** affect public api ingress, which the SPA depends on)

## Context

Sprint 22 Phase C-1 provisioned an Azure Static Web App (`tpdev-ui`)
alongside the api/worker/migrations Container Apps but did **not** wire
up a deploy path for the SPA bundle. The artefact that lands in the
SWA today is the empty default placeholder. Sprint 22 Phase C-2's
`azure.yaml` declares only the three Python services; there is no
`web` service.

Three forces shape the choice of how to actually ship the SPA:

1. **Repo split (ADR-007).** The SPA lives in `9owlsboston/TagPulse-UI`,
   a sibling repo. Combining its CI into this repo's `azd up` would
   require either (a) a git submodule + Docker build context that
   pulls the UI tree into the api image, or (b) a cross-repo workflow
   dispatch from this repo into TagPulse-UI's GHA. Both add coupling
   that the original repo split was meant to avoid.
2. **Different runtime profile.** SPA = static assets best served from
   a CDN edge; api = stateful Container App with secrets, DB, MQTT.
   Co-hosting on ACA means burning vCPU-seconds serving HTML/JS that
   any CDN serves for free. Same-origin delivery (the only reason to
   co-host) is a non-goal — strict-CORS is already shipped (Sprint
   22 A2) and the SPA → API auth path is JWT in headers, not cookies.
3. **Sprint 22 multi-cloud-shaped deploy contract.** The repo has set
   the precedent that infra is per-provider (`deploy/azure/bicep/`,
   `deploy/aws/` skeleton, `deploy/gcp/` skeleton) and the portable
   spec is the Helm chart + the data-migration scripts. The frontend
   needs to fit this same shape.

Three viable hosting targets were considered for v1 (Azure):

| Target | Cost (dev) | TLS / domain | PR previews | CDN | Verdict |
|---|---|---|---|---|---|
| **Azure Static Web Apps (Free)** | $0 | built-in | built-in | built-in | ✅ **chosen** |
| Storage account `$web` + Front Door | ~$5/mo | manual cert + custom domain wiring | none | yes | rejected — more plumbing for no v1 win |
| Container Apps (nginx + dist/) | ~$5/mo (vCPU) | ingress TLS | manual | none | rejected — wastes compute on static files |

For the post-v1 production hosting decision (when SWA Free's 100GB/mo
bandwidth or 500K invocations/day cap matters), the choice converges
on **SWA Standard ($9/mo)** or **Storage + Front Door** — that pick is
deferred to its own ADR gated on real traffic, not v1 launch.

## Decision

### 1. SWA, separate deploy, infra-here / app-there

- **Infra (Bicep) lives in this repo** under `deploy/azure/bicep/modules/static-web-app.bicep` (already shipped in Sprint 22 C-1). `azd up` from this repo provisions the SWA resource, outputs `staticWebAppName` + `staticWebAppHostname` at subscription scope, and seeds `VITE_API_BASE_URL=https://${api-fqdn}` into the SWA's `appsettings`.
- **Deployment (the SPA bundle) ships from TagPulse-UI** via that repo's own GHA workflow. The infra here is the empty container; the app there is what fills it.
- **Coordination contract** = three values exchanged:
  1. From this repo → UI repo: `AZURE_STATIC_WEB_APPS_API_TOKEN` (rotatable; surfaced via `az staticwebapp secrets list` and stored as a per-environment secret in `9owlsboston/TagPulse-UI`'s GitHub Environment).
  2. From this repo → UI repo: `VITE_API_BASE_URL` (per env; sourced from `https://$(azd env get-value apiFqdn)` — the Bicep output is the authoritative api URL since `SERVICE_API_URI` is not persisted by azd for the `containerapp` host).
  3. From UI repo → this repo: the SWA hostname is added to `CORS_ALLOW_ORIGINS` in the api's `.env.<env>` so cross-origin requests are accepted. Already-shipped strict-mode validator (Sprint 22 A2) rejects `*` in non-dev, so this is mandatory.

### 2. Multi-cloud parity (skeletons only, mirroring Sprint 22 F1/F2)

Frontend hosting is per-provider just like backend hosting:

| Provider | Hosting | CDN | TLS | Skeleton path |
|---|---|---|---|---|
| Azure | Static Web Apps (Free → Standard) | built-in | built-in | `deploy/azure/bicep/modules/static-web-app.bicep` (real, shipped Sprint 22) |
| AWS | S3 (`$web`-equivalent: `index.html`/`error.html`) + CloudFront + ACM | CloudFront | ACM | `deploy/aws/ui/` (skeleton, Sprint 24) |
| GCP | Cloud Storage bucket + Cloud CDN + managed cert | Cloud CDN | managed cert | `deploy/gcp/ui/` (skeleton, Sprint 24) |

The Vite `dist/` output is provider-agnostic. The provider-specific
adapter is "how the bundle gets uploaded and how the URL is fronted"
— a 30-line shell script per provider that the cross-cloud DR runbook
will reference once Sprint 22 D-3 lands the backend half.

### 3. Env-file + script parity with backend

The UI repo will mirror the per-env workflow the backend established
in Sprint 22:

- `scripts/azd-bootstrap.sh <env>` → `scripts/ui-bootstrap.sh <env>` in the UI repo. Generates `.env.<env>` with `AZURE_STATIC_WEB_APPS_NAME`, `AZURE_STATIC_WEB_APPS_API_TOKEN`, `VITE_API_BASE_URL`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`. Mode 0600. Never committed.
- `scripts/ui-env-load.sh <env>` → loads the env file into the current shell (mirrors `scripts/azd-env-load.sh`).
- `scripts/ui-cicd-setup.sh <env>` → idempotently creates the GitHub Environment, sets the four variables, and uploads the API token as a secret (mirrors `scripts/azd-cicd-setup.sh`).
- `scripts/ui-cicd-verify.sh <env>` → confirms the Environment exists with the expected variables/secrets (mirrors `scripts/azd-cicd-verify.sh`).
- `scripts/ui-preflight.sh` → checks node/npm/gh/az versions before first deploy (mirrors `scripts/azd-preflight.sh`).
- One-line bootstrap for an existing backend env: `eval "$(cd ../TagPulse && azd env get-values | grep -E 'apiFqdn|AZURE_TENANT_ID|AZURE_SUBSCRIPTION_ID')"` so the UI repo doesn't have to re-discover them.

### 4. Auth model (no change from today)

- The SPA continues to authenticate users via `POST /auth/login` against the api (Sprint 13).
- JWT lives in browser storage; sent as `Authorization: Bearer <token>` on every request.
- **No SWA built-in auth (`/.auth/login/*`) is wired.** The api owns identity — adding SWA auth would create a second identity provider for no operator benefit.
- `staticwebapp.config.json` only configures SPA fallback routing (`/index.html`) and global response headers (HSTS, CSP, X-Frame-Options).

### 5. Production hardening (deferred)

Out of scope for v1; gated on real traffic / a real customer:

- SWA Standard ($9/mo) tier — only matters past Free-tier caps.
- Custom domain (`app.tagpulse.io`) — DNS not provisioned yet.
- Front Door + WAF in front of SWA — promoted from Sprint 23 deferred list when api ingress also moves behind FD.
- SWA-managed staging slots for branch-preview deploys — covered by GHA's PR-comment URL out of the box; revisit if reviewers ask for stable preview URLs.
- CSP tightening — a permissive starting CSP ships in v1; lock it down once the asset list stabilises.

## Consequences

- **Two repos must release in lockstep for breaking API contract changes.** Mitigation: the api ships an `openapi.json` that the UI repo consumes; both repos bump on the same sprint when the contract changes. This mirrors how the Sprint 21 UI items lagged the Sprint 19/20 backend items by one sprint — the workflow already works.
- **CORS becomes mandatory load-bearing config.** A typo in `CORS_ALLOW_ORIGINS` is a complete production outage for the SPA. Mitigation: the SWA hostname is captured into `azd env set CORS_ALLOW_ORIGINS …` in the Sprint 24 first-deploy runbook, and a `/health/ready` enrichment surfaces the configured origins so operators can verify without shelling in.
- **Per-env API tokens rotate ad-hoc.** SWA deployment tokens have no built-in rotation policy. Mitigation: the token is treated as a long-lived secret per env; `scripts/ui-cicd-setup.sh --rotate` rotates and re-uploads in one step.
- **AWS / GCP UI skeletons remain TODO-only this sprint** — same posture as Sprint 22 F1/F2 backend skeletons. No resources provision until a customer pulls them.

## Alternatives Considered

### A. Co-host the SPA inside the api Container App (mount `dist/` on `/`)

- **Pro:** same-origin, no CORS config, one URL, one deploy.
- **Con:** UI release blocked behind api CI + migrations. Loses CDN edge caching unless we add Front Door anyway. Bloats the api image with `dist/` on every UI change. Forces TagPulse-UI to publish a Docker image (currently it publishes `dist/`) — meaningful tooling change for no operator benefit.
- **Verdict:** rejected. Same-origin is the only real win and it's not a v1 problem (JWT-in-header SPA, no cookies).

### B. Combine the two repos

- **Pro:** simpler release coordination.
- **Con:** undoes ADR-007. Different toolchains (Python uv / pip / pytest vs Node / pnpm / vitest), different release cadences (UI wants daily visual fixes; api wants weekly behind migrations), different security boundaries.
- **Verdict:** rejected. The repos are split for good reasons; the deploy path should respect the split.

### C. Storage + Front Door from day one

- **Pro:** Front Door is on the Sprint 23 deferred list anyway; doing it now means one less migration later.
- **Con:** ~$35/mo for FD Standard + manual cert wiring + custom-domain DNS we don't have. Real value of FD is api-side WAF, not SPA delivery.
- **Verdict:** deferred. SWA Free covers v1; promote to Storage + FD when SWA caps bite or when api also moves behind FD.

## Migration

- Greenfield (no SWA hostname configured): nothing to migrate. Sprint 24 runbook walks through first-deploy.
- Sprint 22-deployed envs (SWA exists with the placeholder bundle): the first GHA workflow run from TagPulse-UI's `main` overwrites the placeholder with the real SPA bundle. Backend `.env.<env>` must be edited to add the SWA hostname to `CORS_ALLOW_ORIGINS` and `azd provision` re-run **before** the SPA can talk to the api — order is enforced by the Sprint 24 runbook.
