# Sprint 70 — Uniform table filter & search (wildcard column box)

- Status: **In progress** (2026-06-20). Backend [#137](https://github.com/9owlsboston/TagPulse/pull/137) + UI [#106](https://github.com/9owlsboston/TagPulse-UI/pull/106).
- Owner: backend contract + UI consumer, cross-repo.
- Related: [ADR-030 (list-page column filters)](../adr/030-list-page-column-filters.md),
  [ADR-032 (configurable UI)](../adr/032-configurable-ui.md),
  `src/components/ListPageShell.tsx` (UI), `docs/roadmap.md` (Sprint 70).

## 1. Goal

One consistent **wildcard text filter** on every list/table: an operator types
`reader-*` (or `*-DC`, or `palLet`) into a column-header box and the table
filters by that column — uniformly, on every page, whether the table is fully
loaded client-side or server-paginated.

This is the **free-text/wildcard** companion to ADR-030's **enum checkbox**
filters. ADR-030 governs low-cardinality enumerable columns (status, category,
connection state) via AntD `filters` + `filterSearch`; Sprint 70 governs
**high-cardinality / identifier / name** columns (EPC, reader name, asset name,
tag id) via a per-column wildcard box. The two coexist: a page may have enum
checkbox filters on some columns and wildcard boxes on others.

Non-goals (v1): raw regular expressions (ReDoS/footgun risk), fuzzy/ranked
search, cross-column "global" search, saved filters.

## 2. Wildcard grammar (v1) — the contract

The same grammar is implemented **twice** (backend SQL + frontend JS) and MUST
stay byte-for-byte equivalent in behavior. Both implementations are pure
functions with a shared test vector table (§7).

### 2.1 Metacharacters

| Token | Meaning |
|---|---|
| `*` | match **zero or more** of any character |
| `?` | match **exactly one** of any character |
| `\*` | a literal asterisk |
| `\?` | a literal question mark |
| `\\` | a literal backslash |

Any other character (including `%`, `_`, `.`, `[`, regex metacharacters) is a
**literal** — it matches only itself. This is the whole point of "wildcard, not
regex": the user's input can never be interpreted as a regex or a raw SQL
`LIKE` pattern.

### 2.2 Matching mode — substring by default, anchored when wildcards are present

This is the one nuanced rule, chosen to be **intuitive** *and* **backward
compatible** with the existing `GET /assets?q=` substring search:

- **No `*`/`?` in the pattern** → **substring contains.** `reader` matches
  `My Reader 03` and `reader-03`. (Identical to today's `/assets?q=` behavior,
  and what an operator expects when they type a bare word into a search box.)
- **Pattern contains `*` or `?`** → **anchored glob (whole cell).** `reader-*`
  matches `reader-03` but **not** `my-reader-03`; `*-DC` matches `BOS-DC`;
  `r?ader` matches `reader` and `rxader`. Anchoring is what makes `*` meaningful
  — without it `reader-*` and `reader` would behave identically.

> **Why hybrid, not pure-anchored:** pure-anchored would force operators to type
> `*reader*` for the common "find anything containing reader" case and would
> silently change the existing `/assets?q=foo` substring contract to anchored
> (a behavior break). The hybrid keeps bare terms substring (compatible,
> intuitive) and gives `*`/`?` their natural anchored-glob meaning. The
> confirmed "anchored" decision applies precisely to **wildcard** input, which
> this delivers.

### 2.3 Case sensitivity & whitespace

- **Case-insensitive** always (backend `ILIKE`; frontend `RegExp` `i` flag).
- Leading/trailing whitespace in the pattern is **trimmed**; an empty/whitespace
  pattern means "no filter" (the param is omitted / the column is unfiltered).

### 2.4 Multi-column semantics

When more than one column has an active wildcard filter, results must match
**all** of them (logical **AND**), consistent with how the existing
`status`/`category`/`labels[…]` filters already combine.

### 2.5 Worked examples

| Pattern | Mode | Matches | Does **not** match |
|---|---|---|---|
| `reader` | substring | `reader-03`, `My Reader` | `read` |
| `reader-*` | anchored | `reader-03`, `reader-` | `my-reader-03` |
| `*-dc` | anchored | `BOS-DC`, `-dc` | `dc-bos` |
| `r?ader` | anchored | `reader`, `rxader` | `reaader`, `rader` |
| `50\%` | substring | `discount 50% off` | `50 percent` |
| `a\*b` | substring | `a*b literal` | `axb` |

## 3. Backend contract

### 3.1 Endpoints (v1 — locked with user)

A new **optional** query param is added to the four **paginated** list
endpoints. Fully-loaded lists (Devices, Categories, Sites/Zones) filter
client-side and get **no** backend change.

| Endpoint | Route | Search column(s) | Param |
|---|---|---|---|
| Tag Reads | `GET /tag-reads` | `tag_id` (EPC) | `tag_q` |
| Alert History | `GET /alerts` | message/rule-name (TBD per schema) | `q` |
| Tags | `GET /tags` | `epc_hex` | `q` |
| Assets | `GET /assets` | `name`, `external_ref` (existing `q`) | `q` (extended) |

**Naming.** Single-column endpoints use a column-scoped name (`tag_q`) so the
contract says *which* column it searches; multi-column endpoints keep the
generic `q` (assets already does). All params are **optional, additive,
default `None`** → every existing caller is unaffected. Open item O1 (§8) tracks
whether Tag Reads also wants a `device` name search.

### 3.2 `/assets?q=` — extend, don't break

`GET /assets?q=` already does a substring `ILIKE '%' || q || '%'` across
`name`/`external_ref`. Under the §2.2 hybrid this is the *bare-term* path, so
existing behavior is preserved. Two refinements ship with it:

1. Route `q` through the shared `wildcard_to_ilike()` helper (§3.3) so a `*`/`?`
   in `q` becomes an anchored glob instead of being treated as a literal — a
   superset of today's behavior.
2. **Escape `%`/`_`** in the user input before building the `LIKE` pattern.
   Today `q="50%"` injects a wildcard into the `LIKE` (a latent
   wrong-results bug, not a SQL-injection — the value is still a bound
   parameter). The shared helper escapes them.

### 3.3 Shared helper — `wildcard_to_ilike(pattern) -> str | None`

One pure function (`src/tagpulse/ingestion`… no; `src/tagpulse/api/filters.py`,
new module) compiles a user pattern to a SQL `LIKE` pattern string and is used
by every endpoint. Pseudocode:

```
def wildcard_to_ilike(pattern: str) -> str | None:
    p = pattern.strip()
    if not p:
        return None                      # no filter
    has_wildcard = <unescaped * or ? present>
    out, i = [], 0
    while i < len(p):
        c = p[i]
        if c == '\\' and i+1 < len(p) and p[i+1] in '*?\\':
            out.append(_like_escape(p[i+1])); i += 2; continue
        if c == '*': out.append('%')
        elif c == '?': out.append('_')
        else: out.append(_like_escape(c))   # escapes %, _, and the ESCAPE char
        i += 1
    body = ''.join(out)
    return body if has_wildcard else f'%{body}%'   # anchored vs substring
```

- `_like_escape` escapes `%`, `_`, and the chosen `ESCAPE` char (`\`).
- Callers use `column.ilike(compiled, escape='\\')`.
- Returns `None` for empty → caller skips the predicate.

### 3.4 Where the predicate goes

Threaded route → service → repository as an optional kwarg, AND-combined with
existing `WHERE` predicates, exactly like the `status`/`category_id` filters.
No new indexes in v1 (these are bounded, tenant-scoped, paginated queries);
note in §8 that a trailing-wildcard or leading-anchored pattern can use a btree
index but a leading `%` cannot — acceptable at current data volumes, revisit
with `pg_trgm` if a tenant's table grows past the point where seq-scan latency
shows up in the p95 SLO.

### 3.5 OpenAPI

New optional params → regenerate `openapi.json` in #137. Backend merges first;
UI regenerates its client against it.

## 4. UI contract

### 4.1 `matchWildcard(value, pattern) -> boolean`

Pure util in `src/lib/` mirroring §2 exactly: trims, decides substring vs
anchored by wildcard presence, escapes regex metacharacters in literals,
compiles `*`→`.*` / `?`→`.` , anchors with `^…$` only in glob mode, `i` flag.
Used by **client-side** tables.

### 4.2 `ColumnSearchFilter`

A reusable AntD `filterDropdown` (text input + Search/Reset) factory next to
`ListPageShell`/`ColumnChooser`. Returns the `{ filterDropdown, filterIcon,
onFilter }` column props.

- **Client-side tables** (Devices, Categories, Sites/Zones): `onFilter` calls
  `matchWildcard(rowValue, pattern)`.
- **Server-paginated tables** (Tag Reads, Alert History, Tags, Assets): the
  dropdown's confirm pushes the pattern to the page's query state → the API
  param (§3.1). **No client `onFilter`** — per ADR-030 hard-rule #2, client
  filtering a paginated table only filters the loaded page (a correctness bug).

### 4.3 Relationship to ADR-030

ADR-030's `makeEnumFilterColumn` (checkbox + search) and Sprint 70's
`ColumnSearchFilter` (free wildcard box) are siblings under the same
`ListPageShell` filter toolkit. Doc both together; a column picks exactly one.

## 5. Client vs server matrix

| Table | Source | Mechanism |
|---|---|---|
| Devices, Categories, Sites/Zones | fully loaded | client `matchWildcard` |
| Tag Reads, Alert History, Tags, Assets | paginated | server param (§3.1) |

## 6. Security

- **No raw regex** crosses the boundary in either direction — user input is only
  ever a wildcard pattern compiled to `LIKE` (backend) or an
  **escaped-then-compiled** `RegExp` (frontend). ReDoS-safe: the only regex
  constructs produced are `.*` / `.`, which are linear.
- Backend values stay **bound parameters**; the helper only shapes the `LIKE`
  pattern + `ESCAPE`, never string-interpolates into SQL.
- `%`/`_` escaping prevents user input from injecting SQL wildcards.

## 7. Test plan

- **Shared vector table** (the §2.5 examples + edge cases: empty, whitespace,
  only-`*`, escaped metachars, unicode, `%`/`_` literals) asserted against
  **both** `wildcard_to_ilike` (backend, compile + live ILIKE behavior) and
  `matchWildcard` (frontend) so the two can't drift.
- Backend: per-endpoint route tests that the param filters + AND-combines +
  defaults to no-op; assert the compiled `LIKE` + `escape` shape; an assets
  regression that bare `q` still substring-matches and `%` is now escaped.
- UI: `matchWildcard` unit tests (vector table) + `ColumnSearchFilter` render
  test (dropdown, confirm pushes pattern, reset clears) + one client-side and
  one server-side wiring test.

## 8. Open items

- **O1.** Tag Reads: search `tag_id` only, or also a `device` name search?
  (Leaning `tag_id` only for v1; device is already a dropdown filter.)
- **O2.** Alert History search column — confirm against the `alerts` schema
  (message text vs rule name); may need the rule join.
- **O3.** `pg_trgm` index — deferred; revisit if a tenant's paginated table
  trips the p95 latency SLO on leading-`%` patterns.

## 9. Rollout

1. Backend `api/filters.py` helper + tests (#137).
2. Add params to the 4 endpoints + `openapi.json` regen + CHANGELOG (#137).
3. Merge #137. UI regenerates client.
4. UI `matchWildcard` + `ColumnSearchFilter` + tests (#106).
5. Wire client-side (Devices/Categories/Sites-Zones) + server-side (the 4) +
   CHANGELOG (#106).
6. Flip roadmap Sprint 70 → shipped via `ship-sprint.sh --with-ui`.
