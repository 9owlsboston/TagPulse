# `deploy/portable/ui/` — Provider-agnostic UI deploy recipes

> **Status:** executable recipes (Sprint 25 E1). Bicep / Terraform
> infra-as-code for non-Azure providers is still deferred to Sprint 26+
> per [ADR 016](../../../docs/adr/016-multi-cloud-deployment-strategy.md).

## What is portable

The UI build output (`dist/` from `npm run build` in the
[TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) repo) is
**identical across providers**. It's a static set of HTML / JS / CSS
chunks, hashed for cache-busting, with `index.html` as the SPA shell
and `staticwebapp.config.json` as the only Azure-specific file (other
providers ignore it harmlessly).

No code change is required to retarget the SPA to a different cloud.
Only the upload step + the CORS allow-list on the backend differ.

## What differs (one-line per provider)

| Provider | Upload command (after `npm run build`) |
|---|---|
| Azure SWA | `npx @azure/static-web-apps-cli deploy ./dist --deployment-token "$TOKEN" --env <env>` |
| AWS S3 + CloudFront | `aws s3 sync ./dist s3://${BUCKET} --delete && aws cloudfront create-invalidation --distribution-id ${DIST_ID} --paths '/*'` |
| Cloudflare Pages | `npx wrangler pages deploy ./dist --project-name=tagpulse-ui --branch=main` |
| GCP Cloud Storage + Cloud LB | `gsutil -m rsync -d -r ./dist gs://${BUCKET} && gcloud compute url-maps invalidate-cdn-cache ${URL_MAP} --path '/*' --async` |

In every case `index.html` should override the long-lived cache header
that hashed assets get, so a deploy goes live on the next request
without waiting for a TTL to expire. The two-pass recipes below all
implement that pattern.

---

## Recipes

The recipes assume:

- A current `dist/` directory built from the UI repo.
- Provider auth already configured (`aws sso login`, `wrangler login`,
  `gcloud auth login`, etc.) — these recipes do **not** wire credentials.
- The user has set `VITE_API_BASE_URL` at build time to the correct
  backend FQDN for the target environment.

### AWS S3 + CloudFront

```bash
# Required: BUCKET (s3 bucket name), DIST (CloudFront distribution id),
# AWS_REGION. The bucket must already exist with static-website hosting
# enabled or be fronted by an OAC-attached CloudFront distribution.

set -euo pipefail
: "${BUCKET:?}" "${DIST:?}" "${AWS_REGION:?}"

# Pass 1: hashed assets — cache forever (1 year, immutable).
aws s3 sync ./dist "s3://${BUCKET}/" \
    --delete \
    --exclude "index.html" \
    --exclude "staticwebapp.config.json" \
    --cache-control "public, max-age=31536000, immutable"

# Pass 2: index.html — never cache; this is what makes a deploy go live.
aws s3 cp ./dist/index.html "s3://${BUCKET}/index.html" \
    --cache-control "no-store, must-revalidate" \
    --content-type "text/html"

# Pass 3: invalidate the CDN edge for index.html only — the hashed
# assets don't need invalidation because their URLs change per build.
aws cloudfront create-invalidation \
    --distribution-id "${DIST}" \
    --paths "/index.html" "/"
```

Common gotcha: an OAI-protected bucket (legacy) needs the SPA
fall-through routing rule on the CloudFront error page (404 → /index.html
with HTTP 200), otherwise deep-linking into a SPA route returns
S3's 404 XML.

### Cloudflare Pages

```bash
# Required: CF_ACCOUNT_ID, CLOUDFLARE_API_TOKEN (env vars).
# wrangler auto-creates the Pages project on first deploy.

set -euo pipefail
: "${CF_ACCOUNT_ID:?}" "${CLOUDFLARE_API_TOKEN:?}"

npx wrangler@latest pages deploy ./dist \
    --project-name=tagpulse-ui \
    --branch=main \
    --commit-dirty=true
```

Cloudflare handles the cache-headers split for you (it serves
`index.html` with `Cache-Control: public, max-age=0, must-revalidate`
and hashed assets with `immutable` automatically). SPA fall-through
also works out of the box.

### GCP Cloud Storage + Cloud Load Balancer

```bash
set -euo pipefail
: "${BUCKET:?}" "${URL_MAP:?}"

# Pass 1: hashed assets — cache forever.
gsutil -m -h "Cache-Control:public, max-age=31536000, immutable" \
    rsync -d -r -x '^(index\.html|staticwebapp\.config\.json)$' \
    ./dist "gs://${BUCKET}"

# Pass 2: index.html — no-cache.
gsutil -h "Cache-Control:no-store, must-revalidate" \
    cp ./dist/index.html "gs://${BUCKET}/index.html"

# Pass 3: invalidate the LB edge for index.html.
gcloud compute url-maps invalidate-cdn-cache "${URL_MAP}" \
    --path "/index.html" \
    --async
```

---

## Cross-cloud DR drill (paired with Sprint 22 D3)

The Sprint 22 D3 backend DR runbook covers spinning up a backup-cloud
backend; this section covers the matching UI failover. The bundle is in
the GHA artifact store of the release that built it (the UI repo's
`build` workflow uploads `dist.zip`, retained 30 days).

Drill steps:

1. **Fetch the artifact:**
   ```bash
   gh -R 9owlsboston/TagPulse-UI run download <run-id> --name dist
   unzip dist.zip -d ./dist
   ```
2. **Rebuild if the backup backend FQDN differs.** `VITE_API_BASE_URL`
   is bound at build time. If the backup backend has a different
   hostname, run `npm run build` against the UI repo with
   `VITE_API_BASE_URL=https://<backup-fqdn>` exported.
3. **Run the upload recipe** for the backup provider above.
4. **Update DNS** — the only stateful coupling. The backup provider's
   CDN hostname or LB IP needs the user-facing custom domain CNAME'd
   to it. Drill convention: pre-stage the DNS record with TTL=60 so
   cutover takes ~1 minute.
5. **Verify** by hitting `/health/live` from the SPA's network tab.
   Sprint 25 A1 made `/health/live` SPA-readable from any origin in
   the CORS allow-list.

### Per-provider drill notes

- **AWS:** if the bucket is OAC-protected, the IAM principal running
  the recipe needs `s3:PutObject` + `s3:DeleteObject` on the bucket and
  `cloudfront:CreateInvalidation` on the distribution.
- **Cloudflare:** the API token needs `Account.Cloudflare Pages:Edit`.
  Pages deploys are atomic — there is no partial-deploy state to roll
  back from.
- **GCP:** the LB-edge invalidation can take up to 5 minutes to
  propagate; the `--async` flag returns immediately, but the user-
  facing edge may still serve stale `index.html` until the invalidation
  completes.

## Open work (Sprint 26+)

- [ ] Bicep / Terraform modules for the CloudFront and Cloudflare
      Pages provisioning steps (currently click-ops or one-off scripts).
- [ ] DR drill in CI (currently manual; needs a synthetic backup
      backend in the same drill workflow).
- [ ] CDN-cache TTL recommendation table per asset type, per provider
      (the recipes above use the conservative defaults).
