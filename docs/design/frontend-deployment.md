# Frontend Cloud Deployment

> Sprint: 24
> ADR: [ADR-018](../adr/018-frontend-cloud-deployment.md)
> Repos: this repo (infra + Bicep + coordination contract); [9owlsboston/TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) (SPA bundle + GHA deploy + scripts)

## 1. Goal

Ship the React 19 + Vite SPA from TagPulse-UI to Azure Static Web Apps
on every merge to `main`, with the same deployment ergonomics the
backend established in Sprint 22 (per-env `.env.<env>` files, `*-cicd-setup.sh` scripts, OIDC where possible, multi-cloud-shaped `deploy/<provider>/ui/` layout).

Non-goals (deferred per [ADR-018 §5](../adr/018-frontend-cloud-deployment.md)): custom domain, SWA Standard tier, Front Door + WAF, SWA-managed auth, CSP lock-down.

## 2. Topology

```
┌──────────────────────────┐         ┌──────────────────────────┐
│ TagPulse (this repo)     │         │ TagPulse-UI              │
│                          │         │                          │
│ azd up                   │         │ npm run build → dist/    │
│  └─ Bicep provisions:    │         │  └─ GHA uploads to SWA   │
│     - api/worker/migs    │         │     via deployment token │
│     - SWA (empty)        │         │                          │
│     - outputs:           │ ──────▶ │ env file consumed:       │
│       apiFqdn            │  CI/CD  │   AZURE_STATIC_WEB_APPS_*│
│       SWA hostname       │  vars   │   VITE_API_BASE_URL      │
│       SWA api token      │         │                          │
│                          │ ◀────── │ SWA hostname pasted into │
│ CORS_ALLOW_ORIGINS       │  manual │ CORS_ALLOW_ORIGINS, then │
│   gets the SWA hostname  │  step   │ azd provision re-run     │
└──────────────────────────┘         └──────────────────────────┘
```

## 3. Deliverables

### This repo (TagPulse)

1. **`scripts/azd-ui-token.sh`** — read-only helper. Runs `az staticwebapp secrets list --name $(azd env get-value AZURE_STATIC_WEB_APPS_NAME) --resource-group $(azd env get-value AZURE_RESOURCE_GROUP) --query properties.apiKey -o tsv`. Used by both the operator (`scripts/azd-cicd-setup.sh` for `tagpulse-ui` Environment vars) and the UI repo's `scripts/ui-bootstrap.sh` to seed `.env.<env>`.
2. **CORS surface in `/health/ready`** — extend the existing `config` snapshot (Sprint 22 A5) to include the resolved `cors.allow_origins` list so SPA-side CORS errors can be diagnosed without shelling into the container.
3. **`deploy/azure/bicep/modules/static-web-app.bicep` outputs** — already exists from Sprint 22 C-1; verify the `apiKey` is **not** exposed as a Bicep output (security-sensitive — operators read it via `az staticwebapp secrets list` instead, with proper RBAC gating).
4. **`deploy/aws/ui/` skeleton** — `README.md` + `main.tf` stub (`# TODO Sprint 25+`). Same shape as Sprint 22 F1.
5. **`deploy/gcp/ui/` skeleton** — `README.md` + `main.tf` stub (`# TODO Sprint 25+`). Same shape as Sprint 22 F2.
6. **`docs/runbooks/ui-first-deploy.md`** — mirrors the structure of `azure-first-deploy.md`. Six phases: prereqs, per-env bootstrap, first deploy, smoke tests, CI/CD wiring, production cutover gates. Top-N common-failures table.
7. **Update `docs/runbooks/azure-first-deploy.md` Phase 3** — new step: "after `azd up` reports `staticWebAppHostname`, copy the value into `CORS_ALLOW_ORIGINS=https://${api-hostname},https://${swa-hostname}` in `deploy/azure/.env.<env>` and re-run `azd-env-load.sh <env> && azd provision`."

### TagPulse-UI repo

8. **`staticwebapp.config.json`** — SPA fallback routing (`/*` → `/index.html`), global headers (HSTS 1y, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy strict-origin-when-cross-origin). Permissive starting CSP per ADR-018 §5.
9. **`.env.example` for build-time vars** — at minimum `VITE_API_BASE_URL`. No secrets in this file (the SWA api token is a CI secret, never a build var).
10. **`scripts/ui-bootstrap.sh <env>`** — generates `.env.<env>` (mode 0600) by reading the four needed values out of the backend's `azd env`. Refuses to overwrite without `--force`.
11. **`scripts/ui-env-load.sh <env>`** — `source`-able loader.
12. **`scripts/ui-preflight.sh`** — checks node ≥20, npm ≥10, gh signed in, `az` signed in to the right tenant.
13. **`scripts/ui-cicd-setup.sh <env>`** — idempotent. Creates GitHub Environment `dev`/`staging`/`production`, sets variables `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_STATIC_WEB_APPS_NAME`, `VITE_API_BASE_URL`, uploads `AZURE_STATIC_WEB_APPS_API_TOKEN` as a secret. Supports `--rotate` to regenerate the token via `az staticwebapp secrets reset-api-key`.
14. **`scripts/ui-cicd-verify.sh <env>`** — confirms the Environment, all four variables, and the secret exist.
15. **`.github/workflows/deploy-azure.yml`** — triggered on push to `main` (auto-deploys to `dev`), on `v*` tag (auto-deploys to `staging`), and `workflow_dispatch` (manual deploys to any env, gated by GitHub Environment reviewer rules for `production`). Uses `Azure/static-web-apps-deploy@v1` action with the deployment token. Post-deploy step curls `https://${swa-hostname}/` and asserts HTTP 200 + asset hash present.
16. **`.github/workflows/build-and-test.yml`** — PR build + lint + typecheck + vitest. No deploy on PR — preview deploys come for free from `Azure/static-web-apps-deploy` when `production_branch` is set.
17. **`docs/azure-deploy.md` in the UI repo** — quick-start, links back to `tagpulse/docs/runbooks/ui-first-deploy.md` as the canonical runbook.

