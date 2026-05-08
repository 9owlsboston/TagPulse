# UI First-Deploy Runbook (Azure Static Web App)

Six-phase checklist for shipping the TagPulse-UI React 19 + Vite SPA into
the Azure Static Web App that this repo's Sprint 22 C-1 Bicep already
provisions.

Companion to [azure-first-deploy.md](azure-first-deploy.md). The backend
is the prerequisite — the SWA is empty until the UI repo deploys into
it. See [ADR-018](../adr/018-frontend-cloud-deployment.md) for *why* the
two repos ship separately.

> **Companion repo:** [9owlsboston/TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI)
> All Phase B scripts (`scripts/ui-*.sh`) and workflows live in that repo.
> All Bicep, runbooks, and the SWA resource itself live here.

---

## Phase 0 — Prereqs

- [ ] `node --version` ≥ 20
- [ ] `npm --version` ≥ 10
- [ ] `gh auth status` — signed in to GitHub as a member of `9owlsboston`
- [ ] `az account show` — signed in to the same tenant the backend env
      points at; subscription matches `AZURE_SUBSCRIPTION_ID` in
      `deploy/azure/.env.<env>`
- [ ] Backend `azd up` for the same env succeeded (this runbook depends
      on the SWA + api FQDN existing in Azure)
- [ ] Backend [Phase 3a CORS step](azure-first-deploy.md#3a--add-the-swa-hostname-to-cors-sprint-24-a4)
      complete — the SWA hostname is in `CORS_ALLOW_ORIGINS` and a fresh
      `azd provision` has rolled the api revision. `curl
      "https://$(azd env get-value apiFqdn)/health/ready" | jq
      '.config.cors.allow_origins'` must include `https://<swa-host>`.
- [ ] UI repo cloned: `gh repo clone 9owlsboston/TagPulse-UI && cd TagPulse-UI`

---

## Phase 1 — Per-env bootstrap (UI repo)

Generates `.env.<env>` from this repo's `azd env get-values` plus the
SWA deployment token. Run from the **UI repo root**.

- [ ] `scripts/ui-preflight.sh` exits 0 (node/npm/gh/az versions OK)
- [ ] `scripts/ui-bootstrap.sh <env>` writes `.env.<env>` at mode 0600
      with `VITE_API_BASE_URL` (= `https://$(azd env get-value apiFqdn)` from
      the backend repo), `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
      `AZURE_STATIC_WEB_APPS_NAME`, and the deployment token (which the
      script pulls from this repo's `scripts/azd-ui-token.sh`)
- [ ] `scripts/ui-env-load.sh <env>` sourced into the current shell

The bootstrap helper looks for the backend repo by `$TAGPULSE_REPO`
(env var) or falls back to `../TagPulse` from the UI repo root.

---

## Phase 2 — First manual deploy (validate the wiring)

Skip CI/CD on the first deploy so you debug only the resource path, not
the GHA workflow on top of it.

- [ ] `npm ci`
- [ ] `npm run build` — produces `dist/`
- [ ] `npx @azure/static-web-apps-cli deploy ./dist \
        --deployment-token "$(scripts/azd-ui-token.sh <env> --print)" \
        --env <env>` exits 0
- [ ] CLI prints a deploy URL like
      `https://tpdev-ui.<random>.azurestaticapps.net` — note it

---

## Phase 3 — Post-deploy smoke tests

- [ ] `curl -sf "https://${SWA_HOST}/" | grep -q '<div id="root">'` —
      SPA shell served
- [ ] Open the URL in a browser; the app loads without console errors
- [ ] `POST https://$(azd env get-value apiFqdn)/auth/login` from the
      SPA succeeds (open DevTools → Network) — proves CORS + JWT round-trip
- [ ] `curl "https://$(azd env get-value apiFqdn)/health/ready" | jq
      '.config.cors.allow_origins'` includes the deployed SWA hostname
      (Sprint 24 A2)
- [ ] App Insights → **Page views** — the SPA emits `pageView` telemetry
      within 2 minutes of a fresh session (only if A.I. SDK wired in
      Phase B; safe to skip otherwise)

---

## Phase 4 — CI/CD wiring (one-time, per environment)

> **Shortcut:** `scripts/ui-cicd-setup.sh <env>` from the UI repo
> performs every step here. `scripts/ui-cicd-verify.sh <env>` confirms.

- [ ] `scripts/ui-cicd-setup.sh <env>` exits 0
- [ ] `scripts/ui-cicd-verify.sh <env>` exits 0
- [ ] GitHub Environment exists in `9owlsboston/TagPulse-UI` named
      exactly `dev` / `staging` / `production`
- [ ] Environment **variables**: `AZURE_TENANT_ID`,
      `AZURE_SUBSCRIPTION_ID`, `AZURE_STATIC_WEB_APPS_NAME`,
      `VITE_API_BASE_URL`
- [ ] Environment **secret**: `AZURE_STATIC_WEB_APPS_API_TOKEN`
- [ ] First push to `main` triggers `.github/workflows/deploy-azure.yml`
      → `dev` Environment → SPA visible at `https://${SWA_HOST}/` within
      5 minutes; workflow exit 0

---

## Phase 5 — Production cutover gates

- [ ] `production` Environment has reviewer rule (≥ 1 approver from
      `@9owlsboston/maintainers`)
- [ ] Deployment branches restricted to `v*` tags + `main`
- [ ] Notification channel wired for deploy failures (workflow
      `failure` → Slack / email — the GHA built-in `notification`
      checkbox is sufficient for v1)
- [ ] First-deploy tag pushed: `git tag v0.24.0 && git push --tags`
      triggers `deploy-azure.yml` → `production` Environment → reviewer
      approves → SPA at `https://${prod-swa-host}/` shows v0.24.0 build
      hash in `meta[name=build-id]`

---

## Top-N common failures

| Symptom | Root cause | Fix |
|---|---|---|
| SPA loads, every fetch is `CORS error` in console | Backend `CORS_ALLOW_ORIGINS` doesn't include the SWA hostname | Phase 0 prereq + [azure-first-deploy.md Phase 3a](azure-first-deploy.md#3a--add-the-swa-hostname-to-cors-sprint-24-a4); re-run `azd provision` |
| `swa deploy` exits with `401 Unauthorized` | Deployment token rotated since `.env.<env>` was generated | `scripts/ui-cicd-setup.sh <env> --rotate` (UI repo) |
| Production build calls `http://localhost:8000` | `VITE_API_BASE_URL` not baked in at build time (Vite inlines `VITE_*` only at build, not runtime) | Re-run `npm run build` after `scripts/ui-env-load.sh <env>` |
| `swa deploy` exits 0 but the site 404s | `staticwebapp.config.json` has a JSON syntax error (the action does not validate) | `jq . staticwebapp.config.json` locally; the file MUST sit in `dist/` after build |
| `azd up` fails before the SWA is created with `LocationNotAvailableForResourceType` | The `staticWebAppLocation` param in `main.bicepparam` isn't in the SWA Free-tier region allow-list | Pick from `westus2 / centralus / eastus2 / westeurope / eastasia` per [static-web-app.bicep](../../deploy/azure/bicep/modules/static-web-app.bicep) |
| GHA workflow fails on `Azure/static-web-apps-deploy@v1` with `Repository token not found` | Used the wrong secret name | The token must be `AZURE_STATIC_WEB_APPS_API_TOKEN` (verbatim) — `scripts/ui-cicd-setup.sh` enforces this |

---

## Decommission

- [ ] `az staticwebapp delete --name "$AZURE_STATIC_WEB_APPS_NAME"
      --resource-group "$AZURE_RESOURCE_GROUP" --yes` — deletes
      immediately, no soft-delete reservation (unlike Key Vault)
- [ ] Remove the `static-web-app.bicep` module invocation from
      `workload.bicep` and re-run `azd provision` to drop it from
      Bicep state, OR leave the module in and re-`azd up` to
      recreate the empty placeholder
- [ ] Revoke the GitHub Environment in the UI repo and delete the
      `AZURE_STATIC_WEB_APPS_API_TOKEN` secret

---

## Cross-references

* Sprint 24 plan: [docs/roadmap.md](../roadmap.md#sprint-24--frontend-cloud-deployment-parity-with-sprint-22-backend-deploy)
* ADR: [018-frontend-cloud-deployment.md](../adr/018-frontend-cloud-deployment.md)
* Design: [design/frontend-deployment.md](../design/frontend-deployment.md)
* Backend runbook: [azure-first-deploy.md](azure-first-deploy.md)
* SWA Bicep: [deploy/azure/bicep/modules/static-web-app.bicep](../../deploy/azure/bicep/modules/static-web-app.bicep)
* Token helper: [scripts/azd-ui-token.sh](../../scripts/azd-ui-token.sh)
