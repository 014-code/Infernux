from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from infernux.core.animation_clip import AnimationClip, AnimationFrame
from infernux.core.asset_types import SpriteFrame
from infernux.engine.ui.animclip2d_editor_panel import (
    AnimClip2DEditorPanel,
    _ClipState,
    _TextureState,
    _sprite_frame_by_id,
)
from infernux.engine.path_utils import resolved_path


def _source_id(index: int) -> str:
    return f"{index + 1:032x}"


def _frames(*indices: int) -> list[AnimationFrame]:
    return [AnimationFrame(sprite_frame_id=_source_id(index)) for index in indices]


def test_animation_clip_source_reference_survives_sprite_frame_reorder():
    referenced = SpriteFrame(
        stable_id=_source_id(0),
        name="referenced",
        x=0,
        y=0,
        w=32,
        h=32,
    )
    other = SpriteFrame(
        stable_id=_source_id(1),
        name="other",
        x=32,
        y=0,
        w=32,
        h=32,
    )
    animation_frame = AnimationFrame(sprite_frame_id=referenced.stable_id)

    assert _sprite_frame_by_id([other, referenced], animation_frame.sprite_frame_id) is referenced


@pytest.fixture(autouse=True)
def _isolate_animclip_panel_dirty_tracking():
    from infernux.engine.interaction import DocumentRegistry, FocusService

    previous_documents = DocumentRegistry._instance
    previous_focus = FocusService._instance
    DocumentRegistry()
    FocusService()
    try:
        yield
    finally:
        DocumentRegistry.instance().close_view("animclip2d_editor")
        DocumentRegistry._instance = previous_documents
        FocusService._instance = previous_focus


def test_animclip_authoring_snapshot_participates_in_global_undo():
    from infernux.engine.interaction import AuthoringMutationService, DocumentRegistry
    from infernux.engine.undo import UndoManager

    manager = UndoManager()
    previous_service = AuthoringMutationService._instance
    service = AuthoringMutationService(DocumentRegistry.instance())
    panel = AnimClip2DEditorPanel()
    document = panel._animclip_document()
    assert document is not None

    def edit_clip() -> None:
        panel._clips[0].name = "Run"
        panel._clips[0].fps = 24.0
        panel._clips[0].frames = _frames(0, 2, 4)

    try:
        assert service.apply(
            document.document_id,
            "Edit 2D animation clip",
            edit_clip,
            view_id=panel.window_id,
        )

        assert manager.can_undo
        assert panel._clips[0].name == "Run"
        manager.undo()
        assert panel._clips[0].name == "NewClip"
        assert panel._clips[0].fps == 12.0
        assert panel._clips[0].frames == []
        manager.redo()
        assert panel._clips[0].name == "Run"
        assert panel._clips[0].fps == 24.0
        assert [frame.sprite_frame_id for frame in panel._clips[0].frames] == [
            _source_id(0),
            _source_id(2),
            _source_id(4),
        ]
    finally:
        AuthoringMutationService._instance = previous_service


def test_animclip_frame_delete_restores_stable_identity_and_selection():
    from infernux.engine.interaction import (
        AuthoringMutationService,
        ContextRestoreStatus,
        DocumentRegistry,
        EditorContextSnapshot,
        SelectionService,
    )
    from infernux.engine.undo import UndoManager

    selection = SelectionService()
    manager = UndoManager()
    manager.set_context_hooks(
        lambda: EditorContextSnapshot(selection=selection.snapshot),
        lambda context, _phase: (
            selection.apply_snapshot(
                context.selection,
                reason="test_animclip_restore",
                record_history=False,
            ),
            ContextRestoreStatus.READY,
        )[1],
    )
    previous_service = AuthoringMutationService._instance
    AuthoringMutationService(DocumentRegistry.instance())
    panel = AnimClip2DEditorPanel()
    first = AnimationFrame(sprite_frame_id=_source_id(0))
    second = AnimationFrame(sprite_frame_id=_source_id(1))
    panel._clips[0].frames = [first, second]
    selection.select(
        panel._sequence_target(first),
        owner_id=panel.window_id,
        record_history=False,
    )

    try:
        assert panel.command_delete_selected_frame()
        assert [frame.stable_id for frame in panel._clips[0].frames] == [second.stable_id]
        assert selection.snapshot.primary == panel._sequence_target(second)

        manager.undo()
        assert [frame.stable_id for frame in panel._clips[0].frames] == [
            first.stable_id,
            second.stable_id,
        ]
        assert selection.snapshot.primary == panel._sequence_target(first)

        manager.redo()
        assert [frame.stable_id for frame in panel._clips[0].frames] == [second.stable_id]
        assert selection.snapshot.primary == panel._sequence_target(second)
    finally:
        AuthoringMutationService._instance = previous_service