## 4. Coordination contract (the three values)

| Direction | Value | How produced | How consumed |
|---|---|---|---|
| TagPulse → UI | `VITE_API_BASE_URL` | `https://$(azd env get-value apiFqdn)` (Bicep output, auto-promoted to azd env values) | Build-time env var; baked into bundle |
| TagPulse → UI | `AZURE_STATIC_WEB_APPS_API_TOKEN` | `az staticwebapp secrets list … apiKey` | GHA secret in UI repo's Environment |
| UI → TagPulse | SWA hostname (`<id>.azurestaticapps.net`) | `azd env get-value AZURE_STATIC_WEB_APPS_HOSTNAME` | Pasted into `CORS_ALLOW_ORIGINS` in this repo's `.env.<env>` |

The UI → TagPulse direction is **one-time per env** (the SWA hostname is stable across deploys). The two TagPulse → UI values are refreshed by re-running `scripts/ui-bootstrap.sh <env>` whenever the backend env is rebuilt.

## 5. Per-env layout

| Env | Backend resource group | SWA name | UI GHA Environment | Branch trigger |
|---|---|---|---|---|
| `dev` | `tagpulse-dev-rg` | `tpdev-ui` | `dev` | `main` push, auto-deploy |
| `staging` | `tagpulse-staging-rg` | `tpstg-ui` | `staging` | `v*` tag, auto-deploy |
| `production` | `tagpulse-prod-rg` | `tpprd-ui` | `production` | manual dispatch, reviewer-gated |

## 6. Multi-cloud parity

The `deploy/<provider>/ui/` skeletons mirror the Sprint 22 F1/F2 backend skeletons:

```
deploy/
  azure/
    bicep/modules/static-web-app.bicep   # real, Sprint 22 C-1
  aws/
    ui/                                  # Sprint 24 skeleton
      README.md
      main.tf                            # TODO Sprint 25+
  gcp/
    ui/                                  # Sprint 24 skeleton
      README.md
      main.tf                            # TODO Sprint 25+
```

The provider mapping documented in each skeleton's README:

| Layer | Azure | AWS | GCP |
|---|---|---|---|
| Static hosting | SWA | S3 (`$web`-equivalent) | Cloud Storage bucket |
| CDN + TLS | SWA built-in | CloudFront + ACM | Cloud CDN + managed cert |
| Custom domain | SWA-managed | Route 53 → CloudFront | Cloud DNS → Cloud LB |
| Deploy mechanism | `Azure/static-web-apps-deploy@v1` | `aws s3 sync` + CloudFront invalidation | `gsutil rsync` + Cloud CDN invalidation |

The Vite `dist/` output is identical across all three. Only the upload step differs.

## 7. Acceptance criteria

- `azd up` from this repo lands an SWA whose `appsettings.VITE_API_BASE_URL` matches the deployed api FQDN (already shipped Sprint 22 C-1; re-verify in this sprint).
- `scripts/ui-bootstrap.sh dev` in TagPulse-UI generates a complete `.env.dev` from a freshly-deployed backend with no manual editing.
- `scripts/ui-cicd-setup.sh dev` followed by a `git push origin main` in TagPulse-UI lands a real SPA bundle on `https://tpdev-ui.<random>.azurestaticapps.net` within 5 minutes.
- The deployed SPA can hit `https://${apiFqdn}/auth/login` and reach the dashboard (= CORS configured correctly + JWT round-trip works).
- `docs/runbooks/ui-first-deploy.md` walks a fresh operator end-to-end without any external context.
- `deploy/aws/ui/README.md` + `deploy/gcp/ui/README.md` exist with provider-mapping tables and `# TODO` stubs (no implementation).
- No regression in the Sprint 22 backend deploy path — running `azd up` without ever touching the UI repo continues to work; the SWA just serves its empty placeholder.

## 8. Sprint 24 → Sprint 25+ deferred

- Real AWS / GCP UI implementations (skeletons only this sprint).
- Custom domain wiring (`app.tagpulse.io`) — gated on registering the domain.
- SWA Standard tier upgrade — gated on Free-tier caps biting.
- Front Door + WAF in front of both SWA + api — paired with the api-side FD work on the Sprint 23+ deferred list.
- CSP lock-down — gated on stable asset manifest.
- E2E test in CI (Playwright against the deployed SWA) — gated on first customer / first design refresh that's worth protecting.

## 9. Open questions

1. **Should the UI repo also ship a portable Helm-style "deploy this dist/ to any S3-shaped target" abstraction**, paralleling the backend's Helm chart (Sprint 22 B4)? *Tentative no — the upload commands are 1-line per provider; no abstraction needed until a third provider lands.*
2. **Do we want to surface the SWA hostname in the api's `/health/ready`** so operators can confirm CORS is configured without grepping env vars? *Yes — landed as part of deliverable #2.*
3. **Should staging/prod use the same SWA token rotation cadence as backend secrets?** *Open. Backend has no automated rotation either. Track on the post-Sprint-24 hardening backlog.*
