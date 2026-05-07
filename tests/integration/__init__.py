"""Integration tests — exercise real Postgres / TimescaleDB.

Skipped unless ``TAGPULSE_INTEGRATION_DB_URL`` is set. CI sets it via
``make migration-check`` against the docker-compose ``timescaledb``
service. Local devs can run these against an ephemeral container of
their choice — the harness assumes an empty database and owns the
schema for the duration of the test.
"""
