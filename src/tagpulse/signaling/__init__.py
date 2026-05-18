"""Signaling events subsystem (Sprint 41 / ADR-021 v2).

Houses the workers and processors that evaluate configurable signaling
event rules (``signaling.<event_type>.<trigger>``). The package is a
sibling to :mod:`tagpulse.rules`; the rules engine still owns CRUD and
alert creation, while modules here own the *evaluation* of the various
signaling triggers.

Phase B ships the periodic dispatcher shell only; processor
implementations (IsolatedZones, OverlappingZones, temperature-delta,
etc.) land in Phase D.
"""