class _ClipInfoContext:
    def __init__(self) -> None:
        self.semantics = []

    def label(self, _label):
        pass

    def same_line(self, *_args):
        pass

    def set_next_item_width(self, _width):
        pass

    def text_input(self, _label, value, _size):
        return value

    def drag_float(self, _label, value, *_args):
        return value

    def checkbox(self, _label, value):
        return value

    def dummy(self, *_args):
        pass

    def push_style_color(self, *_args):
        pass

    def pop_style_color(self, *_args):
        pass

    def button(self, _label):
        return False

    def begin_disabled(self, _disabled):
        pass

    def end_disabled(self):
        pass

    def record_semantic_item(self, *args, **_kwargs):
        self.semantics.append(args)


class _PaletteContext:
    def __init__(self) -> None:
        self.semantics = []
        self.invisible_sizes = []
        self.dummy_sizes = []

    def begin_child(self, *_args):
        pass

    def end_child(self):
        pass

    def label(self, _label):
        pass

    def separator(self):
        pass

    def same_line(self, *_args):
        pass

    def dummy(self, width, height):
        self.dummy_sizes.append((width, height))

    def get_content_region_avail_height(self):
        return 180.0

    def get_content_region_avail_width(self):
        return 180.0

    def begin_table(self, *_args):
        return True

    def end_table(self):
        pass

    def table_next_column(self):
        pass

    def get_cursor_pos_x(self):
        return 0.0

    def get_cursor_pos_y(self):
        return 0.0

    def set_cursor_pos_x(self, _value):
        pass

    def set_cursor_pos_y(self, _value):
        pass

    def button(self, _label, **_kwargs):
        return False

    def invisible_button(self, _label, _width, _height):
        self.invisible_sizes.append((_width, _height))

    def is_item_clicked(self, _button=0):
        return False

    def record_semantic_item(self, *args, **_kwargs):
        self.semantics.append(args)

    def is_item_hovered(self):
        return False

    def get_item_rect_min_x(self):
        return 0.0

    def get_item_rect_min_y(self):
        return 0.0

    def get_item_rect_max_x(self):
        return 50.0

    def get_item_rect_max_y(self):
        return 66.0

    def image(self, *_args):
        pass

    def draw_text(self, *_args):
        pass

    def draw_filled_rect(self, *_args):
        pass

    def draw_rect(self, *_args):
        pass

    def draw_image_rect(self, *_args):
        pass

    def draw_text_aligned(self, *_args):
        pass


def test_open_panel_refreshes_sprite_slices_on_asset_change(monkeypatch, tmp_path):
    texture_path = str(tmp_path / "sheet.png")
    texture_path_obj = tmp_path / "sheet.png"
    texture_path_obj.write_bytes(b"png")
    panel = AnimClip2DEditorPanel()
    panel._tex = _TextureState(
        file_path=texture_path,
        texture_id=1,
        tex_w=128,
        tex_h=64,
        frames=[SpriteFrame(name="frame_0", x=0, y=0, w=128, h=64)],
    )
    refreshed = [
        SpriteFrame(name="frame_0", x=0, y=0, w=64, h=64),
        SpriteFrame(name="frame_1", x=64, y=0, w=64, h=64),
    ]
    monkeypatch.setattr(panel, "_read_texture_sampling", lambda _path: ("POINT", "srgb", True, True))
    monkeypatch.setattr(panel, "_read_sprite_frames", lambda _path: refreshed)
    monkeypatch.setattr(panel, "_read_source_dimensions", lambda _path, _frames: (128, 64))
    monkeypatch.setattr(panel, "_build_texture_stamp", lambda *_args: 99)

    from infernux.engine.interaction import (
        AssetMutationKind,
        AssetMutationService,
        DocumentRegistry,
        SelectionService,
    )

    previous = AssetMutationService.instance()
    if previous is not None:
        previous.shutdown()
    bus = AssetMutationService(DocumentRegistry(), SelectionService())
    panel.on_enable()
    try:
        bus.publish_content_change(texture_path, AssetMutationKind.MODIFIED)
    finally:
        panel.on_disable()
        bus.shutdown()

    assert panel._tex is not None
    assert panel._tex.frames == refreshed
    assert panel._tex.stamp == 99
    assert panel._tex.resource_key.endswith(resolved_path(texture_path))


