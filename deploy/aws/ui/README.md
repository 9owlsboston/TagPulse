# `deploy/aws/ui/` — AWS Static Site (skeleton)

> **Status:** structure-only stub per [Sprint 24 D1](../../../docs/roadmap.md#phase-d--multi-cloud-skeletons-this-repo-backend-structure-only-per-adr-016-precedent).
> Mirrors the Sprint 22 F1 backend skeleton in [`deploy/aws/`](../README.md) (TODO: that README too).
> No resources provision from this directory yet — see [`main.tf`](main.tf).

## Provider mapping (Azure SWA → AWS)

| TagPulse-on-Azure                     | TagPulse-on-AWS                          |
|---------------------------------------|------------------------------------------|
| Azure Static Web App (Free tier)      | S3 bucket (static-website hosting) + CloudFront distribution |
| SWA built-in TLS cert                 | ACM certificate (us-east-1, for CF)      |
| `appsettings.VITE_API_BASE_URL`       | Build-time `VITE_API_BASE_URL` injected by GHA before `npm run build` |
| `staticwebapp.config.json` SPA fallback | CloudFront custom error response: 403/404 → `/index.html` (200) |
| SWA security headers (`globalHeaders`) | CloudFront response-headers policy       |
| SWA reviewer rules on Environment     | CodePipeline manual approval action OR GHA Environment reviewer rule (preferred — keeps the gate in GitHub) |
| `staticWebAppHostname`                | CloudFront distribution domain (`d…cloudfront.net`) + Route 53 alias |

## Deploy mechanism (target shape)

```bash
# Build comes from the UI repo; this directory only owns the upload step.
aws s3 sync ./dist "s3://${BUCKET}" --delete \
    --cache-control 'public, max-age=31536000, immutable' \
    --exclude index.html
aws s3 cp ./dist/index.html "s3://${BUCKET}/index.html" \
    --cache-control 'no-cache, no-store, must-revalidate'
aws cloudfront create-invalidation \
    --distribution-id "${DIST_ID}" --paths '/*'
```

Note the two-pass upload: hashed asset bundles get a 1-year immutable
TTL; `index.html` is force-revalidated so a deploy is visible
immediately after the invalidation completes.

## CORS coupling

The deployed CloudFront domain (or the custom domain alias) MUST be in
the backend's `CORS_ALLOW_ORIGINS` allow-list. On Azure that's the
[Phase 3a step](../../../docs/runbooks/azure-first-deploy.md#3a--add-the-swa-hostname-to-cors-sprint-24-a4)
in the first-deploy runbook; on AWS the equivalent goes into the
backend `.env.<env>` for the AWS deployment (Sprint 22 F1 deferred).

## TODO (Sprint 25+)

- [ ] `main.tf` — S3 + CloudFront + ACM + Route 53 alias module
- [ ] `scripts/aws-ui-bootstrap.sh <env>` — mirror of UI repo's
      `scripts/ui-bootstrap.sh` for AWS
- [ ] `.github/workflows/deploy-aws-ui.yml` (in the UI repo)
- [ ] First-deploy runbook entry under [`docs/runbooks/`](../../../docs/runbooks/)
