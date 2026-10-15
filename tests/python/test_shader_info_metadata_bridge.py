from __future__ import annotations

import json

from infernux.engine.ui import inspector_shader_utils
from infernux.engine.ui import inspector_material


def _write_meta(path, metadata):
    document = {
        "metadata": {
            key: {
                "type": "bool" if isinstance(value, bool) else "string",
                "value": value,
            }
            for key, value in metadata.items()
        },
    }
    path.with_name(path.name + ".meta").write_text(
        json.dumps(document), encoding="utf-8"
    )


class _StyleScopeContext:
    def __init__(self):
        self.calls = []

    def push_style_var_vec2(self, *_args):
        self.calls.append("push")

    def pop_style_var(self, count):
        self.calls.append(("pop", count))

    def begin_disabled(self, _disabled):
        self.calls.append("begin_disabled")

    def end_disabled(self):
        self.calls.append("end_disabled")


def test_material_inspector_balances_style_scope_after_render_error(monkeypatch):
    ctx = _StyleScopeContext()
    state = type("State", (), {
        "extra": {"native_mat": type("Mat", (), {"file_path": "asset::submat:0"})()},
        "file_path": "",
    })()

    def fail_render(*_args):
        raise ValueError("bad shader property")

    monkeypatch.setattr(inspector_material, "_render_material_body_impl", fail_render)
    try:
        inspector_material.render_material_body(ctx, None, state)
    except ValueError:
        pass
    else:
        raise AssertionError("expected the synthetic material render error")

    assert ctx.calls == [
        "push", "push", "begin_disabled", "end_disabled", ("pop", 2),
    ]


def test_material_shader_object_field_forwards_stable_semantic_id(monkeypatch):
    calls = []

    def render_asset_reference_field(*args, **kwargs):
        calls.append((args, kwargs))
        return False

    from infernux.engine.ui import inspector_components

    monkeypatch.setattr(
        inspector_components,
        "render_asset_reference_field",
        render_asset_reference_field,
    )
    inspector_material._render_obj_field(
        object(),
        "mat_frag",
        "unlit",
        "Frag",
        "SHADER_FILE",
        lambda _path: None,
        semantic_id="asset.material.shader.fragment",
    )

    assert calls[0][1]["semantic_id"] == "asset.material.shader.fragment"


class _RangePropertyContext:
    def __init__(self):
        self.calls = []

    def align_text_to_frame_padding(self):
        pass

    def label(self, _label):
        pass

    def same_line(self, _width):
        pass

    def set_next_item_width(self, _width):
        pass

    def float_slider(self, label, value, minimum, maximum):
        self.calls.append(("float_slider", label, value, minimum, maximum))
        return 0.75

    def int_slider(self, label, value, minimum, maximum):
        self.calls.append(("int_slider", label, value, minimum, maximum))
        return 3

    def drag_float(self, *_args):
        raise AssertionError("A ranged Float must use float_slider")

    def drag_int(self, *_args):
        raise AssertionError("A ranged Int must use int_slider")


def test_inspector_catalog_reads_structured_hidden_flag_without_meta(tmp_path):
    shader = tmp_path / "internal.vert"
    shader.write_text(
        'ShaderInfo { Name "Internal/Pass" Hidden On }\n', encoding="utf-8"
    )

    assert inspector_shader_utils.parse_shader_id(str(shader)) == "Internal/Pass"
    assert inspector_shader_utils.is_shader_hidden(str(shader)) is True


def test_shader_catalog_cache_tracks_project_search_roots(monkeypatch, tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "first.frag").write_text(
        'ShaderInfo { Name "Tests/First" }\nvoid main() {}\n', encoding="utf-8"
    )
    (second_root / "second.frag").write_text(
        'ShaderInfo { Name "Tests/Second" }\nvoid main() {}\n', encoding="utf-8"
    )
    roots = [str(first_root)]
    monkeypatch.setattr(inspector_shader_utils, "_get_shader_search_roots", lambda: roots)
    inspector_shader_utils.bump_shader_property_generation()

    assert inspector_shader_utils._get_shader_catalog(".frag")["items"] == [
        ("Tests/First", "Tests/First")
    ]

    roots = [str(second_root)]
    assert inspector_shader_utils._get_shader_catalog(".frag")["items"] == [
        ("Tests/Second", "Tests/Second")
    ]


