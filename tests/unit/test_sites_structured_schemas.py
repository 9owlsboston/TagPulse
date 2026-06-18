"""Unit tests for Sprint 34 gap 2.7 site schema validators.

Covers ``SiteCreate`` / ``SiteUpdate`` validation:

- ``kind`` enum (site | transporter)
- ``latitude`` / ``longitude`` range + paired invariant
- ``country`` ISO 3166-1 alpha-2 normalisation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tagpulse.models.schemas import CoordSystem, SiteCreate, SiteUpdate

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


class TestSiteUpdateRejectsExplicitNullForNotNullFields:
    """Patch payloads must not send explicit ``null`` for NOT-NULL DB columns.

    ``Optional`` on ``SiteUpdate`` means "omit to leave unchanged", not
    "send null to clear". The DB has ``NOT NULL`` on ``name`` / ``kind``
    / ``default_timezone``; rejecting at the schema layer keeps the
    error a 422 instead of leaking as a 500.
    """

    @pytest.mark.parametrize("field", ["name", "kind", "default_timezone"])
    def test_explicit_null_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            SiteUpdate(**{field: None})

    def test_omission_still_allowed(self) -> None:
        # No fields set at all — empty patch is fine.
        patch = SiteUpdate()
        assert "name" not in patch.model_fields_set
        assert "kind" not in patch.model_fields_set
        assert "default_timezone" not in patch.model_fields_set

    def test_nullable_fields_still_clearable(self) -> None:
        # ``address`` and structured-address columns are nullable in the DB;
        # explicit null must still be allowed to clear them.
        patch = SiteUpdate(address=None, city=None, country=None)
        assert "address" in patch.model_fields_set
        assert patch.address is None
        assert patch.city is None
        assert patch.country is None


# ---------------------------------------------------------------------------
# CoordSystem (Sprint 64 / ADR-024)
# ---------------------------------------------------------------------------


class TestCoordSystem:
    def test_minimal_valid(self) -> None:
        cs = CoordSystem(extent_x=400, extent_y=600)
        assert cs.units == "meters"
        assert cs.origin_anchor == "nw_corner"
        assert cs.rotation_deg == 0.0
        assert cs.geo_anchor is None

    def test_extent_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            CoordSystem(extent_x=0, extent_y=600)
        with pytest.raises(ValidationError):
            CoordSystem(extent_x=400, extent_y=-1)

    def test_unknown_key_rejected(self) -> None:
        # extra="forbid" → a typo'd key is a 422, not silently dropped.
        with pytest.raises(ValidationError):
            CoordSystem(extent_x=400, extent_y=600, extentZ=1)  # type: ignore[call-arg]

    def test_device_origin_requires_device_id(self) -> None:
        with pytest.raises(ValidationError):
            CoordSystem(extent_x=400, extent_y=600, origin_anchor="device_id")

    def test_device_id_only_valid_for_device_origin(self) -> None:
        from uuid import uuid4

        with pytest.raises(ValidationError):
            CoordSystem(
                extent_x=400, extent_y=600, origin_anchor="nw_corner", origin_device_id=uuid4()
            )

    def test_geo_anchor_round_trips(self) -> None:
        cs = CoordSystem(
            extent_x=400,
            extent_y=600,
            units="feet",
            geo_anchor={"lat": 47.6, "lng": -122.3, "x": 0, "y": 0},
        )
        assert cs.geo_anchor is not None
        assert cs.geo_anchor.lat == 47.6

    def test_geo_anchor_lat_range(self) -> None:
        with pytest.raises(ValidationError):
            CoordSystem(
                extent_x=400, extent_y=600, geo_anchor={"lat": 200, "lng": 0, "x": 0, "y": 0}
            )

    def test_on_site_create(self) -> None:
        body = SiteCreate(name="DC-1", coord_system=CoordSystem(extent_x=120, extent_y=80))
        assert body.coord_system is not None
        assert body.coord_system.extent_x == 120


class TestCoordSystemFloorplanImage:
    _PNG = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
        "AAAAC0lEQVR42mNk+M8AAAMBAQAY3Y2wAAAAAElFTkSuQmCC"
    )

    def test_accepts_data_url(self) -> None:
        cs = CoordSystem(extent_x=600, extent_y=400, floorplan_image=self._PNG)
        assert cs.floorplan_image == self._PNG

    def test_accepts_https_url(self) -> None:
        cs = CoordSystem(
            extent_x=600, extent_y=400, floorplan_image="https://example.com/floor.png"
        )
        assert cs.floorplan_image is not None

    def test_none_and_empty_clear(self) -> None:
        assert CoordSystem(extent_x=1, extent_y=1, floorplan_image=None).floorplan_image is None
        assert CoordSystem(extent_x=1, extent_y=1, floorplan_image="").floorplan_image is None

    def test_rejects_non_image_data_url(self) -> None:
        with pytest.raises(ValidationError):
            CoordSystem(extent_x=1, extent_y=1, floorplan_image="data:text/plain;base64,aGk=")

    def test_rejects_bare_string(self) -> None:
        with pytest.raises(ValidationError):
            CoordSystem(extent_x=1, extent_y=1, floorplan_image="floor.png")

    def test_rejects_oversized_data_url(self) -> None:
        big = "data:image/png;base64," + ("A" * (2 * 1024 * 1024 + 10))
        with pytest.raises(ValidationError):
            CoordSystem(extent_x=1, extent_y=1, floorplan_image=big)
