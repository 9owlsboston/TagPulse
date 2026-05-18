"""Unit tests for Sprint 34 gap 2.7 site schema validators.

Covers ``SiteCreate`` / ``SiteUpdate`` validation:

- ``kind`` enum (site | transporter)
- ``latitude`` / ``longitude`` range + paired invariant
- ``country`` ISO 3166-1 alpha-2 normalisation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tagpulse.models.schemas import SiteCreate, SiteUpdate

# ---------------------------------------------------------------------------
# SiteCreate
# ---------------------------------------------------------------------------


class TestSiteCreateKind:
    def test_defaults_to_site(self) -> None:
        body = SiteCreate(name="HQ")
        assert body.kind == "site"

    def test_accepts_transporter(self) -> None:
        body = SiteCreate(name="Truck 17", kind="transporter")
        assert body.kind == "transporter"

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValidationError):
            SiteCreate(name="X", kind="warehouse")  # type: ignore[arg-type]


class TestSiteCreateLatLon:
    def test_neither_provided_ok(self) -> None:
        body = SiteCreate(name="HQ")
        assert body.latitude is None
        assert body.longitude is None

    def test_both_provided_ok(self) -> None:
        body = SiteCreate(name="HQ", latitude=40.7128, longitude=-74.0060)
        assert body.latitude == pytest.approx(40.7128)
        assert body.longitude == pytest.approx(-74.0060)

    def test_only_latitude_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SiteCreate(name="HQ", latitude=40.0)

    def test_only_longitude_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SiteCreate(name="HQ", longitude=-74.0)

    @pytest.mark.parametrize("bad_lat", [-90.1, 90.1, 1000.0])
    def test_latitude_out_of_range(self, bad_lat: float) -> None:
        with pytest.raises(ValidationError):
            SiteCreate(name="HQ", latitude=bad_lat, longitude=0.0)

    @pytest.mark.parametrize("bad_lon", [-180.1, 180.1, 1000.0])
    def test_longitude_out_of_range(self, bad_lon: float) -> None:
        with pytest.raises(ValidationError):
            SiteCreate(name="HQ", latitude=0.0, longitude=bad_lon)


class TestSiteCreateCountry:
    def test_uppercase_alpha2(self) -> None:
        body = SiteCreate(name="HQ", country="US")
        assert body.country == "US"

    def test_lowercase_normalised(self) -> None:
        body = SiteCreate(name="HQ", country="us")
        assert body.country == "US"

    def test_with_whitespace(self) -> None:
        body = SiteCreate(name="HQ", country=" gb ")
        assert body.country == "GB"

    @pytest.mark.parametrize("bad", ["USA", "U", "U1", "12"])
    def test_rejects_non_alpha2(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            SiteCreate(name="HQ", country=bad)


class TestSiteCreateStructuredAddress:
    def test_full_payload_roundtrips(self) -> None:
        body = SiteCreate(
            name="HQ",
            kind="site",
            street_line1="1 Infinite Loop",
            street_line2="Suite 100",
            city="Cupertino",
            region="CA",
            postal_code="95014",
            country="US",
            latitude=37.3318,
            longitude=-122.0312,
        )
        assert body.street_line1 == "1 Infinite Loop"
        assert body.street_line2 == "Suite 100"
        assert body.city == "Cupertino"
        assert body.region == "CA"
        assert body.postal_code == "95014"
        assert body.country == "US"


# ---------------------------------------------------------------------------
# SiteUpdate
# ---------------------------------------------------------------------------


class TestSiteUpdateKind:
    def test_kind_mutable(self) -> None:
        patch = SiteUpdate(kind="transporter")
        assert patch.kind == "transporter"

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SiteUpdate(kind="warehouse")  # type: ignore[arg-type]


class TestSiteUpdateLatLon:
    def test_both_in_payload_paired_check(self) -> None:
        with pytest.raises(ValidationError):
            SiteUpdate(latitude=10.0, longitude=None)

    def test_only_one_in_payload_deferred_to_db(self) -> None:
        # Only latitude in the patch payload — the model-level paired check
        # is deliberately skipped; the DB CHECK ck_sites_latlon_paired
        # enforces the invariant. This keeps PATCH semantics ergonomic.
        patch = SiteUpdate(latitude=10.0)
        assert patch.latitude == 10.0
        assert "longitude" not in patch.model_fields_set

    def test_both_set_consistently(self) -> None:
        patch = SiteUpdate(latitude=10.0, longitude=20.0)
        assert patch.latitude == 10.0
        assert patch.longitude == 20.0

    def test_both_cleared_ok(self) -> None:
        patch = SiteUpdate(latitude=None, longitude=None)
        assert patch.latitude is None
        assert patch.longitude is None


class TestSiteUpdateCountry:
    def test_country_normalised(self) -> None:
        patch = SiteUpdate(country="de")
        assert patch.country == "DE"

    def test_country_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SiteUpdate(country="DEU")
