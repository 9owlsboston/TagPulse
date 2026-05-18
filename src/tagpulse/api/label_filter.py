"""Deep-object ``?labels[key]=v1,v2`` parser + SQL applicator (Sprint 35 Phase C).

Implements the filter encoding from
[ADR 020 §"Filter encoding"](../../docs/adr/020-labels-first-class.md) for
``GET /assets``, ``GET /sites``, ``GET /zones`` and ``GET /devices``.

Semantics:

- AND across distinct keys (each becomes a correlated ``EXISTS`` subquery).
- OR within values of the same key (``value IN (...)``).
- Comma-separated values inside one ``labels[key]=`` are split before
  validation; empty segments are an error.

Guard rails (return 400 if exceeded):

- ≤ 5 distinct keys per request.
- ≤ 20 values per key.
- Key matches ``^[A-Za-z0-9_.+$]{3,24}$`` (mirrors the catalog regex).
- Value matches ``^[A-Za-z0-9._-]{1,64}$`` (mirrors the association regex).

The key match is case-insensitive — the labels catalog stores
``(tenant_id, entity_type, lower(key))`` as a functional unique index
(migration 039), so ``lower(l.key) = lower(:key)`` uses that index.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.sql import Select

from tagpulse.models.database import EntityLabelModel, LabelModel
from tagpulse.models.schemas import _LABEL_KEY_PATTERN, _LABEL_VALUE_PATTERN

if TYPE_CHECKING:
    from sqlalchemy.orm import InstrumentedAttribute
    from starlette.datastructures import QueryParams

# --- Guard-rail constants ---------------------------------------------------

#: Max distinct keys allowed in one request.
LABEL_FILTER_MAX_KEYS = 5

#: Max values per key allowed in one request.
LABEL_FILTER_MAX_VALUES_PER_KEY = 20

# Compiled patterns reused per request.
_KEY_REGEX = re.compile(_LABEL_KEY_PATTERN)
_VALUE_REGEX = re.compile(_LABEL_VALUE_PATTERN)
_LABELS_PARAM_REGEX = re.compile(r"^labels\[([^\]]*)\]$")


class LabelFilterError(ValueError):
    """Raised when ``?labels[...]=`` input violates the guard rails."""


def parse_label_filter(query_params: QueryParams) -> dict[str, list[str]] | None:
    """Extract a ``{key: [value, ...]}`` map from ``labels[key]=`` params.

    Returns ``None`` if no ``labels[...]`` keys are present in the query
    string. Raises :class:`LabelFilterError` on any guard-rail violation.

    Duplicate ``labels[key]=`` entries merge their value lists, then
    de-duplicate. Order across keys/values is not significant for the
    SQL we generate (``IN (...)`` clauses).
    """
    filters: dict[str, list[str]] = {}
    seen_values: dict[str, set[str]] = {}

    for raw_key, raw_value in query_params.multi_items():
        match = _LABELS_PARAM_REGEX.match(raw_key)
        if match is None:
            continue
        inner = match.group(1)
        if not _KEY_REGEX.match(inner):
            raise LabelFilterError(f"Invalid label key '{inner}' — must match {_LABEL_KEY_PATTERN}")
        key_lower = inner.lower()
        values_bucket = filters.setdefault(key_lower, [])
        seen_bucket = seen_values.setdefault(key_lower, set())

        # Comma-split. Empty input or trailing comma yields an empty segment,
        # which we reject so users don't silently match nothing.
        parts = raw_value.split(",") if raw_value != "" else [""]
        for part in parts:
            if part == "":
                raise LabelFilterError(
                    f"Empty value in labels[{inner}]= — pass at least one "
                    "non-empty value, comma-separated"
                )
            if not _VALUE_REGEX.match(part):
                raise LabelFilterError(
                    f"Invalid label value '{part}' for key '{inner}' — must "
                    f"match {_LABEL_VALUE_PATTERN}"
                )
            if part not in seen_bucket:
                seen_bucket.add(part)
                values_bucket.append(part)

    if not filters:
        return None

    if len(filters) > LABEL_FILTER_MAX_KEYS:
        raise LabelFilterError(
            f"Too many label keys ({len(filters)}); max is {LABEL_FILTER_MAX_KEYS} per request"
        )
    for key, values in filters.items():
        if len(values) > LABEL_FILTER_MAX_VALUES_PER_KEY:
            raise LabelFilterError(
                f"Too many values for labels[{key}] ({len(values)}); "
                f"max is {LABEL_FILTER_MAX_VALUES_PER_KEY} per key"
            )

    return filters


def apply_label_filter(
    stmt: Select[Any],
    *,
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id_col: InstrumentedAttribute[uuid.UUID],
    labels: dict[str, list[str]] | None,
) -> Select[Any]:
    """Append one correlated ``EXISTS`` per key to ``stmt``.

    ``entity_id_col`` is the ORM column of the parent entity's primary
    key (e.g. ``AssetModel.id``). ``entity_type`` is the singular form
    stored in ``labels.entity_type`` (``"asset"``, ``"site"``,
    ``"zone"``, ``"device"``).

    Returns ``stmt`` unchanged when ``labels`` is ``None`` or empty.
    """
    if not labels:
        return stmt
    for key, values in labels.items():
        sub = (
            select(1)
            .select_from(EntityLabelModel)
            .join(LabelModel, LabelModel.id == EntityLabelModel.label_id)
            .where(
                EntityLabelModel.entity_id == entity_id_col,
                LabelModel.tenant_id == tenant_id,
                LabelModel.entity_type == entity_type,
                func.lower(LabelModel.key) == key.lower(),
                EntityLabelModel.value.in_(values),
            )
        )
        stmt = stmt.where(sub.exists())
    return stmt
