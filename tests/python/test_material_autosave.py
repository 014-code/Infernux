from __future__ import annotations

import pytest

import infernux.lib as native_lib
from infernux.core.material import Material


def test_builtin_unlit_material_does_not_persist_shader_paths():
    import json
    from pathlib import Path

    from infernux.resources import resources_path

    document = json.loads(
        (Path(resources_path) / "materials" / "default_unlit.mat").read_text(
            encoding="utf-8"
        )
    )
    assert "path_hint" not in document["shaders"]["vertex"]
    assert "path_hint" not in document["shaders"]["fragment"]


class _NativeMaterial:
    def __init__(self, *, deleted: bool = False, save_result: bool = True):
        self.guid = "test-material"
        self.file_path = "Assets/Test.mat"
        self.name = "Test"
        self.deleted = deleted
        self.save_result = save_result
        self.save_count = 0

    def is_deleted(self) -> bool:
        return self.deleted

    def save(self) -> bool:
        self.save_count += 1
        return self.save_result

    def serialize_document(self) -> dict:
        return {
            "name": self.name,
            "shaders": {"vertex": {}, "fragment": {}},
            "renderState": {"renderQueue": 2000},
            "properties": {},
        }


class _AssetRegistry:
    current = None

    @classmethod
    def instance(cls):
        return cls.current


def test_render_texture_binding_does_not_schedule_asset_save(monkeypatch):
    from types import SimpleNamespace
    from infernux.core.render_texture import RenderTexture

    native = _NativeMaterial()
    bindings = {}
    native._set_render_texture = lambda name, owner: bindings.update({name: owner})
    material = Material(native)
    target = object.__new__(RenderTexture)
    target._native = SimpleNamespace(asset_guid='')
    monkeypatch.setattr(material, '_auto_save', lambda: pytest.fail('runtime target must not save an asset'))
    material.set_texture('texSampler', target)
    assert bindings['texSampler'] is target._native
    assert native.save_count == 0


class _MaterialRegistry:
    def __init__(self, native=None, error: Exception | None = None):
        self.native = native
        self.error = error

    def load_material(self, _path: str):
        if self.error is not None:
            raise self.error
        return self.native

    def get_builtin_material(self, _name: str):
        if self.error is not None:
            raise self.error
        return self.native


def test_material_load_uses_the_asset_registry_as_its_only_loader(monkeypatch):
    native = _NativeMaterial()
    _AssetRegistry.current = _MaterialRegistry(native)
    monkeypatch.setattr(native_lib, "AssetRegistry", _AssetRegistry)

    loaded = Material.load("Assets/Test.mat")

    assert loaded is not None
    assert loaded.native is native


@pytest.mark.parametrize("method, argument", [("load", "bad.mat"), ("get", "Bad")])
def test_material_registry_failures_are_not_suppressed(
    monkeypatch, method: str, argument: str
):
    _AssetRegistry.current = _MaterialRegistry(error=RuntimeError("registry failed"))
    monkeypatch.setattr(native_lib, "AssetRegistry", _AssetRegistry)

    with pytest.raises(RuntimeError, match="registry failed"):
        getattr(Material, method)(argument)


def test_material_template_roundtrip_uses_canonical_native_color_and_document_semantics():
    from infernux.engine.ui.project_file_ops import _new_material_document

    native = native_lib.InxMaterial('Authored', 'Unlit')
    native.set_color('baseColor', (1., .55, .12, 1.))
    expected = native.serialize_document()['properties']['baseColor']['type']
    document = _new_material_document('Created')
    assert "path_hint" not in document["shaders"]["vertex"] and "path_hint" not in document["shaders"]["fragment"]
    assert document['properties']['baseColor']['type'] == expected
    assert native.deserialize_document(document)
    material = Material(native)
    assert material.to_dict() == material.serialize_document() == native.serialize_document()
    assert {'shaders', 'renderState', 'properties'} <= material.to_dict().keys()


def test_pending_material_owners_keep_independent_save_failure_and_retirement_state():
    first_native, second_native = _NativeMaterial(), _NativeMaterial()
    first, second = Material(first_native), Material(second_native)
    try:
        first._auto_save()
        second._auto_save()
        assert not Material._pending_saves
        first._last_save_time = second._last_save_time = float('inf')
        first._auto_save()
        second._auto_save()
        assert set(Material._pending_saves) == {id(first), id(second)}
        first_native.save_result = False
        first._flush_save()
        assert first_native.save_count == 2 and first._save_pending
        assert Material._pending_saves[id(first)] is first
        first_native.deleted = True
        first._flush_save()
        assert first_native.save_count == 2 and not first._save_pending
        assert set(Material._pending_saves) == {id(second)}
        second.dispose()
        assert not second._save_pending and not Material._pending_saves
    finally:
        Material._pending_saves.clear()