def test_native_shader_metadata_owns_the_imported_schema_and_normalizes_hdr_values(tmp_path):
    shader = tmp_path / "structured.frag"
    shader.write_text('ShaderInfo { Name "Source/ShouldNotBeParsed" }\nvoid main() {}\n', encoding="utf-8")
    color = {"name": "baseColor", "type": "Color", "default": [1.0, .5, .25, 1.0],
             "hdr": True, "range": [0.0, 1.0], "line": 7, "column": 9}
    encoded = {"name": "emissionColor", "type": "Color", "default": "[0.0, 0.0, 0.0, 0.0], HDR", "hdr": False}
    _write_meta(shader, {"shader_id": "Imported/Structured", "shader_schema_format": "ShaderInfo",
                         "properties": json.dumps([color, encoded]), "shader_hidden": True})
    assert inspector_shader_utils.parse_shader_id(str(shader)) == "Imported/Structured"
    assert inspector_shader_utils.parse_shader_properties(str(shader)) == [color,
        {"name": "emissionColor", "type": "Color", "default": [0.0, 0.0, 0.0, 0.0], "hdr": True}]
    assert inspector_shader_utils.is_shader_hidden(str(shader))


def test_unimported_shader_info_exposes_properties_internal_flags_and_catalog_identity(tmp_path):
    shader = tmp_path / "structured.vert"
    shader.write_text(
        '// ShaderInfo { Name "Commented Out" }\n'
        'ShaderInfo {\n Name "Structured/Source"\n Capabilities [ParticleSprite]\n Properties {\n'
        ' Float amount = 0.25 Range(0.0, 1.0)\n'
        ' Color glow = [0.1, 0.2, 0.3, 1.0] HDR\n'
        ' Texture2D implementationMap = white Internal\n }\n}\nvoid main() {}\n', encoding="utf-8")
    assert inspector_shader_utils.parse_shader_id(str(shader)) == "Structured/Source"
    properties = inspector_shader_utils.parse_shader_properties(str(shader))
    assert properties[0] == {"name": "amount", "type": "Float", "default": .25, "hdr": False, "range": [0.0, 1.0]}
    assert properties[1] == {"name": "glow", "type": "Color", "default": [.1, .2, .3, 1.0], "hdr": True}
    assert not properties[0].get("internal", False) and properties[2]["internal"]
    assert not inspector_shader_utils.is_shader_hidden(str(shader))


def test_shader_schema_sync_drives_material_edits_and_removes_retired_range():
    data = {"properties": {"emissionColor": {"type": 0, "value": "[0.0, 0.0, 0.0, 0.0], HDR"},
                           "amount": {"type": 0, "value": .25}, "steps": {"type": 4, "value": 1}}}
    schema = [
        {"name": "emissionColor", "type": "Color", "default": "[0.1, 0.2, 0.3, 1.0], HDR", "hdr": False},
        {"name": "amount", "type": "Float", "default": .5, "hdr": False, "range": [0.0, 1.0]},
        {"name": "steps", "type": "Int", "default": 2, "hdr": False, "range": [0, 8]},
    ]
    inspector_shader_utils._apply_shader_props_to_mat(data, schema)
    assert data["properties"]["emissionColor"] == {"type": 7, "value": [.1, .2, .3, 1.0], "hdr": True}
    amount, steps = data["properties"]["amount"], data["properties"]["steps"]
    assert amount["value"] == .25 and amount["range"] == [0.0, 1.0]
    context = _RangePropertyContext()
    assert inspector_material.render_material_property(context, "amount", amount, 0, amount["value"], 80.0)
    assert inspector_material.render_material_property(context, "steps", steps, 4, steps["value"], 80.0)
    assert amount["value"] == .75 and steps["value"] == 3
    assert context.calls == [("float_slider", "##mp_amount", .25, 0.0, 1.0), ("int_slider", "##mp_steps", 1, 0, 8)]
    schema[1].pop("range")
    inspector_shader_utils._apply_shader_props_to_mat(data, schema)
    assert data["properties"]["amount"]["value"] == .75
    assert "range" not in data["properties"]["amount"]
