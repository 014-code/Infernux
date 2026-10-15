"""Published theme palette and colour-space conversion contracts."""

import pytest

from infernux.engine.ui.theme import (
    active_editor_theme,
    hex_to_linear,
    list_editor_themes,
    srgb3,
    srgb_to_linear,
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
