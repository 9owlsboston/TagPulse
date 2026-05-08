# `deploy/gcp/ui/` — GCP Static Site (skeleton)

> **Status:** structure-only stub per [Sprint 24 D2](../../../docs/roadmap.md#phase-d--multi-cloud-skeletons-this-repo-backend-structure-only-per-adr-016-precedent).
> Mirrors the Sprint 22 F2 backend skeleton in [`deploy/gcp/`](../README.md) (TODO: that README too).
> No resources provision from this directory yet — see [`main.tf`](main.tf).

## Provider mapping (Azure SWA → GCP)

| TagPulse-on-Azure                     | TagPulse-on-GCP                          |
|---------------------------------------|------------------------------------------|
| Azure Static Web App (Free tier)      | Cloud Storage bucket (website mode) behind a Cloud Load Balancing HTTPS LB |
| SWA built-in TLS cert                 | Google-managed SSL certificate           |
| `appsettings.VITE_API_BASE_URL`       | Build-time `VITE_API_BASE_URL` injected by GHA before `npm run build` |
| `staticwebapp.config.json` SPA fallback | Cloud Storage `MainPageSuffix=index.html`, `NotFoundPage=index.html` (200 served via LB URL map rewrite) |
| SWA security headers                  | LB backend-bucket header policy (or sidecar Cloud Function) |
| SWA reviewer rules                    | GHA Environment reviewer rule (preferred — keeps the gate in GitHub) |
| `staticWebAppHostname`                | LB IP + Cloud DNS A/AAAA record (or `gs://${bucket}` direct domain) |

## Deploy mechanism (target shape)

```bash
gsutil -m -h 'Cache-Control:public,max-age=31536000,immutable' \
    rsync -d -r ./dist gs://${BUCKET}
gsutil -h 'Cache-Control:no-cache,no-store,must-revalidate' \
    cp ./dist/index.html gs://${BUCKET}/index.html
gcloud compute url-maps invalidate-cdn-cache "${URL_MAP}" \
    --path '/*' --async
```

Two-pass upload mirrors the AWS pattern: hashed assets get long
immutable TTL; `index.html` force-revalidates so a deploy is visible
once the CDN cache invalidation completes.

## CORS coupling

The deployed LB hostname (or custom domain) MUST be in the backend's
`CORS_ALLOW_ORIGINS` allow-list. Equivalent of the Azure
[Phase 3a step](../../../docs/runbooks/azure-first-deploy.md#3a--add-the-swa-hostname-to-cors-sprint-24-a4).

## TODO (Sprint 25+)

- [ ] `main.tf` — Cloud Storage bucket + LB + managed SSL + Cloud DNS module
- [ ] `scripts/gcp-ui-bootstrap.sh <env>` — mirror of UI repo's
      `scripts/ui-bootstrap.sh` for GCP
- [ ] `.github/workflows/deploy-gcp-ui.yml` (in the UI repo)
- [ ] First-deploy runbook entry under [`docs/runbooks/`](../../../docs/runbooks/)
