"""Wildcard → SQL ``ILIKE`` compilation for list-page search params (Sprint 70).

A single, shared compiler so every paginated list endpoint that exposes a
free-text wildcard filter (Tag Reads, Alert History, Tags, Assets) behaves
identically. The mirror-image frontend implementation lives in the UI repo
(``src/lib/wildcard.ts`` — ``matchWildcard``); the two are kept in lock-step by
a shared test-vector table.

Grammar (see ``docs/design/sprint-70-table-filter.md`` §2):

* ``*`` matches zero or more characters; ``?`` matches exactly one.
* ``\\*`` / ``\\?`` / ``\\\\`` are the literal ``*`` / ``?`` / ``\\``.
* Every other character is a **literal** (SQL ``%`` / ``_`` are escaped, so
  user input can never inject a raw ``LIKE`` wildcard).
* **No** ``*`` / ``?`` in the pattern → **substring** match (``%term%``).
* A ``*`` / ``?`` is present → **anchored** (whole-value) match.
* Case-insensitivity comes from ``ILIKE`` at the call site.

Use the result with ``column.ilike(compiled, escape="\\\\")``; a ``None`` return
means "no filter" (empty / whitespace input) and the caller should skip the
predicate.
"""

from __future__ import annotations

# The ``ESCAPE`` character handed to SQL ``LIKE``. Backslash is conventional and
# does not collide with any character special to our wildcard grammar's output
# (only ``%`` and ``_`` are SQL-special, and we escape those).
LIKE_ESCAPE = "\\"

_GLOB_WILDCARDS = ("*", "?")
_ESCAPABLE = ("*", "?", "\\")


def _has_unescaped_wildcard(pattern: str) -> bool:
    """True if ``pattern`` contains a ``*`` / ``?`` that is not backslash-escaped."""
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n and pattern[i + 1] in _ESCAPABLE:
            i += 2
            continue
        if c in _GLOB_WILDCARDS:
            return True
        i += 1
    return False


def _escape_like_literal(ch: str) -> str:
    """Escape a single literal char for SQL ``LIKE`` (``%`` / ``_`` / the ESCAPE char)."""
    if ch in ("%", "_", LIKE_ESCAPE):
        return LIKE_ESCAPE + ch
    return ch


def wildcard_to_ilike(pattern: str | None) -> str | None:
    """Compile a user wildcard ``pattern`` to a SQL ``ILIKE`` pattern string.

    Returns ``None`` for ``None`` / empty / whitespace-only input so the caller
    can skip the predicate entirely. See the module docstring for the grammar.
    """
    if pattern is None:
        return None
    p = pattern.strip()
    if not p:
        return None

    anchored = _has_unescaped_wildcard(p)
    out: list[str] = []
    i = 0
    n = len(p)
    while i < n:
        c = p[i]
        if c == "\\" and i + 1 < n and p[i + 1] in _ESCAPABLE:
            # Escaped metacharacter → the literal character.
            out.append(_escape_like_literal(p[i + 1]))
            i += 2
            continue
        if c == "*":
            out.append("%")
        elif c == "?":
            out.append("_")
        else:
            out.append(_escape_like_literal(c))
        i += 1

    body = "".join(out)
    # Bare term → substring; wildcard present → anchored (whole value).
    return body if anchored else f"%{body}%"


__all__ = ["LIKE_ESCAPE", "wildcard_to_ilike"]
