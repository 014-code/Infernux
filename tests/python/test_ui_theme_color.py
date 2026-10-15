"""Published theme palette and colour-space conversion contracts."""

import math

import pytest

from infernux.engine.ui.theme import (
    Theme, active_editor_theme, hex_to_linear, list_editor_themes, srgb3, srgb_to_linear,
)


def test_srgb_transfer_curve_boundaries_and_order():
    assert srgb_to_linear(0.0) == 0.0
    assert srgb_to_linear(1.0) == pytest.approx(1.0)
    assert srgb_to_linear(0.02) == pytest.approx(0.02 / 12.92)
    assert srgb_to_linear(0.04045) == pytest.approx(0.04045 / 12.92, abs=1e-6)
    assert srgb_to_linear(0.5) == pytest.approx(0.21404, abs=1e-4)
    samples = [srgb_to_linear(value / 100.0) for value in range(101)]
    assert samples == sorted(samples)


def test_rgb_and_hex_helpers_share_conversion_and_preserve_alpha():
    assert srgb3(0, 0, 0) == (0, 0, 0, 1)
    assert hex_to_linear(0, 0, 0) == (0, 0, 0, 1)
    assert srgb3(1, 0, 0.5, 0.3) == pytest.approx((1, 0, srgb_to_linear(0.5), 0.3))
    assert hex_to_linear(255, 255, 255, 0.5) == pytest.approx((1, 1, 1, 0.5))
    assert hex_to_linear(128, 128, 128) == srgb3(128 / 255, 128 / 255, 128 / 255)


def test_published_editor_palette_is_valid_rgba_and_preserves_red_accent():
    palette = {name: getattr(Theme, name) for name in (
        "TEXT", "TEXT_DIM", "TEXT_DISABLED", "WINDOW_BG", "FRAME_BG", "HEADER",
        "APPLY_BUTTON", "BORDER",
    )}
    for name, value in palette.items():
        assert isinstance(value, tuple) and len(value) == 4, name
        assert all(isinstance(channel, (int, float)) and math.isfinite(channel)
                   and 0 <= channel <= 1 for channel in value), name
    red, green, blue, alpha = palette["APPLY_BUTTON"]
    assert red > green and red > blue
    assert alpha == pytest.approx(1)


def test_theme_registry_public_facade_matches_native_registry():
    themes = list_editor_themes()
    active = active_editor_theme()
    assert isinstance(themes, list) and isinstance(active, str)
    assert Theme.list_themes() == themes
    assert Theme.active_theme() == active
    assert callable(Theme.set_theme) and callable(Theme.refresh)


def test_native_theme_failures_are_not_replaced_with_empty_values(monkeypatch):
    import infernux.lib as native

    def fail():
        raise RuntimeError("native theme registry unavailable")

    monkeypatch.setattr(native, "list_editor_themes", fail)
    monkeypatch.setattr(native, "get_editor_theme", fail)
    with pytest.raises(RuntimeError, match="native theme registry unavailable"):
        list_editor_themes()
    with pytest.raises(RuntimeError, match="native theme registry unavailable"):
        active_editor_theme()
