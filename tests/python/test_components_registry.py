"""Tests for infernux.components.registry — get_type, get_all_types, T accessor."""

from infernux.components.component import InxComponent
from infernux.components.registry import get_type, get_all_types, T


# ── Test components ──

class _TestCompA(InxComponent):
    pass

class _TestCompB(_TestCompA):
    pass


class TestGetType:
    def test_finds_direct_subclass(self):
        cls = get_type("_TestCompA")
        assert cls is _TestCompA

    def test_finds_nested_subclass(self):
        cls = get_type("_TestCompB")
        assert cls is _TestCompB

    def test_returns_none_for_unknown(self):
        assert get_type("NonExistentType12345") is None


class TestGetAllTypes:
    def test_includes_test_types(self):
        all_types = get_all_types()
        assert "_TestCompA" in all_types
        assert "_TestCompB" in all_types


class TestTypeAccessor:
    def test_attribute_access(self):
        assert T._TestCompA is _TestCompA

    def test_returns_none_for_unknown(self):
        assert T.NonExistentComp99999 is None


class TestComponentsRootExports:
    def test_common_component_types_are_exported(self):
        from infernux import components

        assert components.Transform is not None
        assert components.Rigidbody is not None
        assert components.BoxCollider is not None
        assert components.CylinderCollider is not None
        assert components.Camera is not None
