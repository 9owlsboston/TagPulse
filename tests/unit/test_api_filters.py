"""Unit tests for the Sprint 70 wildcard → ILIKE compiler (``api/filters.py``).

The ``VECTORS`` table is the **canonical shared contract** mirrored by the UI's
``matchWildcard`` (``src/lib/wildcard.test.ts``) — keep the two in sync. Each row
is ``(user_input, expected_ilike)`` where ``expected_ilike`` is the SQL ``LIKE``
pattern (used with ``ESCAPE '\\'``). ``None`` means "no predicate".
"""

from __future__ import annotations

import pytest

from tagpulse.api.filters import LIKE_ESCAPE, wildcard_to_ilike

# (input, expected compiled ILIKE pattern)
VECTORS: list[tuple[str | None, str | None]] = [
    # --- no-op / empty ---
    (None, None),
    ("", None),
    ("   ", None),
    # --- bare term → substring (back-compat with /assets?q=) ---
    ("reader", "%reader%"),
    ("  reader  ", "%reader%"),  # trimmed
    ("Reader-03", "%Reader-03%"),  # case preserved in pattern; ILIKE handles fold
    # --- wildcard present → anchored (whole value) ---
    ("reader-*", "reader-%"),
    ("*-dc", "%-dc"),
    ("*", "%"),
    ("r?ader", "r_ader"),
    ("a*b?c", "a%b_c"),
    # --- escaped metacharacters → literal (so NOT anchored) ---
    (r"\*", "%*%"),  # literal asterisk, substring
    (r"\?", "%?%"),  # literal question mark, substring
    (r"a\*b", "%a*b%"),  # "a*b literal", substring
    (r"a\\b", "%a" + LIKE_ESCAPE + "\\b%"),  # literal backslash, escaped for LIKE
    # --- SQL LIKE metacharacters in input are escaped, never wildcards ---
    ("50%", "%50" + LIKE_ESCAPE + "%%"),  # literal % → escaped
    ("a_b", "%a" + LIKE_ESCAPE + "_b%"),  # literal _ → escaped
    ("100%*", "100" + LIKE_ESCAPE + "%%"),  # % escaped, * → % (anchored via *)
]


@pytest.mark.parametrize(("raw", "expected"), VECTORS)
def test_wildcard_to_ilike(raw: str | None, expected: str | None) -> None:
    assert wildcard_to_ilike(raw) == expected


def test_substring_vs_anchored_switch() -> None:
    # Bare term is wrapped in %...%; the moment a wildcard appears it is not.
    assert wildcard_to_ilike("dc").startswith("%") and wildcard_to_ilike("dc").endswith("%")
    assert wildcard_to_ilike("dc*") == "dc%"  # anchored, no leading %


def test_escape_char_is_backslash() -> None:
    assert LIKE_ESCAPE == "\\"
