"""Unit tests for the Sprint 33 QW6 tenant branding surface."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tagpulse.api.routes.tenant_branding import (
    PublicBranding,
    TenantBranding,
    TenantBrandingUpdate,
)


class TestTenantBrandingResponse:
    """The read-side payload returned by GET /tenant/branding."""

    def test_all_fields_default_to_none(self) -> None:
        body = TenantBranding()
        dumped = body.model_dump()
        assert dumped == {
            "logo_url": None,
            "logo_collapsed_url": None,
            "display_name": None,
            "brand_color": None,
        }

    def test_populated_values_round_trip(self) -> None:
        body = TenantBranding(
            logo_url="https://cdn.example.com/acme.png",
            display_name="Acme Corp",
            brand_color="#14B8A6",
        )
        dumped = body.model_dump()
        assert dumped["logo_url"] == "https://cdn.example.com/acme.png"
        assert dumped["display_name"] == "Acme Corp"
        assert dumped["brand_color"] == "#14B8A6"

    def test_read_model_accepts_a_data_url_logo(self) -> None:
        # The read side no longer caps logo length (it can hold a data: URL).
        data_url = "data:image/png;base64," + ("A" * 5000)
        body = TenantBranding(logo_url=data_url, logo_collapsed_url=data_url)
        assert body.logo_url == data_url
        assert body.logo_collapsed_url == data_url

    def test_display_name_length_capped_at_255(self) -> None:
        with pytest.raises(ValidationError):
            TenantBranding(display_name="A" * 256)


class TestTenantBrandingUpdateValidation:
    """Validators on the admin PATCH payload."""

    def test_accepts_empty_payload(self) -> None:
        payload = TenantBrandingUpdate()
        assert payload.model_dump(exclude_unset=True) == {}

    def test_explicit_nulls_clear_overrides(self) -> None:
        payload = TenantBrandingUpdate(logo_url=None, display_name=None, brand_color=None)
        # exclude_unset must keep these — caller asked to clear them.
        provided = payload.model_dump(exclude_unset=True)
        assert provided == {"logo_url": None, "display_name": None, "brand_color": None}

    def test_https_logo_url_accepted(self) -> None:
        payload = TenantBrandingUpdate(logo_url="https://cdn.example.com/acme.png")
        assert payload.logo_url == "https://cdn.example.com/acme.png"

    def test_http_logo_url_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            TenantBrandingUpdate(logo_url="http://cdn.example.com/acme.png")
        assert "https://" in str(exc.value)

    def test_non_url_logo_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantBrandingUpdate(logo_url="cdn.example.com/acme.png")

    def test_empty_logo_string_treated_as_clear(self) -> None:
        payload = TenantBrandingUpdate(logo_url="")
        assert payload.logo_url is None

    def test_valid_hex_color_accepted(self) -> None:
        for colour in ("#14B8A6", "#000000", "#FFFFFF", "#aabbcc"):
            payload = TenantBrandingUpdate(brand_color=colour)
            assert payload.brand_color == colour

    def test_invalid_hex_color_rejected(self) -> None:
        for bad in ("14B8A6", "#14B8A", "#14B8A6FF", "red", "#GGGGGG"):
            with pytest.raises(ValidationError):
                TenantBrandingUpdate(brand_color=bad)

    def test_empty_color_string_treated_as_clear(self) -> None:
        payload = TenantBrandingUpdate(brand_color="")
        assert payload.brand_color is None

    def test_display_name_whitespace_only_treated_as_clear(self) -> None:
        payload = TenantBrandingUpdate(display_name="   ")
        assert payload.display_name is None

    def test_display_name_trimmed(self) -> None:
        payload = TenantBrandingUpdate(display_name="  Acme  ")
        assert payload.display_name == "Acme"

    def test_display_name_length_capped(self) -> None:
        with pytest.raises(ValidationError):
            TenantBrandingUpdate(display_name="A" * 256)

    def test_logo_url_length_capped(self) -> None:
        too_long = "https://cdn.example.com/" + ("a" * 2100)
        with pytest.raises(ValidationError):
            TenantBrandingUpdate(logo_url=too_long)

    # -- uploaded logo (data: URL) — branding logo upload chore ----------------

    def test_data_url_logo_accepted(self) -> None:
        # A 1x1 transparent PNG as a base64 data URL.
        data_url = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4n"
            "GNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
        )
        payload = TenantBrandingUpdate(logo_url=data_url, logo_collapsed_url=data_url)
        assert payload.logo_url == data_url
        assert payload.logo_collapsed_url == data_url

    def test_svg_data_url_logo_accepted(self) -> None:
        payload = TenantBrandingUpdate(logo_url="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=")
        assert payload.logo_url is not None

    def test_oversized_data_url_logo_rejected(self) -> None:
        # 96 KB encoded cap.
        too_big = "data:image/png;base64," + ("A" * (96 * 1024 + 1))
        with pytest.raises(ValidationError) as exc:
            TenantBrandingUpdate(logo_url=too_big)
        assert "too large" in str(exc.value)

    def test_non_image_data_url_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantBrandingUpdate(logo_url="data:text/html;base64,PHNjcmlwdD4=")

    def test_collapsed_logo_validated_like_logo(self) -> None:
        with pytest.raises(ValidationError):
            TenantBrandingUpdate(logo_collapsed_url="ftp://nope/x.png")

    def test_collapsed_logo_https_accepted(self) -> None:
        payload = TenantBrandingUpdate(logo_collapsed_url="https://cdn.example.com/icon.svg")
        assert payload.logo_collapsed_url == "https://cdn.example.com/icon.svg"


class TestPublicBranding:
    """Payload returned to the unauthenticated login page."""

    def test_minimum_shape(self) -> None:
        body = PublicBranding(slug="acme", name="Acme")
        dumped = body.model_dump()
        assert dumped == {
            "slug": "acme",
            "name": "Acme",
            "display_name": None,
            "logo_url": None,
            "logo_collapsed_url": None,
            "brand_color": None,
        }

    def test_full_shape_round_trip(self) -> None:
        body = PublicBranding(
            slug="acme",
            name="Acme Industries",
            display_name="Acme",
            logo_url="https://cdn.example.com/acme.png",
            brand_color="#14B8A6",
        )
        dumped = body.model_dump()
        assert dumped["display_name"] == "Acme"
        assert dumped["logo_url"] == "https://cdn.example.com/acme.png"
        assert dumped["brand_color"] == "#14B8A6"

    def test_slug_and_name_required(self) -> None:
        with pytest.raises(ValidationError):
            PublicBranding(slug="acme")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            PublicBranding(name="Acme")  # type: ignore[call-arg]
