# `deploy/portable/ui/` — Provider-agnostic UI deploy notes

> **Status:** structure-only stub per [Sprint 24 D3](../../../docs/roadmap.md#phase-d--multi-cloud-skeletons-this-repo-backend-structure-only-per-adr-016-precedent).

## What is portable

The UI build output (`dist/` from `npm run build` in the
[TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) repo) is
**identical across providers**. It's a static set of HTML / JS / CSS
chunks, hashed for cache-busting, with `index.html` as the SPA shell
and `staticwebapp.config.json` as the only Azure-specific file (other
providers ignore it harmlessly).

No code change is required to retarget the SPA to a different cloud.
Only the deploy step + the CORS allow-list on the backend differ.

## What differs (one-line per provider)

| Provider | Upload command (after `npm run build`) |
|---|---|
| Azure SWA | `npx @azure/static-web-apps-cli deploy ./dist --deployment-token "$TOKEN" --env <env>` |
| AWS S3 + CloudFront | `aws s3 sync ./dist s3://${BUCKET} --delete && aws cloudfront create-invalidation --distribution-id ${DIST_ID} --paths '/*'` |
| GCP Cloud Storage + Cloud LB | `gsutil -m rsync -d -r ./dist gs://${BUCKET} && gcloud compute url-maps invalidate-cdn-cache ${URL_MAP} --path '/*' --async` |

In every case the `index.html` should override the long-lived cache
header that hashed assets get, so a deploy goes live on the next
request without waiting for a TTL to expire. See the per-provider
READMEs for the two-pass upload recipe.

## Cross-cloud DR

If the primary provider fails, the SPA can be re-deployed to a backup
provider in minutes — the bundle is in the artifact store of the
release that built it (GHA `deploy-azure.yml` or equivalent uploads
`dist.zip` as a workflow artifact, retained 30 days). Steps:

1. `gh run download <run-id> --name dist`
2. Set `VITE_API_BASE_URL` for the backup-provider backend (the SPA's
   API base URL is build-time, not runtime, so a rebuild is required if
   the backup backend has a different FQDN).
3. Run the upload command for the backup provider from the table above.
4. Update DNS (the only stateful coupling — the backup provider's CDN
   hostname or LB IP needs the user-facing custom domain).

The Azure SWA runbook ([`ui-first-deploy.md`](../../../docs/runbooks/ui-first-deploy.md))
is the template; per-provider runbooks land alongside `main.tf`
implementations in Sprint 25+.

## TODO (Sprint 25+)

- [ ] Cross-cloud DR drill (primary Azure SWA → backup AWS CloudFront)
- [ ] Cross-cloud DR appendix in [`docs/runbooks/`](../../../docs/runbooks/)
      paired with the Sprint 22 D-3 backend DR doc when that lands
- [ ] CDN-cache TTL recommendation table (per asset type, per provider)
