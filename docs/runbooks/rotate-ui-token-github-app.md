# Eliminate the cross-repo PAT — migrate `rotate-ui-token` to a GitHub App

**Summary — what / who / when.** The `rotate-ui-token` workflow writes the
freshly-rotated Static Web App deploy token into the `TagPulse-UI` repo. It
authenticates to that *other* repo with a long-lived fine-grained PAT
(`UI_REPO_SECRETS_PAT`), which **expires and breaks the quarterly cron
silently** (runs `#36887295240`, `#28515415671`). This one-time cutover
replaces that PAT with a **GitHub App installation token** — minted per run,
**never expires, nothing to rotate**. For the repo owner (`9owlsboston`).
Do it once; the workflow already supports it and falls back to the PAT until
you finish, so there is **zero downtime**.

> **Why an App, not a PAT?** A fine-grained PAT is a *user* credential with a
> hard max expiry (≤ 1 year) that a human must re-mint. A GitHub App is an
> *installation* identity: `actions/create-github-app-token` exchanges the
> App's private key for a short-lived token scoped to exactly the repos the
> App is installed on, on every run. No expiry to track, smaller blast radius.

## How the workflow already supports this

`.github/workflows/rotate-ui-token.yml` mints the cross-repo token with a
GitHub-App-first, PAT-fallback expression:

```yaml
- name: Mint cross-repo token (GitHub App → PAT fallback)
  id: appauth
  if: steps.gate.outputs.run == 'true' && vars.UI_SECRETS_APP_ID != ''
  uses: actions/create-github-app-token@v2
  with:
    app-id: ${{ vars.UI_SECRETS_APP_ID }}
    private-key: ${{ secrets.UI_SECRETS_APP_PRIVATE_KEY }}
    owner: ${{ vars.UI_SECRETS_APP_OWNER || '9owlsboston' }}
    repositories: ${{ vars.UI_SECRETS_APP_REPOS || 'TagPulse-UI' }}
# ... downstream steps use:
#   GH_TOKEN: ${{ steps.appauth.outputs.token || secrets.UI_REPO_SECRETS_PAT }}
```

- **Before** you set `UI_SECRETS_APP_ID`: the mint step is skipped, its output
  is empty, and the workflow uses `UI_REPO_SECRETS_PAT` — today's behaviour.
- **After** you set it: the workflow uses the App token and ignores the PAT.
- The `pat-expiry-check.yml` canary **self-disables** once `UI_SECRETS_APP_ID`
  is set (there's no PAT left to expire).

So the cutover is purely config — **no code change, no PR** — and reversible
(unset the variable to fall back).

## Steps (one-time, ~10 min)

### 1. Create the GitHub App  *(browser — owner only)*

**Fast path — one prefilled link.** While signed in to GitHub **as
`9owlsboston`**, open this URL; it opens the *New GitHub App* form with the
name, homepage, webhook-off, and both permissions (`Secrets: write`,
`Environments: read`) already filled. Review, then click **Create GitHub App**:

```text
https://github.com/settings/apps/new?name=tagpulse-ui-secrets&url=https://github.com/9owlsboston/TagPulse&public=false&webhook_active=false&secrets=write&environments=read
```

Or fill it manually: `github.com` → your avatar → **Settings** →
**Developer settings** → **GitHub Apps** → **New GitHub App**.

| Field | Value |
|---|---|
| GitHub App name | `tagpulse-ui-secrets` (any unique name) |
| Homepage URL | `https://github.com/9owlsboston/TagPulse` (anything) |
| Webhook | **Uncheck "Active"** — this App takes no webhooks |
| Repository permissions → **Secrets** | **Read and write** |
| Repository permissions → **Environments** | **Read-only** *(lets it resolve `environments/<env>/secrets/public-key`)* |
| Where can this App be installed? | **Only on this account** |

Leave every other permission at *No access* (least privilege). Click
**Create GitHub App**.

### 2. Generate a private key

On the new App's page → **Private keys** → **Generate a private key**. A
`*.pem` downloads. Note the **App ID** shown at the top of the page.

### 3. Install the App on `TagPulse-UI`

App page → **Install App** → install on the `9owlsboston` account →
**Only select repositories** → choose **`TagPulse-UI`** → **Install**.

> If you later add `staging`/`production` SWAs on other UI repos, add them to
> the installation and to `UI_SECRETS_APP_REPOS`.

### 4. Wire the credentials into `TagPulse` (this repo)

Repo `9owlsboston/TagPulse` → **Settings** → **Secrets and variables** →
**Actions**:

- **Variables** tab → **New repository variable**
  - `UI_SECRETS_APP_ID` = the App ID from step 2
  - *(optional)* `UI_SECRETS_APP_OWNER` = `9owlsboston` *(default already)*
  - *(optional)* `UI_SECRETS_APP_REPOS` = `TagPulse-UI` *(default already)*
- **Secrets** tab → **New repository secret**
  - `UI_SECRETS_APP_PRIVATE_KEY` = **full contents** of the `.pem`
    (including the `-----BEGIN/END ...-----` lines)

CLI equivalent (run as the `9owlsboston` gh account):

```bash
gh variable set UI_SECRETS_APP_ID    -R 9owlsboston/TagPulse --body '<APP_ID>'
gh secret   set UI_SECRETS_APP_PRIVATE_KEY -R 9owlsboston/TagPulse < app-private-key.pem
```

### 5. Verify

```bash
gh workflow run rotate-ui-token.yml -R 9owlsboston/TagPulse -f force=true
gh run watch -R 9owlsboston/TagPulse "$(gh run list -R 9owlsboston/TagPulse \
  -w rotate-ui-token --limit 1 --json databaseId --jq '.[0].databaseId')"
```

In the run log, confirm the **"Mint cross-repo token (GitHub App → PAT
fallback)"** step executed (not skipped) and the preflight prints
`✓ token validated for 9owlsboston/TagPulse-UI env=dev secret writes`.

### 6. Retire the PAT

Once a run succeeds via the App:

- Delete the `UI_REPO_SECRETS_PAT` repo secret on `TagPulse`.
- Revoke the old fine-grained PAT in **Settings → Developer settings →
  Personal access tokens**.
- `pat-expiry-check.yml` is already dormant (step-4 `UI_SECRETS_APP_ID` set).
  You may delete it, or keep it inert as documentation.
- Delete the `.pem` from your laptop (`UI_SECRETS_APP_PRIVATE_KEY` is now the
  only copy that matters).

## Rollback

Unset `UI_SECRETS_APP_ID` (or delete the variable). The next run skips the
mint step and falls back to `UI_REPO_SECRETS_PAT` — so keep the PAT until
step 5 passes.

## Security notes

- The App's **only** power is read/write **Secrets** (+ read **Environments**)
  on `TagPulse-UI` — a smaller blast radius than a PAT's `repo`/secrets scope.
- The private key lives solely in the `UI_SECRETS_APP_PRIVATE_KEY` secret;
  `create-github-app-token` revokes each minted installation token at job end.
- No webhook, no account-wide install.

## Source

- Workflow: [`.github/workflows/rotate-ui-token.yml`](../../.github/workflows/rotate-ui-token.yml)
- Canary: [`.github/workflows/pat-expiry-check.yml`](../../.github/workflows/pat-expiry-check.yml)
- Rotation script: [`scripts/azd-ui-token-rotate.sh`](../../scripts/azd-ui-token-rotate.sh)
- Related: [secret-rotation.md](secret-rotation.md) · [github-workflows.md](github-workflows.md)
- Action: [`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)
