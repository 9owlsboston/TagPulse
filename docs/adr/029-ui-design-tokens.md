# ADR-029: UI design tokens — semantic layer, dual theme, AntD ConfigProvider integration

- Status: **Accepted (Sprint 54, May 2026)**
- Scope: `TagPulse-UI` repo. This ADR is recorded in the backend repo because the backend owns the roadmap and ADR series; the implementation lives in `TagPulse-UI` and is referenced from the [Sprint 54 design doc](../design/sprint-54-ui-overhaul.md).
- Related: Sprint 54 design doc (this sprint). No prior UI-theming ADR — this is the first.

## Context

The UI today has two theming weaknesses that get worse with every new page:

1. **Per-component overrides.** Each page that needed a dark-mode tweak added its own `style={{ … }}` or a one-off CSS rule. Light theme has accumulated overrides that selectively neutralise dark-theme rules. The pattern is "ship feature, retrofit theme" — exactly the trap noted in the Sprint 54 design doc's Risk #3 row.
2. **No semantic layer.** AntD's `ConfigProvider` is configured with raw colour values inline. There's no name for "the colour we use for danger surfaces" — only `#ff5252` repeated in eight files. Renaming the danger colour is a global find-and-replace, and renaming it differently in light vs dark is a per-call-site edit.

Sprint 54 introduces a Dashboard with KPI tiles that intentionally use semantic colour (an alerts tile is "danger" if any open alerts exist; a devices tile is "warning" if any reader is offline). Doing that without a semantic layer means hardcoding the same eight hex values into the new tile component too, and locking us into the retrofit pattern for one more page.

## Decision

Introduce a two-layer design-token catalog in `TagPulse-UI`:

- **Primitive layer** — palette only. Raw colours, raw spacing units, raw font sizes. Not consumed directly by components.
- **Semantic layer** — named roles that components consume. Each role resolves to a primitive token *per theme*.

Both layers are declared as CSS custom properties on `:root`, with the semantic layer overridden under `[data-theme="light"]`. `ThemeProvider` flips the `data-theme` attribute on `<html>`. AntD's `ConfigProvider` is configured to read from the semantic layer via JS bindings (mirror, not duplicate).

### Primitive layer (palette, both themes share names)

```
--palette-bg-dark:        #0F0F0F
--palette-surface-dark:   #1A1A1A
--palette-accent-dark:    #4CC2FF
--palette-success-dark:   #3DDC84
--palette-warning-dark:   #FFB74D
--palette-danger-dark:    #FF5252

--palette-bg-light:       #F5F5F5
--palette-surface-light:  #FFFFFF
--palette-accent-light:   #0078D4
--palette-success-light:  #107C10
--palette-warning-light:  #D88C1A
--palette-danger-light:   #C42B1C
```

(Plus AntD's neutral text-on-surface scale, untouched.)

### Semantic layer (what components consume)

```
--color-bg            → page background
--color-surface       → card / panel surface
--color-surface-raised → modal / drawer / popover surface (one notch above surface)
--color-accent        → primary action, active nav highlight, focus ring
--color-success       → "healthy" KPI state, success toast
--color-warning       → "degraded" KPI state, warning toast
--color-danger        → "alert" KPI state, error toast, destructive button
--color-text          → primary text on surface
--color-text-muted    → secondary text on surface
--color-border        → 1px dividers
```

Resolution under `[data-theme="dark"]` (the default):

```
--color-bg:            var(--palette-bg-dark);
--color-surface:       var(--palette-surface-dark);
--color-accent:        var(--palette-accent-dark);
…
```

Resolution under `[data-theme="light"]`:

```
--color-bg:            var(--palette-bg-light);
--color-surface:       var(--palette-surface-light);
--color-accent:        var(--palette-accent-light);
…
```

### Hard rules

1. **Components and pages consume the semantic layer only.** Zero references to `--palette-*` outside the token file. Zero hardcoded hex anywhere under `src/components/` and `src/pages/`.
2. **Zero `!important` in committed CSS.** If a rule needs `!important` to take effect, the cascade is wrong — fix the cascade, not the rule.
3. **AntD `ConfigProvider` is a mirror, not a duplicate.** The JS theme object reads the same semantic names. When a semantic value changes, the change propagates to both CSS and AntD components from one source.
4. **`ThemeProvider` is the only toggler.** No component reads or sets `data-theme` directly; they consume the semantic layer and trust the theme is set above them.
5. **A `/dev/tokens` debug page** renders every semantic token as a swatch in both themes side-by-side. Required artifact for Phase 54.1 pass bar.

## Consequences

### Positive

- Renaming or recolouring a semantic role is a one-file edit.
- New components have no excuse to hardcode hex — the lint rule (a simple regex grep in CI) enforces it.
- AntD upgrades are decoupled from theming because the bridge layer is small and named.
- Dark/light parity is structural: a missing override in light is a CSS error, not a per-page bug.

### Negative / costs

- Phase 54.1 is upfront token work that produces no user-visible feature on its own. Phases 54.2–54.5 depend on it being right.
- Existing pages need to be swept for hardcoded hex during their phase (54.4 Dashboard, 54.5 list pages). The 14 admin pages out-of-scope for Sprint 54+55 will continue to carry hardcoded values until Sprint 56 converts them.
- A small amount of theming-on-the-wire happens via CSS custom properties, which means SSR-style theme-flash-on-load needs a small inline script in `index.html` to set `data-theme` before stylesheet parse. Acceptable; standard pattern.

### Out of scope for this ADR

- Spacing / radius / shadow / font tokens (will use the same two-layer pattern but are not enumerated here; see Phase 54.1 implementation).
- A third theme beyond light + dark.
- User-customisable accent colour.

## Decision history

- **v1.0 (Sprint 54, May 2026)** — Initial ADR. Two-layer catalog, dual theme via `[data-theme]`, AntD `ConfigProvider` mirror, four hard rules, `/dev/tokens` debug page as the Phase 54.1 acceptance artifact.
