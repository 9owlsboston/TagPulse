# Documentation audit — Sprint 28 H1

Inventory of every `.md` under `docs/`, plus top-level `README` / `CONTRIBUTING`
/ `CHANGELOG`, plus per-folder `README` files under `deploy/`, `ops/`, and
`tests/conformance/`. Generated programmatically from `git log -1` per file
and a regex-based inbound-link count across the same corpus.

Drives H2 (runbook index), H3 (operator quickstart), H4 (README +
architecture refresh), and H5 (CI markdown lint + link check).

**As-of:** Sprint 28 (2026-05-10). Re-run via the inline `python3` script in
[Sprint 28 H1 commit message](../roadmap.md#sprint-28).

---

## Summary

- **Total markdown files inventoried:** 79
- **Last sprint mentioned anywhere in commit history:** sprint-29 (CHANGELOG
  forward-reference) — current shipped sprint is 28
- **Files last touched in sprint-28:** 14 (this sprint's own deliverables)
- **Files ≥3 sprints stale (last touched in sprint-25 or earlier) AND still
  inbound-referenced:** see [Flagged: stale](#flagged-stale)
- **Files with ≤1 inbound link:** see [Flagged: orphaned](#flagged-orphaned)
- **Files with content contradicting current code:** see [Flagged:
  contradictory](#flagged-contradictory)

The full inventory table follows the flag sections so reviewers can scan the
flags first.

---

## Flagged: stale

Files whose last sprint mention is sprint-25 or older AND that still have
inbound links (i.e., something points readers at them, so the staleness
matters). Stale-but-orphaned files are listed under "orphaned" only.

| File | Last sprint | Inbound | Action |
|---|---|---|---|
| `docs/adr/012-mtls-for-mqtt.md` | sprint-17 | 5 | **Update for Sprint 28 C6.** The ADR is still labelled `Proposed`. Sprint 28 C6 ships server-TLS on `:8883` (no client cert). Add a `Status: Partially Implemented (Sprint 28 — server-auth only; mTLS deferred)` line + a follow-up section. H4-scoped. |
| `docs/design/storage-strategy.md` | sprint-15 | 10 | Spot-checked: still accurate (PG + Timescale, hot/warm/cold buckets, lifecycle to ADLS). No edit needed. |
| `docs/design/ui-authentication.md` | sprint-13 | 2 | Mostly accurate; doesn't mention the Static Web App deploy model from Sprint 24. Append a "Production deployment" pointer to `docs/design/frontend-deployment.md` (H4). |
| `docs/runbooks/device-token-rotation.md` | sprint-16 | 4 | Procedures still work; the KV references should namespace as `tp${env}-kv-*` per the Sprint 26 naming. Minor edit during H2 runbook-index review. |
| `docs/runbooks/geofence-postgis-trigger.md` | sprint-17 | 3 | Still accurate — the trigger code hasn't changed. No edit. |
| `docs/data-models.md` | sprint-18 | 12 | Still authoritative; cite-only references from 11 other docs. Re-verify after H6 OpenAPI refresh that the table fields match `models/database.py` (planned for sprint-29). |
| `docs/quickstart.md` | sprint-19 | 5 | High-value entry-point doc, but it predates the Sprint 22+ Azure deploy story and Sprint 26 KV ergonomics. **H3 (operator-quickstart.md) is the targeted replacement for the operator audience**; quickstart.md stays as the developer-laptop on-ramp. H4 will add a banner pointing operators at H3. |
| `docs/user-guide.md` | sprint-19 | 3 | UI walkthroughs from Sprint 19. Spot-checked the screenshots/copy: still matches the current SPA. No edit. |

## Flagged: orphaned

Files with ≤1 inbound link from any other md in the repo. Either intentional
endpoints (entry-points indexed externally, e.g., README), or candidates for
deletion / consolidation.

| File | Inbound | Status |
|---|---|---|
| `docs/design/production-hardening.md` | 1 | **Keep.** Sprint 22 hardening checklist; referenced from roadmap. Add it to the runbook index ([H2](#h2-runbook-index)) under "Migrations & cutovers" so it's not invisible. |
| `docs/guides/device-developer-guide.md` | 1 | **Keep.** Entry-point doc for edge developers; should be linked from README + `clients/pi/README.md` (H4 task). |
| `docs/refs/IoT.md` | 1 | **Keep.** Background reference, not an operator doc. Already labelled `refs/`. |
| `docs/review-checklist.md` | 1 | **Keep.** Linked from `CONTRIBUTING.md`. No action. |
| `.pytest_cache/README.md` | 14 | **Ignore.** Generated; will be excluded from H5 markdownlint via `.markdownlintignore`. |

## Flagged: contradictory

Files whose content disagrees with current code or current scripts. Most-recent
first.

| File | Issue | Fix |
|---|---|---|
| `docs/adr/012-mtls-for-mqtt.md` | Says mTLS is "the future direction" with no implementation status. Sprint 28 C6 shipped server-TLS via the optional `mqttTlsEnabled` Bicep param + `MQTT_USE_TLS` worker config. | Add `Status: Partially Implemented (server-TLS in Sprint 28; mTLS still deferred)` section. **H4 will land this edit.** |
| `docs/runbooks/azure-first-deploy.md` | References `scripts/azd-kv-get.sh` and `scripts/azd-mqtt-restart.sh` with usage signatures that pre-date the F2 shared-lib refactor. | Spot-checked during Sprint 28 F2; signatures still compatible (positional `<env>` first arg). No edit. |
| `docs/runbooks/README.md` | 14-line stub. Doesn't index any of the 13 runbooks now under `docs/runbooks/`. | **H2** replaces this file with a categorized table. |
| `README.md` (top-level) | "Status" wording predates Sprint 22 Azure deploy. The "Deployment" section mentions Helm + Docker but not `azd up` as the primary path. | **H4** rewrites Status + Deployment sections. |
| `docs/architecture.md` | Was updated in Sprint 28 with the alignment recipe; ASCII diagrams checked. Missing the alerts surface (Sprint 28 D2) and the MQTT TLS listener (Sprint 28 C6). | **H4** adds both surfaces. |

## Inventory

See `git log -1` history for each file. Rather than freezing a large table into
the audit (which itself ages out), the audit doc commits only the **flag**
sections above. The full inventory can be re-generated via:

```bash
python3 - <<'PYEOF'
import subprocess, re, pathlib
from collections import defaultdict
repo = pathlib.Path('.')
files = sorted(
    p for p in repo.rglob('*.md')
    if not any(seg in str(p).split('/') for seg in
               ['node_modules', '.venv', 'tagpulse.egg-info',
                'tagpulse_edge.egg-info', '.pytest_cache'])
    and not str(p).startswith('clients/pi/')
)
def gl(p, fmt):
    return subprocess.run(
        ['git', 'log', '-1', f'--format={fmt}', '--', str(p)],
        capture_output=True, text=True,
    ).stdout.strip()
inbound = defaultdict(int)
text = {p: p.read_text(errors='ignore') for p in files}
for p in files:
    rel = str(p); name = p.name
    for q, t in text.items():
        if q == p: continue
        if rel in t or '/' + name in t or '(' + name in t:
            inbound[rel] += 1
print('| File | Last commit | Inbound | Subject |')
print('|---|---|---|---|')
for p in files:
    rel = str(p)
    date = gl(p, '%ai')[:10]
    subj = gl(p, '%s')[:60]
    print(f'| `{rel}` | {date} | {inbound[rel]} | {subj} |')
PYEOF
```

---

## H2 / H3 / H4 / H5 cross-references

- **[H2 — runbook index](../runbooks/README.md):** the 14-line stub is
  replaced with a categorized table indexing every file under
  `docs/runbooks/`. Pulls in the "stale" callouts above as TODO footnotes
  (device-token-rotation, geofence-postgis-trigger).
- **[H3 — operator quickstart](../operator-quickstart.md):** new one-page
  doc. Pulls the "common operator tasks" list from `Makefile` (Sprint 28 F1
  targets) and points each at the relevant runbook.
- **H4 — README + architecture refresh:** edits land per the
  "contradictory" table above. Architecture additions: alerts module +
  port-8883 listener.
- **H5 — markdownlint + lychee CI:** see `.github/workflows/docs-lint.yml`
  (added this sprint). Allowlist includes `localhost`, `example.com`, and
  `https://example.org/...`. Configured to fail PR-only (post a summary
  comment on the PR, not block main).

---

## Re-audit cadence

Run this audit at the end of every sprint that ships a runbook or design doc.
Drop the previous sprint's `docs-audit-sprint{N-1}.md` after the new one
lands — these are point-in-time artifacts, not living documents.