def test_loop_round_trips_document_session_and_saved_clip(monkeypatch, tmp_path):
    from infernux.engine.interaction import (
        DocumentIdentityKind,
        DocumentRegistry,
    )
    from infernux.core.assets import AssetManager

    texture_path = tmp_path / "countdown.png"
    texture_path.write_bytes(b"png")
    saved_path = str(tmp_path / "Countdown.animclip2d")
    database = SimpleNamespace(
        get_path_from_guid=lambda guid: str(texture_path) if guid == "texture-guid" else saved_path,
        get_guid_from_path=lambda path: (
            "texture-guid" if resolved_path(path) == resolved_path(str(texture_path)) else "saved-clip-guid"
        ),
    )
    monkeypatch.setattr(AssetManager, "_asset_database", database)
    panel = AnimClip2DEditorPanel()
    panel._tex = _TextureState(file_path=str(texture_path), guid="texture-guid")
    clip = _ClipState(name="Countdown", frames=_frames(0, 1, 2), fps=3.0, loop=False)
    panel._clips = [clip]

    session_state = DocumentRegistry.instance().capture_session_state()
    restored_registry = DocumentRegistry()
    assert restored_registry.queue_session_restore(session_state) == 1
    restored = AnimClip2DEditorPanel()
    assert restored.restore_persisted_session_document()
    assert restored._clips[0].loop is False

    monkeypatch.setattr(
        AnimationClip,
        "validate_sprite_frame_references",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        AssetManager,
        "reimport_asset",
        classmethod(lambda cls, _path: True),
    )
    panel._replace_animclip_document(resource_path=saved_path, dirty=True)
    registry = DocumentRegistry.instance()
    result = registry.request_save(panel.document_id)
    assert result.accepted
    from infernux.core.document_store import DocumentStore

    DocumentStore.flush(saved_path)
    registry.process_pending_saves()
    captured = json.loads((tmp_path / "Countdown.animclip2d").read_text(encoding="utf-8"))
    assert captured["loop"] is False
    assert [frame["sprite_frame_id"] for frame in captured["frames"]] == [
        _source_id(0),
        _source_id(1),
        _source_id(2),
    ]
    assert captured["fps"] == 3.0
    document = registry.require(panel.document_id)
    assert document.is_dirty is False
    assert document.resource_path == resolved_path(saved_path)
    assert document.key.identity_kind is DocumentIdentityKind.ASSET_GUID
    assert document.key.identity == "saved-clip-guid"


def test_animclip_save_uses_ticket_resource_path_and_never_directly_marks_saved(
    monkeypatch,
    tmp_path,
):
    from infernux.core.assets import AssetManager
    from infernux.engine.interaction import (
        DocumentRegistry,
    )

    panel = AnimClip2DEditorPanel()
    panel._tex = _TextureState(file_path="Assets/countdown.png", guid="texture-guid")
    panel._clips = [
        _ClipState(
            name="Countdown",
            frames=_frames(0, 1),
        )
    ]
    target = resolved_path(str(tmp_path / "Authoritative.animclip2d"))
    monkeypatch.setattr(
        AssetManager,
        "_asset_database",
        SimpleNamespace(
            get_guid_from_path=lambda _path: "authoritative-clip-guid",
            get_path_from_guid=lambda _guid: target,
        ),
    )
    panel._replace_animclip_document(resource_path=target, dirty=True)
    registry = DocumentRegistry.instance()
    monkeypatch.setattr(
        AnimationClip,
        "validate_sprite_frame_references",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        AssetManager,
        "reimport_asset",
        classmethod(lambda cls, _path: True),
    )
    monkeypatch.setattr(
        registry,
        "mark_saved",
        lambda *_args, **_kwargs: pytest.fail("panel bypassed the SaveTicket transaction"),
    )

    result = registry.request_save(panel.document_id)
    from infernux.core.document_store import DocumentStore

    DocumentStore.flush(target)
    registry.process_pending_saves()

    document = registry.require(panel.document_id)
    assert result.accepted
    assert resolved_path(target) == target
    assert json.loads((tmp_path / "Authoritative.animclip2d").read_text(encoding="utf-8"))["name"] == "Authoritative"
    assert document.resource_path == target
    assert document.is_dirty is False


def test_animclip_new_document_and_dirty_draft_round_trip_through_registry_session():
    from infernux.engine.interaction import DocumentRegistry

    panel = AnimClip2DEditorPanel()
    assert panel._document_is_dirty() is False
    assert panel._new_clip_document_immediate()
    assert panel._document_is_dirty() is True
    panel._clips[0].name = "Recovered Clip"
    panel._clips[0].fps = 18.0

    session_state = DocumentRegistry.instance().capture_session_state()
    restored_registry = DocumentRegistry()
    assert restored_registry.queue_session_restore(session_state) == 1
    restored = AnimClip2DEditorPanel()
    assert restored.restore_persisted_session_document()

    assert restored._document_is_dirty() is True
    assert restored._clips[0].name == "Recovered Clip"
    assert restored._clips[0].fps == 18.0
