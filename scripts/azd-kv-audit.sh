#!/usr/bin/env bash
# scripts/azd-kv-audit.sh <env>
#
# Sprint 28 B1 — operator-side KV inventory.
# Lists every secret in the env's vault with name, enabled, created, updated,
# expires, contentType, and the principals (objectId + RBAC role) with access.
# Flags:
#   - secrets without an expiry
#   - secrets older than 180 days
#   - principals with 'Officer' that may only need 'User'
#
# Pure az-CLI; runs from operator laptop with 'az login' as the operator's
# Entra principal. Read-only.

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/azd-common.sh"

ENV_NAME="${1:-}"
[[ -z "$ENV_NAME" ]] && die "Usage: $0 <env>" 2

azd_env_resolve "$ENV_NAME"
[[ -z "$KV_NAME" ]] && die "azd env '$RESOLVED_AZD_ENV' has no Key Vault name" 2
log "auditing KV $KV_NAME (rg=$RG)"

NOW_S=$(date -u +%s)
THRESHOLD_180=$((NOW_S - 180*86400))
WARN=0

echo
echo "==> Secrets in $KV_NAME"
printf '%-44s %-8s %-12s %-12s %-30s\n' "name" "enabled" "created" "updated" "expires/flags"
printf '%-44s %-8s %-12s %-12s %-30s\n' "----" "-------" "-------" "-------" "-------------"

az keyvault secret list --vault-name "$KV_NAME" --query \
  '[].{n:name,e:attributes.enabled,c:attributes.created,u:attributes.updated,x:attributes.expires}' \
  -o json 2>/dev/null \
| python3 -c "
import json, sys, datetime
items = json.load(sys.stdin)
now = $NOW_S
threshold_180 = $THRESHOLD_180
warn = 0
def to_ts(s):
    if not s: return None
    try: return int(datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
    except Exception: return None
for it in sorted(items, key=lambda x: x['n']):
    flags = []
    expires = it.get('x') or ''
    if not expires:
        flags.append('NO-EXPIRY')
        warn += 1
    created_ts = to_ts(it.get('c'))
    if created_ts and created_ts < threshold_180:
        age_days = (now - created_ts) // 86400
        flags.append(f'OLD({age_days}d)')
        warn += 1
    fmt_date = lambda v: (v[:10] if v else '-')
    print(f\"{it['n']:<44} {str(it.get('e','?')):<8} {fmt_date(it.get('c')):<12} {fmt_date(it.get('u')):<12} {fmt_date(expires) if expires else '-':<12} {' '.join(flags)}\")
sys.exit(0 if warn == 0 else 11)
" 2>/dev/null
PY_RC=$?
if [[ "$PY_RC" == 11 ]]; then WARN=1; fi

echo
echo "==> RBAC role assignments on $KV_NAME"
KV_ID=$(az keyvault show --name "$KV_NAME" --query id -o tsv 2>/dev/null)
if [[ -n "$KV_ID" ]]; then
  printf '%-40s %-30s %-40s\n' "role" "principalType" "principalName"
  printf '%-40s %-30s %-40s\n' "----" "-------------" "-------------"
  az role assignment list --scope "$KV_ID" --include-inherited \
    --query '[].{role:roleDefinitionName,type:principalType,name:principalName}' -o json 2>/dev/null \
  | python3 -c "
import json, sys
items = json.load(sys.stdin)
warn = 0
for it in sorted(items, key=lambda x: (x.get('role',''), x.get('name',''))):
    flag = ''
    if 'Officer' in (it.get('role') or ''):
        flag = '   ← consider downgrading to *User*'
        warn += 1
    print(f\"{it.get('role',''):<40} {it.get('type',''):<30} {it.get('name','-'):<40}{flag}\")
sys.exit(0 if warn == 0 else 12)
" 2>/dev/null
  if [[ $? == 12 ]]; then WARN=1; fi
else
  log "warning: could not resolve KV resource ID — RBAC listing skipped"
  WARN=1
fi

echo
if [[ "$WARN" == 0 ]]; then
  echo "==> No issues found."
else
  echo "==> Audit complete with warnings (see flags above)."
fi
exit "$WARN"
