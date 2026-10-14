"""Pure-logic tests for the editor UI layer (no ImGui frame required).

Covers: theme color math + C++ SSOT override, viewport math, window manager
state machine, panel state persistence, scene-view math helpers, UI-editor
geometry, and selection manager behavior.
"""


from __future__ import annotations


import math


import os


import pytest


from infernux.engine.ui.theme import Theme, srgb_to_linear, srgb3, hex_to_linear


class _WindowManagerPanelInteractions:
    """Explicit interaction boundary for WindowManager-only tests."""

    def bind_view(self, _view_id, _type_id, _instance):
        pass

    def unbind_view(self, _view_id):
        return True

    def records_focus_history(self, **_identity):
        return True

    def is_document_backed(self, **_identity):
        return False


def _window_manager(engine, panel_interactions=None):
    from infernux.engine.ui.window_manager import WindowManager

    registrar = getattr(engine, "register_gui", None)
    if registrar is None:
        registrar = lambda _window_id, _instance: None
    return WindowManager(
        engine,
        panel_interactions or _WindowManagerPanelInteractions(),
        registrar,
    )


class _FakeCtx:
    """Minimal stand-in exposing the mouse-position API ViewportInfo uses."""

    def __init__(self, x: float, y: float):
        self._x, self._y = x, y

    def get_mouse_pos_x(self) -> float:
        return self._x

    def get_mouse_pos_y(self) -> float:
        return self._y


def test_window_manager_requires_explicit_interaction_and_gui_boundaries():
    from infernux.engine.ui.window_manager import WindowManager

    with pytest.raises(ValueError, match="PanelInteractionRegistry"):
        WindowManager(object(), None, lambda _window_id, _instance: None)
    with pytest.raises(TypeError, match="panel GUI registrar"):
        WindowManager(object(), _WindowManagerPanelInteractions(), None)


def test_default_editor_and_game_ui_font_size_is_18px():
    import inspect

    from infernux.engine.engine import Engine
    from infernux.ui import UIButton, UIText

    assert Theme.UI_DEFAULT_FONT_SIZE == 18.0
    assert UIText().font_size == 18.0
    assert UIButton().font_size == 18.0
    assert inspect.signature(Engine.set_gui_font).parameters["font_size"].default == 18


def test_direct_ui_resize_makes_layout_axes_authoritative():
    from infernux.engine.ui.ui_rect_manipulation import (
        layout_snapshot,
        prepare_layout_resize,
    )
    from infernux.ui import UIText
    from infernux.ui.enums import TextResizeMode, UILayoutSizing

    text = UIText()
    text.width_sizing = UILayoutSizing.Fill
    text.height_sizing = UILayoutSizing.Hug
    text.resize_mode = TextResizeMode.AutoWidth
    before = layout_snapshot(text)

    prepare_layout_resize(text, width=True, height=False)

    assert before["width_sizing"] == UILayoutSizing.Fill
    assert before["height_sizing"] == UILayoutSizing.Hug
    assert text.width_sizing == UILayoutSizing.Fixed
    assert text.height_sizing == UILayoutSizing.Hug
    assert text.resize_mode == TextResizeMode.FixedSize


def test_shared_layout_size_commit_updates_only_touched_axes():
    from infernux.engine.ui.ui_rect_manipulation import apply_layout_size
    from infernux.ui import UIText
    from infernux.ui.enums import TextResizeMode, UILayoutSizing

    text = UIText()
    text.width = 120.0
    text.height = 48.0
    text.width_sizing = UILayoutSizing.Fill
    text.height_sizing = UILayoutSizing.Hug
    text.resize_mode = TextResizeMode.AutoHeight

    apply_layout_size(text, width=260.0)

    assert text.width == 260.0
    assert text.height == 48.0
    assert text.width_sizing == UILayoutSizing.Fixed
    assert text.height_sizing == UILayoutSizing.Hug
    assert text.resize_mode == TextResizeMode.FixedSize


def test_button_render_color_consumes_selectable_state_tint():
    from infernux.ui import UIButton
    from infernux.ui.ui_render_dispatch import _get_button_bg
    from infernux.ui.ui_selectable import SelectionState

    button = UIButton()
    button.background_color = [0.8, 0.5, 0.25, 0.75]
    button.normal_color = [1.0, 0.5, 0.25, 1.0]
    button.highlighted_color = [0.25, 1.0, 0.5, 0.8]
    button.pressed_color = [0.5, 0.25, 1.0, 0.4]

    assert _get_button_bg(button) == pytest.approx([0.8, 0.25, 0.0625, 0.75])
    button._current_state = SelectionState.Highlighted
    assert _get_button_bg(button) == pytest.approx([0.2, 0.5, 0.125, 0.6])
    button._current_state = SelectionState.Pressed
    assert _get_button_bg(button) == pytest.approx([0.4, 0.125, 0.25, 0.3])


def test_ui_material_slots_use_normal_material_assets_and_button_has_two_slots():
    from infernux.components.fields import get_serialized_fields
    from infernux.core.material import Material
    from infernux.ui import UIButton, UIText
    from infernux.ui.ui_render_dispatch import _get_button_bg, _get_label_attrs

    class NativeMaterialProbe:
        _texture_assets_pending = False

        def __init__(self, guid, color):
            self.guid = guid
            self.name = guid
            self.file_path = ""
            self._color = color

        def get_version(self):
            return 1

        def _get_render_texture(self, name):
            return None

        def has_property(self, name):
            return name == "baseColor"

        def get_color(self, name):
            assert name == "baseColor"
            return self._color

    background = Material(NativeMaterialProbe("background-material", (0.5, 0.25, 1.0, 0.8)))
    label = Material(NativeMaterialProbe("text-material", (0.25, 1.0, 0.5, 0.5)))
    button = UIButton()
    button.background_color = [0.8, 0.4, 0.2, 1.0]
    button.label_color = [1.0, 0.5, 0.25, 1.0]
    button.background_material = background
    button.text_material = label

    assert button.material is background
    assert _get_button_bg(button) == pytest.approx([0.4, 0.1, 0.2, 0.8])
    _text, text_color, _attrs = _get_label_attrs(button, 1.0)
    assert text_color == pytest.approx([0.25, 0.5, 0.125, 0.5])
    assert {"material", "text_material"} <= set(get_serialized_fields(UIButton))
    assert "material" in get_serialized_fields(UIText)


def test_engine_gui_registration_forwards_overlay_priority():
    from types import SimpleNamespace

    from infernux.engine.engine import Engine

    calls = []
    engine = Engine.__new__(Engine)
    engine._engine = SimpleNamespace(
        register_gui_renderable=lambda name, renderable, priority: calls.append(
            (name, renderable, priority)
        )
    )
    engine._gui_objects = {}
    renderable = object()

    engine.register_gui("global_overlay", renderable, priority=1000)

    assert calls == [("global_overlay", renderable, 1000)]
    assert engine._gui_objects["global_overlay"] is renderable


def test_engine_gui_registration_rejects_duplicate_renderable_identity():
    from types import SimpleNamespace

    from infernux.engine.engine import Engine

    calls = []
    engine = Engine.__new__(Engine)
    engine._engine = SimpleNamespace(
        register_gui_renderable=lambda name, renderable, priority: calls.append(
            (name, renderable, priority)
        )
    )
    existing = object()
    engine._gui_objects = {"scene_view": existing}

    with pytest.raises(RuntimeError, match="already registered"):
        engine.register_gui("scene_view", object())

    assert calls == []
    assert engine._gui_objects["scene_view"] is existing


def test_existing_window_registration_is_atomic_when_native_registration_fails():
    from infernux.engine.interaction import (
        EditorInteractionCore,
        PanelInteractionDescriptor,
    )
    from infernux.engine.ui.window_manager import WindowManager

    class Engine:
        @staticmethod
        def register_gui(_window_id, _instance):
            raise RuntimeError("native registration failed")

    class Panel:
        is_open = True

    previous_manager = WindowManager._instance
    core = EditorInteractionCore()
    try:
        manager = _window_manager(Engine(), core.panels)
        core.panels.register_type("native", PanelInteractionDescriptor())

        with pytest.raises(RuntimeError, match="native registration failed"):
            manager.register_existing_window("native", Panel(), "native")

        with pytest.raises(KeyError, match="Unknown window id"):
            manager.get_window_state("native")
        assert manager.get_window_instance("native") is None
        assert core.panels.instance_for_view("native") is None
        assert "native" not in manager._registered_instance_ids
    finally:
        core.shutdown()
        WindowManager._instance = previous_manager


def test_engine_docked_window_selection_forwards_modal_flag():
    from types import SimpleNamespace

    from infernux.engine.engine import Engine

    calls = []
    engine = Engine.__new__(Engine)
    engine._engine = SimpleNamespace(
        select_docked_window=lambda window_id, allow_during_modal: calls.append(
            (window_id, allow_during_modal)
        )
    )

    engine.select_docked_window("particle_graph_editor", True)

    assert calls == [("particle_graph_editor", True)]


def test_engine_docked_window_selection_rejects_stale_native_binding():
    from infernux.engine.engine import Engine

    class LegacyNativeEngine:
        def __init__(self):
            self.focus_requests = []

        def select_docked_window(self, window_id):
            self.focus_requests.append(window_id)

    engine = Engine.__new__(Engine)
    engine._engine = LegacyNativeEngine()

    with pytest.raises(TypeError):
        engine.select_docked_window("particle_graph_editor", True)

    assert engine._engine.focus_requests == []


class TestThemeColorMath:
    def test_srgb_to_linear_low_segment(self):
        assert srgb_to_linear(0.0) == 0.0
        assert srgb_to_linear(0.04045) == pytest.approx(0.04045 / 12.92)

    def test_srgb_to_linear_power_segment(self):
        assert srgb_to_linear(1.0) == pytest.approx(1.0)
        assert srgb_to_linear(0.5) == pytest.approx(((0.5 + 0.055) / 1.055) ** 2.4)

    def test_srgb_to_linear_monotonic(self):
        samples = [srgb_to_linear(i / 20.0) for i in range(21)]
        assert all(b >= a for a, b in zip(samples, samples[1:]))

    def test_srgb3_keeps_alpha(self):
        r, g, b, a = srgb3(1.0, 0.5, 0.25, 0.7)
        assert a == 0.7
        assert r == pytest.approx(1.0)

    def test_hex_to_linear(self):
        r, g, b, a = hex_to_linear(255, 0, 255)
        assert r == pytest.approx(1.0)
        assert g == pytest.approx(0.0)
        assert b == pytest.approx(1.0)
        assert a == 1.0


class TestThemeNativeSSOT:
    """The C++ EditorThemeRegistry is the single source of truth."""

    def test_native_registry_exposed(self):
        from infernux.lib import (
            get_editor_theme_colors,
            get_editor_theme_floats,
            get_editor_theme_vec2s,
        )
        colors = get_editor_theme_colors()
        floats = get_editor_theme_floats()
        assert len(colors) >= 90, "expected the full migrated color table"
        assert len(floats) >= 50
        assert isinstance(get_editor_theme_vec2s(), dict)

    def test_python_theme_overridden_from_native(self):
        from infernux.lib import get_editor_theme_colors
        native = get_editor_theme_colors()
        assert getattr(Theme, "_NATIVE_OVERRIDES_APPLIED", 0) > 0
        # Every overlapping constant must match the native value exactly.
        mismatches = []
        for name, value in native.items():
            if hasattr(Theme, name):
                if tuple(getattr(Theme, name)) != tuple(value):
                    mismatches.append(name)
        assert mismatches == []

    def test_native_values_are_rgba(self):
        from infernux.lib import get_editor_theme_colors
        for name, value in get_editor_theme_colors().items():
            assert len(value) == 4, name
            assert all(isinstance(c, float) for c in value), name

    def test_play_border_color_logic(self):
        playing = Theme.get_play_border_color(False)
        paused = Theme.get_play_border_color(True)
        assert playing == tuple(Theme.BORDER_PLAY)
        assert paused == tuple(Theme.BORDER_PAUSE)
        assert playing != paused


class TestViewportInfo:
    def _vp(self):
        from infernux.engine.ui.viewport_utils import ViewportInfo
        return ViewportInfo(image_min_x=100, image_min_y=50,
                            image_max_x=300, image_max_y=250)

    def test_dimensions(self):
        vp = self._vp()
        assert vp.width == 200 and vp.height == 200

    def test_mouse_local(self):
        vp = self._vp()
        assert vp.mouse_local(_FakeCtx(150, 75)) == (50, 25)

    def test_mouse_inside_boundaries(self):
        vp = self._vp()
        assert vp.is_mouse_inside(_FakeCtx(100, 50))      # top-left corner
        assert vp.is_mouse_inside(_FakeCtx(300, 250))     # bottom-right corner
        assert not vp.is_mouse_inside(_FakeCtx(99, 50))
        assert not vp.is_mouse_inside(_FakeCtx(301, 250))


class TestWindowManager:
    def test_menu_contract_supports_five_levels_and_parallel_translation_keys(self):
        manager = _window_manager(object())
        keys = (
            "menu.extensions",
            "vendor.example.root",
            "vendor.example.tools",
            "vendor.example.diagnostics",
            "vendor.example.live",
        )
        manager.register_window_type(
            "vendor.example.panel",
            object,
            "Live Panel",
            menu_path="Extensions/Example/Tools/Diagnostics/Live",
            menu_path_keys=keys,
        )

        info = manager.get_registered_types()["vendor.example.panel"]
        assert info.menu_path == "Extensions/Example/Tools/Diagnostics/Live"
        assert info.menu_path_keys == keys

    @pytest.mark.parametrize(
        ("path", "keys", "message"),
        [
            ("Extensions//Live", None, "non-empty author labels"),
            ("Extensions/Live", ("menu.extensions",), "exactly one entry"),
            ("", ("hidden.key",), "hidden editor panels"),
        ],
    )
    def test_menu_contract_rejects_ambiguous_paths(self, path, keys, message):
        manager = _window_manager(object())
        with pytest.raises((TypeError, ValueError), match=message):
            manager.register_window_type(
                "invalid", object, "Invalid", menu_path=path, menu_path_keys=keys
            )

    @staticmethod
    def _layout_reset_fixture():
        from infernux.engine.interaction import (
            DocumentCapability,
            DocumentKind,
            EditorInteractionCore,
            PanelInteractionDescriptor,
        )
        from infernux.engine.ui.closable_panel import ClosablePanel
        from infernux.engine.ui.editor_panel import EditorPanel
        from infernux.engine.ui.window_manager import WindowManager

        class Engine:
            def __init__(self):
                self.registered = {}
                self.reset_count = 0
                self.focused = []

            def register_gui(self, window_id, instance):
                self.registered[window_id] = instance

            def unregister_gui(self, window_id):
                self.registered.pop(window_id, None)

            def reset_imgui_layout(self):
                self.reset_count += 1

            def select_docked_window(self, window_id, *, allow_during_modal=False):
                self.focused.append((window_id, bool(allow_during_modal)))

        class Controller:
            def __init__(self):
                self.discard_calls = 0

            def discard(self, *, document_id):
                assert document_id == "graph-document"
                self.discard_calls += 1
                return True

        core = EditorInteractionCore()
        engine = Engine()
        manager = _window_manager(engine, core.panels)
        for type_id in ("scene_view", "graph"):
            core.panels.register_type(type_id, PanelInteractionDescriptor())

        scene = EditorPanel("Scene", "scene_view")
        manager.register_existing_window("scene_view", scene, "scene_view")
        manager.register_window_type(
            "graph",
            ClosablePanel,
            "Graph",
            factory=lambda: ClosablePanel("Graph", "graph"),
        )
        graph = manager.open_window("graph")
        manager.process_pending_actions()

        controller = Controller()
        document = core.documents.create(
            DocumentKind.GENERIC,
            "Graph",
            document_id="graph-document",
            revision=1,
            saved_revision=0,
            capabilities=DocumentCapability.DISCARD,
            controller=controller,
        )
        graph.bind_document(document.document_id)
        return core, engine, manager, scene, graph, document, controller

    def test_native_titlebar_close_intent_uses_window_manager_lifecycle(self):
        from infernux.engine.interaction import (
            EditorInteractionCore,
            PanelInteractionDescriptor,
        )
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def select_docked_window(self, *_args, **_kwargs):
                pass

        class NativePanel:
            def __init__(self):
                self.open = True
                self.on_request_close = None

            def is_open(self):
                return self.open

            def set_open(self, value):
                self.open = bool(value)

        previous = WindowManager._instance
        core = None
        try:
            core = EditorInteractionCore()
            manager = _window_manager(Engine(), core.panels)
            core.panels.register_type(
                "native",
                PanelInteractionDescriptor(),
            )
            panel = NativePanel()
            manager.register_existing_window("native", panel, "native")
            state_changes = []
            manager.set_on_state_changed(lambda: state_changes.append(True))

            core.focus.activate_panel(
                "native",
                view_id="native",
                reason="test",
                record_history=False,
            )
            core.modals.register(
                "native.modal",
                is_active=lambda: True,
                render=lambda _ctx: None,
                cancel=lambda: None,
            )
            assert core.modals.activate("native.modal", owner_id="native")
            assert callable(panel.on_request_close)

            assert panel.on_request_close()

            assert manager.get_window_state("native") is WindowState.CLOSED
            assert not panel.open
            assert core.focus.snapshot.active_view_id == ""
            assert core.modals.active_modal_id == ""
            assert state_changes
        finally:
            if core is not None:
                core.shutdown()
            WindowManager._instance = previous

    def test_reset_layout_cancel_preserves_dirty_dynamic_view(self):
        from infernux.engine.interaction import CloseState
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        previous = WindowManager._instance
        core = None
        try:
            core, engine, manager, _scene, graph, document, controller = (
                self._layout_reset_fixture()
            )

            assert manager.reset_layout()
            assert core.close_coordinator.state is CloseState.AWAITING_DECISION
            assert core.close_coordinator.active_document is document
            assert core.modals.active_modal_id == "editor.unsaved_changes"
            assert engine.reset_count == 0

            core.close_coordinator.cancel()
            manager.process_pending_actions()

            assert manager.get_window_state("graph") in {
                WindowState.OPEN,
                WindowState.FOCUSED,
            }
            assert manager.get_window_instance("graph") is graph
            assert core.documents.document_for_view("graph") is document
            assert controller.discard_calls == 0
            assert engine.reset_count == 0
            assert core.modals.active_modal_id == ""
        finally:
            if core is not None:
                core.shutdown()
            WindowManager._instance = previous

    def test_reset_layout_discards_then_formally_retires_dynamic_view(self):
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        previous = WindowManager._instance
        core = None
        try:
            core, engine, manager, _scene, graph, _document, controller = (
                self._layout_reset_fixture()
            )

            assert manager.reset_layout()
            core.close_coordinator.decide_discard()
            manager.process_pending_actions()

            assert controller.discard_calls == 1
            assert manager.get_window_state("graph") is WindowState.CLOSED
            assert "graph" not in manager._window_instances
            assert core.documents.document_for_view("graph") is None
            assert graph.document_id == ""
            assert "graph" not in engine.registered
            assert engine.reset_count == 1
            assert engine.focused[-1] == ("scene_view", False)
        finally:
            if core is not None:
                core.shutdown()
            WindowManager._instance = previous

    def test_reset_layout_does_not_prompt_for_document_with_retained_view(self):
        from infernux.engine.interaction import CloseState
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        previous = WindowManager._instance
        core = None
        try:
            core, engine, manager, _scene, _graph, document, controller = (
                self._layout_reset_fixture()
            )
            core.documents.attach_view(document.document_id, "scene_view")

            assert manager.reset_layout()
            assert core.close_coordinator.state is CloseState.IDLE
            manager.process_pending_actions()

            assert controller.discard_calls == 0
            assert document.is_dirty
            assert document.view_ids == {"scene_view"}
            assert manager.get_window_state("graph") is WindowState.CLOSED
            assert engine.reset_count == 1
        finally:
            if core is not None:
                core.shutdown()
            WindowManager._instance = previous

    def test_document_panel_payload_without_formal_snapshot_is_pruned(self, monkeypatch):
        from infernux.engine.ui import panel_state

        monkeypatch.setattr(
            panel_state,
            "_state",
            {
                "panel:animclip2d_editor": {"dirty": True, "clips": ["draft"]},
                "panel:particle_graph_editor": {"schema": "view"},
                "panel:console": {"filter": "warning"},
                "window_manager": {"open_windows": {}},
            },
        )

        removed = panel_state.prune_document_view_states(
            is_document_backed=lambda view_id: view_id
            in {"animclip2d_editor", "particle_graph_editor"},
            has_restorable_document=lambda view_id: view_id
            == "particle_graph_editor",
        )

        assert removed == ("animclip2d_editor",)
        assert "panel:animclip2d_editor" not in panel_state.keys()
        assert panel_state.get("panel:particle_graph_editor") == {"schema": "view"}
        assert panel_state.get("panel:console") == {"filter": "warning"}

    def test_window_state_retains_type_identity_for_closed_dynamic_views(
        self,
        _reset_editor_interaction_state,
    ):
        from infernux.engine.interaction import (
            PanelInteractionDescriptor,
            PanelInteractionRegistry,
        )
        from infernux.engine.ui.window_manager import WindowManager

        previous = WindowManager._instance
        try:
            panels = PanelInteractionRegistry()
            panels.register_type(
                "particle_graph_editor",
                PanelInteractionDescriptor(document_backed=True),
            )
            manager = _window_manager(object(), panels)
            manager.load_state(
                {
                    "open_windows": {"graph-instance": False},
                    "window_types": {
                        "graph-instance": "particle_graph_editor",
                    },
                }
            )

            assert manager.window_type_id("graph-instance") == "particle_graph_editor"
            assert manager.is_document_backed_view(
                "graph-instance",
                manager.window_type_id("graph-instance"),
            )
        finally:
            WindowManager._instance = previous

    def test_terminal_discard_closes_document_backed_view_in_saved_state(
        self,
        _reset_editor_interaction_state,
    ):
        from infernux.engine.interaction import (
            DocumentKind,
            PanelInteractionDescriptor,
            PanelInteractionRegistry,
        )
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        previous = WindowManager._instance
        try:
            panels = PanelInteractionRegistry()
            panels.register_type(
                "particle_graph_editor",
                PanelInteractionDescriptor(document_backed=True),
            )
            manager = _window_manager(object(), panels)
            manager._window_states["particle_graph_editor"] = WindowState.OPEN
            manager._window_type_ids["particle_graph_editor"] = (
                "particle_graph_editor"
            )

            document = _reset_editor_interaction_state.create(
                DocumentKind.PARTICLE_GRAPH,
                "Unsaved Graph",
                revision=1,
                saved_revision=0,
            )
            _reset_editor_interaction_state.attach_view(
                document.document_id,
                "particle_graph_editor",
            )
            _reset_editor_interaction_state.abandon_session_changes(
                document.document_id
            )

            state = manager.save_state()
            assert state["open_windows"]["particle_graph_editor"] is False
        finally:
            WindowManager._instance = previous

    def test_deleted_asset_closes_its_authoring_view_without_dormant_draft(
        self,
        _reset_editor_interaction_state,
        tmp_path,
    ):
        from infernux.engine.interaction import (
            AssetContentChange,
            AssetMutation,
            AssetMutationKind,
            DocumentKey,
            DocumentKind,
            PanelInteractionDescriptor,
            PanelInteractionRegistry,
        )
        from infernux.engine.ui.closable_panel import ClosablePanel
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            @staticmethod
            def register_gui(_window_id, _instance):
                pass

            @staticmethod
            def unregister_gui(_window_id):
                pass

        previous = WindowManager._instance
        try:
            panels = PanelInteractionRegistry()
            panels.register_type(
                "particle_graph_editor",
                PanelInteractionDescriptor(document_backed=True),
            )
            manager = _window_manager(Engine(), panels)
            manager.register_window_type(
                "particle_graph_editor",
                ClosablePanel,
                "Particle Graph",
                factory=lambda: ClosablePanel(
                    "Particle Graph",
                    "particle_graph_editor",
                ),
            )
            panel = manager.open_window("particle_graph_editor")
            manager.process_pending_actions()

            path = tmp_path / "Smoke.particlegraph"
            path.write_text("{}", encoding="utf-8")
            document = _reset_editor_interaction_state.create(
                DocumentKind.PARTICLE_GRAPH,
                "Smoke",
                key=DocumentKey.asset(DocumentKind.PARTICLE_GRAPH, "particle-guid"),
                resource_path=str(path),
                revision=1,
                saved_revision=0,
            )
            panel.bind_document(document.document_id)
            locator = _reset_editor_interaction_state.locate(document.document_id)
            path.unlink()

            manager.on_asset_mutation(
                AssetContentChange(
                    AssetMutation(
                        AssetMutationKind.DELETED,
                        str(path),
                        guid="particle-guid",
                    ),
                    1,
                )
            )
            manager.process_pending_actions()

            assert manager.get_window_state(
                "particle_graph_editor"
            ) is WindowState.CLOSED
            assert panel.document_id == ""
            assert _reset_editor_interaction_state.get(document.document_id) is None
            assert _reset_editor_interaction_state.resolve_locator(locator) is None
        finally:
            WindowManager._instance = previous

    def test_startup_does_not_recreate_document_panel_without_snapshot(
        self,
        _reset_editor_interaction_state,
    ):
        from infernux.engine.interaction import (
            PanelInteractionDescriptor,
            PanelInteractionRegistry,
        )
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Panel:
            def __init__(self):
                self.is_open = True

            def set_open(self, value):
                self.is_open = bool(value)

        previous = WindowManager._instance
        try:
            panels = PanelInteractionRegistry()
            panels.register_type(
                "particle_graph_editor",
                PanelInteractionDescriptor(document_backed=True),
            )
            manager = _window_manager(object(), panels)
            manager.register_window_type(
                "particle_graph_editor",
                Panel,
                "Particle Graph",
                factory=Panel,
            )

            manager.load_state(
                {
                    "open_windows": {"particle_graph_editor": True},
                    "window_types": {
                        "particle_graph_editor": "particle_graph_editor"
                    },
                }
            )

            assert manager.get_window_state(
                "particle_graph_editor"
            ) is WindowState.CLOSED
            assert manager.get_window_instance("particle_graph_editor") is None
        finally:
            WindowManager._instance = previous

    def test_startup_discards_pending_draft_for_closed_document_view(
        self,
        _reset_editor_interaction_state,
    ):
        from infernux.engine.interaction import (
            DocumentKind,
            PanelInteractionDescriptor,
            PanelInteractionRegistry,
        )
        from infernux.engine.ui.window_manager import WindowManager

        class Controller:
            @staticmethod
            def capture_document_restore_state(_document_id):
                return {"draft": True}

        document = _reset_editor_interaction_state.create(
            DocumentKind.PARTICLE_GRAPH,
            "Closed Draft",
            revision=1,
            saved_revision=0,
            controller=Controller(),
        )
        _reset_editor_interaction_state.attach_view(
            document.document_id,
            "particle_graph_editor",
        )
        session = _reset_editor_interaction_state.capture_session_state()
        _reset_editor_interaction_state.clear()
        assert _reset_editor_interaction_state.queue_session_restore(session) == 1

        previous = WindowManager._instance
        try:
            panels = PanelInteractionRegistry()
            panels.register_type(
                "particle_graph_editor",
                PanelInteractionDescriptor(document_backed=True),
            )
            manager = _window_manager(object(), panels)
            manager.load_state(
                {
                    "open_windows": {"particle_graph_editor": False},
                    "window_types": {
                        "particle_graph_editor": "particle_graph_editor"
                    },
                }
            )

            assert not _reset_editor_interaction_state.has_pending_session_document(
                "particle_graph_editor"
            )
            assert _reset_editor_interaction_state.capture_session_state()[
                "documents"
            ] == []
        finally:
            WindowManager._instance = previous

    def test_startup_restores_document_panel_when_snapshot_exists(
        self,
        _reset_editor_interaction_state,
    ):
        from infernux.engine.interaction import (
            DocumentKind,
            PanelInteractionDescriptor,
            PanelInteractionRegistry,
        )
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Panel:
            def __init__(self):
                self.is_open = True

            def set_open(self, value):
                self.is_open = bool(value)

        class Controller:
            @staticmethod
            def capture_document_restore_state(_document_id):
                return {"asset": "Assets/Smoke.particlegraph"}

        document = _reset_editor_interaction_state.create(
            DocumentKind.PARTICLE_GRAPH,
            "Smoke",
            controller=Controller(),
        )
        _reset_editor_interaction_state.attach_view(
            document.document_id,
            "particle_graph_editor",
        )
        session = _reset_editor_interaction_state.capture_session_state()
        _reset_editor_interaction_state.clear()
        assert _reset_editor_interaction_state.queue_session_restore(session) == 1

        previous = WindowManager._instance
        try:
            panels = PanelInteractionRegistry()
            panels.register_type(
                "particle_graph_editor",
                PanelInteractionDescriptor(document_backed=True),
            )
            manager = _window_manager(object(), panels)
            manager.register_window_type(
                "particle_graph_editor",
                Panel,
                "Particle Graph",
                factory=Panel,
            )

            manager.load_state(
                {
                    "open_windows": {"particle_graph_editor": True},
                    "window_types": {
                        "particle_graph_editor": "particle_graph_editor"
                    },
                }
            )

            assert manager.get_window_state(
                "particle_graph_editor"
            ) is WindowState.OPENING
            assert manager.get_window_instance("particle_graph_editor") is not None
        finally:
            WindowManager._instance = previous

    def test_dynamic_panel_reload_restores_open_view_with_new_class(self):
        from infernux.engine.ui.window_manager import WindowState

        class Engine:
            def __init__(self):
                self.registered = {}

            def register_gui(self, window_id, instance):
                self.registered[window_id] = instance

            def unregister_gui(self, window_id):
                self.registered.pop(window_id)

        class OriginalPanel:
            def __init__(self):
                self.is_open = True

            def set_open(self, value):
                self.is_open = bool(value)

        class ReloadedPanel(OriginalPanel):
            pass

        manager = _window_manager(Engine())
        manager.register_window_type(
            "plugin.tool",
            OriginalPanel,
            "Plugin Tool",
            factory=OriginalPanel,
        )
        original = manager.open_window("plugin.tool")
        manager.process_pending_actions()

        views = manager.capture_open_views_for_types(("plugin.tool",))
        assert views == (("plugin.tool", "plugin.tool"),)
        assert manager.unregister_window_type("plugin.tool")
        manager.register_window_type(
            "plugin.tool",
            ReloadedPanel,
            "Plugin Tool",
            factory=ReloadedPanel,
        )
        manager.restore_reloaded_views(views)
        manager.process_pending_actions()

        current = manager.get_window_instance("plugin.tool")
        assert current is not original
        assert isinstance(current, ReloadedPanel)
        assert manager.get_window_state("plugin.tool") is WindowState.OPEN

    def test_dynamic_panel_menu_close_finalizes_lifecycle_before_unregister(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.editor_panel import EditorPanel
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.registered = {}

            def register_gui(self, window_id, instance):
                self.registered[window_id] = instance

            def unregister_gui(self, window_id):
                self.registered.pop(window_id)

            @staticmethod
            def select_docked_window(_window_id):
                pass

        class Panel(EditorPanel):
            def __init__(self):
                super().__init__("Utility", "utility")
                self.disable_count = 0

            def on_disable(self):
                self.disable_count += 1

        previous_manager = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            FocusService()
            engine = Engine()
            manager = _window_manager(engine)
            manager.register_window_type(
                "utility",
                Panel,
                "Utility",
                factory=Panel,
                singleton=True,
            )
            panel = manager.open_window("utility")
            manager.process_pending_actions()
            panel._enable_called = True

            manager.close_window("utility")
            assert panel.disable_count == 0
            assert manager.get_window_state("utility") is WindowState.CLOSING

            manager.process_pending_actions()
            assert panel.disable_count == 1
            assert manager.get_window_state("utility") is WindowState.CLOSED
            assert "utility" not in engine.registered

            panel._finalize_close_lifecycle()
            assert panel.disable_count == 1
        finally:
            FocusService._instance = previous_focus
            WindowManager._instance = previous_manager

    def test_utility_settings_use_the_regular_dockable_panel_contract(self):
        from infernux.engine.interaction import PanelInteractionDescriptor
        from infernux.engine.ui import (
            BuildSettingsPanel,
            EditorPanel,
            EnvironmentSettingsPanel,
            FloatingEditorPanel,
            InxPackageImportPanel,
            PhysicsLayerMatrixPanel,
            PreferencesPanel,
        )

        expected_ids = {
            BuildSettingsPanel: "build_settings",
            PreferencesPanel: "preferences",
            PhysicsLayerMatrixPanel: "physics_settings",
            EnvironmentSettingsPanel: "environment_settings",
            InxPackageImportPanel: "inxpackage_import",
        }
        for panel_class, expected_id in expected_ids.items():
            assert issubclass(panel_class, EditorPanel)
            assert not issubclass(panel_class, FloatingEditorPanel)
            assert panel_class.WINDOW_TYPE_ID == expected_id
            assert panel_class._panel_menu_path == ""
            assert isinstance(panel_class.PANEL_INTERACTION, PanelInteractionDescriptor)
            assert "render" not in panel_class.__dict__

    def test_close_confirmation_restores_source_during_modal(self):
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.focus_requests = []

            def select_docked_window(
                self, window_id, *, allow_during_modal=False
            ):
                self.focus_requests.append((window_id, allow_during_modal))

        previous = WindowManager._instance
        try:
            engine = Engine()
            manager = _window_manager(engine)
            manager._window_states["particle_graph_editor"] = WindowState.OPEN

            manager.restore_close_confirmation_source("particle_graph_editor")

            assert engine.focus_requests == [("particle_graph_editor", True)]
        finally:
            WindowManager._instance = previous

    def test_close_confirmation_requires_canonical_engine_boundary(self):
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.focus_requests = []

            def select_docked_window(self, window_id):
                self.focus_requests.append(window_id)

        previous = WindowManager._instance
        try:
            engine = Engine()
            manager = _window_manager(engine)
            manager._window_states["particle_graph_editor"] = WindowState.OPEN

            with pytest.raises(TypeError):
                manager.restore_close_confirmation_source("particle_graph_editor")

            assert engine.focus_requests == []
        finally:
            WindowManager._instance = previous

    def test_dynamic_views_keep_panel_type_separate_from_instance_identity(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.editor_panel import EditorPanel
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            @staticmethod
            def register_gui(_window_id, _instance):
                pass

            @staticmethod
            def unregister_gui(_window_id):
                pass

            @staticmethod
            def select_docked_window(_window_id):
                pass

        class FocusContext:
            @staticmethod
            def set_window_focus():
                pass

        previous_manager = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            manager = _window_manager(Engine())
            manager.register_window_type(
                "graph",
                EditorPanel,
                "Graph",
                factory=lambda: EditorPanel("Graph", "factory-default"),
                singleton=False,
            )
            left = manager.open_window("graph", instance_id="graph/left")
            right = manager.open_window("graph", instance_id="graph/right")
            manager.process_pending_actions()

            assert left.panel_type_id == right.panel_type_id == "graph"
            assert left.window_id == "graph/left"
            assert right.window_id == "graph/right"

            right._activate_panel(FocusContext())
            assert focus.snapshot.active_panel_id == "graph"
            assert focus.snapshot.active_view_id == "graph/right"

            manager.close_window("graph/left")
            assert focus.snapshot.active_panel_id == "graph"
            assert focus.snapshot.active_view_id == "graph/right"
        finally:
            FocusService._instance = previous_focus
            WindowManager._instance = previous_manager

    def test_panel_identity_transfer_preserves_an_early_document_binding(self):
        from infernux.engine.interaction import DocumentKind, DocumentRegistry
        from infernux.engine.ui.closable_panel import ClosablePanel

        previous_registry = DocumentRegistry._instance
        try:
            registry = DocumentRegistry()
            document = registry.create(DocumentKind.GENERIC, "Graph")
            panel = ClosablePanel("Graph", "factory-default")
            panel.bind_document(document.document_id)

            panel.set_panel_identity("graph", "graph/asset-a")

            assert registry.document_for_view("factory-default") is None
            assert registry.document_for_view("graph/asset-a") is document
            assert registry.get(document.document_id) is document
        finally:
            DocumentRegistry._instance = previous_registry

    def test_failed_native_registration_rolls_back_panel_interaction_binding(self):
        from infernux.engine.interaction import (
            EditorInteractionCore,
            PanelInteractionDescriptor,
        )
        from infernux.engine.ui.closable_panel import ClosablePanel
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            @staticmethod
            def register_gui(_window_id, _instance):
                raise RuntimeError("native registration failed")

        previous_manager = WindowManager._instance
        core = EditorInteractionCore()
        try:
            manager = _window_manager(Engine(), core.panels)
            core.panels.register_type("graph", PanelInteractionDescriptor())
            manager.register_window_type(
                "graph",
                ClosablePanel,
                "Graph",
                factory=lambda: ClosablePanel("Graph"),
                singleton=False,
            )
            manager.open_window("graph", instance_id="graph/broken")

            with pytest.raises(RuntimeError, match="native registration failed"):
                manager.process_pending_actions()

            assert manager.get_window_state("graph/broken") is WindowState.CLOSED
            assert "graph/broken" not in core.panels._views
        finally:
            core.shutdown()
            WindowManager._instance = previous_manager

    def test_native_pointer_activation_is_distinct_user_focus_history(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.window_manager import WindowManager

        class Panel:
            is_open = True

        previous_manager = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            changes = []
            focus.add_change_listener(changes.append)
            focus.activate_panel(
                "scene_view",
                view_id="scene_view",
                record_history=False,
            )
            changes.clear()

            manager = _window_manager(object())
            manager.register_existing_window("project", Panel())
            callback = manager.native_panel_focus_callback("project")
            callback(True, True)

            assert focus.snapshot.active_panel_id == "project"
            assert len(changes) == 1
            assert changes[0].record_history is True
            assert changes[0].reason == "pointer_panel_activation"
        finally:
            WindowManager._instance = previous_manager
            FocusService._instance = previous_focus

    def test_user_window_command_publishes_reveal_before_native_focus(self):
        from infernux.engine.interaction import (
            EditorInteractionCore,
            PanelInteractionDescriptor,
        )
        from infernux.engine.ui.closable_panel import ClosablePanel
        from infernux.engine.ui.window_manager import WindowManager

        class Engine:
            @staticmethod
            def select_docked_window(_window_id):
                pass

        previous_manager = WindowManager._instance
        core = EditorInteractionCore()
        changes = []
        core.focus.add_change_listener(changes.append)
        try:
            manager = _window_manager(Engine(), core.panels)
            core.panels.register_type("graph", PanelInteractionDescriptor())
            manager.register_window_type(
                "graph",
                ClosablePanel,
                "Graph",
                factory=lambda: ClosablePanel("Graph", "graph"),
            )
            panel = ClosablePanel("Graph", "graph")
            manager.register_existing_window("graph", panel, type_id="graph")
            core.focus.activate_panel(
                "scene_view",
                view_id="scene_view",
                record_history=False,
            )
            changes.clear()

            assert manager.open_window_from_user("graph") is panel

            assert core.focus.snapshot.active_view_id == "graph"
            assert len(changes) == 1
            assert changes[0].reason == "window_open_command"
            assert changes[0].record_history is True
        finally:
            core.shutdown()
            WindowManager._instance = previous_manager

    def test_user_window_reveal_survives_early_focus_projection(self):
        from infernux.engine.interaction import (
            EditorInteractionCore,
            PanelInteractionDescriptor,
        )
        from infernux.engine.ui.closable_panel import ClosablePanel
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.focused = []

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        previous_manager = WindowManager._instance
        core = EditorInteractionCore()
        try:
            engine = Engine()
            manager = _window_manager(engine, core.panels)
            core.panels.register_type("graph", PanelInteractionDescriptor())
            manager.register_window_type(
                "graph",
                ClosablePanel,
                "Graph",
                factory=lambda: ClosablePanel("Graph", "graph"),
            )
            panel = ClosablePanel("Graph", "graph")
            manager.register_existing_window("graph", panel, type_id="graph")
            core.focus.add_listener(manager.project_interaction_focus)
            core.focus.activate_panel(
                "scene_view",
                view_id="scene_view",
                record_history=False,
            )

            assert manager.open_window_from_user("graph") is panel
            assert manager.get_window_state("graph") is WindowState.FOCUSED

            manager.process_pending_actions()

            assert engine.focused == ["graph"]
            assert manager.get_window_state("graph") is WindowState.FOCUSED
        finally:
            core.shutdown()
            WindowManager._instance = previous_manager

    def test_new_user_window_registers_before_focus_projection(self):
        from infernux.engine.interaction import (
            EditorInteractionCore,
            PanelInteractionDescriptor,
        )
        from infernux.engine.ui.closable_panel import ClosablePanel
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.registered = {}
                self.focused = []

            def register_gui(self, window_id, instance):
                self.registered[window_id] = instance

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        previous_manager = WindowManager._instance
        core = EditorInteractionCore()
        changes = []
        core.focus.add_change_listener(changes.append)
        try:
            engine = Engine()
            manager = _window_manager(engine, core.panels)
            core.panels.register_type("graph", PanelInteractionDescriptor())
            manager.register_window_type(
                "graph",
                ClosablePanel,
                "Graph",
                factory=lambda: ClosablePanel("Graph", "graph"),
            )
            core.focus.add_listener(manager.project_interaction_focus)
            core.focus.activate_panel(
                "project",
                view_id="project",
                record_history=False,
            )
            changes.clear()

            panel = manager.open_window_from_user("graph")

            assert manager.get_window_state("graph") is WindowState.OPENING
            assert core.focus.snapshot.active_view_id == "project"
            assert "graph" not in engine.registered

            manager.process_pending_actions()

            assert engine.registered["graph"] is panel
            assert engine.focused == ["graph"]
            assert manager.get_window_state("graph") is WindowState.FOCUSED
            assert core.focus.snapshot.active_view_id == "graph"
            recorded = [change for change in changes if change.record_history]
            assert len(recorded) == 1
            assert recorded[0].reason == "window_open_command"
        finally:
            core.shutdown()
            WindowManager._instance = previous_manager

    @pytest.mark.parametrize("intent", ["focus", "project", "both"])
    def test_opening_window_keeps_registration_when_focus_arrives(self, intent):
        from infernux.engine.interaction import EditorInteractionCore, PanelInteractionDescriptor
        from infernux.engine.ui.closable_panel import ClosablePanel
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.events = []

            def register_gui(self, window_id, instance):
                self.events.append(("register", window_id))

            def select_docked_window(self, window_id):
                assert ("register", window_id) in self.events
                self.events.append(("focus", window_id))

        previous = WindowManager._instance
        core = EditorInteractionCore()
        try:
            engine = Engine()
            manager = _window_manager(engine, core.panels)
            core.panels.register_type("graph", PanelInteractionDescriptor())
            manager.register_window_type("graph", ClosablePanel, "Graph",
                                         factory=lambda: ClosablePanel("Graph", "graph"))
            core.focus.add_listener(manager.project_interaction_focus)
            panel = manager.open_window("graph")
            if intent in {"focus", "both"}:
                manager.focus_window("graph")
            if intent in {"project", "both"}:
                core.focus.activate_panel("graph", view_id="graph", record_history=False)
            assert manager.get_window_state("graph") is WindowState.OPENING
            manager.process_pending_actions()
            assert engine.events.count(("register", "graph")) == 1
            assert "graph" in manager._registered_instance_ids
            assert manager.get_window_instance("graph") is panel
            assert manager.get_window_state("graph") is WindowState.FOCUSED
        finally:
            core.shutdown()
            WindowManager._instance = previous

    def test_user_window_command_does_not_publish_visible_focus(self):
        from infernux.engine.interaction import (
            EditorInteractionCore,
            PanelInteractionDescriptor,
        )
        from infernux.engine.ui.editor_panel import EditorPanel
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.focused = []

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        previous_manager = WindowManager._instance
        core = EditorInteractionCore()
        changes = []
        core.focus.add_change_listener(changes.append)
        try:
            engine = Engine()
            manager = _window_manager(engine, core.panels)
            core.panels.register_type("graph", PanelInteractionDescriptor())
            manager.register_window_type(
                "graph",
                EditorPanel,
                "Graph",
                factory=lambda: EditorPanel("Graph", "graph"),
            )
            panel = EditorPanel("Graph", "graph")
            panel.open()
            panel._content_was_visible = True
            panel._content_visible_previous_frame = True
            manager.register_existing_window("graph", panel, type_id="graph")
            core.focus.activate_panel(
                "scene_view",
                view_id="scene_view",
                record_history=False,
            )
            changes.clear()

            assert manager.open_window_from_user("graph") is panel

            assert manager.get_window_state("graph") is WindowState.FOCUS_REQUESTED
            assert engine.focused == []
            assert core.focus.snapshot.active_view_id == "scene_view"
            assert changes == []
        finally:
            core.shutdown()
            WindowManager._instance = previous_manager

    @pytest.mark.parametrize(
        "visible,was_visible,previous_panel,panel,record_history",
        [
            pytest.param(True, True, "scene_view", "inspector", False, id="visible"),
            pytest.param(False, False, "scene_view", "inspector", True, id="revealed"),
            pytest.param(True, False, "particle_graph_editor", "scene_view", True, id="previous_frame"),
        ],
    )
    def test_native_panel_focus_history(self, visible, was_visible, previous_panel, panel, record_history):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.window_manager import WindowManager

        class Panel:
            is_open = True

            @staticmethod
            def is_content_visible():
                return visible

            @staticmethod
            def was_content_visible():
                return was_visible

        previous_manager = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            changes = []
            focus.add_change_listener(changes.append)
            focus.activate_panel(previous_panel, view_id=previous_panel, record_history=False)
            changes.clear()

            manager = _window_manager(object())
            manager.register_existing_window(panel, Panel())
            manager.native_panel_focus_callback(panel)(True, True)

            assert focus.snapshot.active_panel_id == panel
            assert len(changes) == 1
            assert changes[0].record_history is record_history
            assert changes[0].reason == "pointer_panel_activation"
        finally:
            WindowManager._instance = previous_manager
            FocusService._instance = previous_focus


    def test_revealed_dock_tab_reports_the_tab_it_replaced(self, monkeypatch):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.window_manager import WindowManager

        class Panel:
            is_open = True

            @staticmethod
            def is_content_visible():
                return False

            @staticmethod
            def was_content_visible():
                return False

        previous_manager = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            focus.activate_panel("console", view_id="console", record_history=False)
            changes = []
            focus.add_change_listener(changes.append)

            manager = _window_manager(object())
            manager.register_existing_window("scene_view", Panel())
            manager.register_existing_window("particle_graph_editor", Panel())
            monkeypatch.setattr(
                manager,
                "_native_window_presented_dock_peer",
                lambda window_id: "scene_view"
                if window_id == "particle_graph_editor"
                else "",
            )

            manager.native_panel_focus_callback("particle_graph_editor")(True, True)

            assert len(changes) == 1
            assert changes[0].before.active_view_id == "console"
            assert changes[0].after.active_view_id == "particle_graph_editor"
            assert changes[0].presentation_before_view_id == "scene_view"
        finally:
            WindowManager._instance = previous_manager
            FocusService._instance = previous_focus

    def test_user_window_command_captures_peer_before_dock_selection(
        self, monkeypatch
    ):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.window_manager import WindowManager

        class Engine:
            pass

        class Panel:
            is_open = True

            @staticmethod
            def is_content_visible():
                return True

            @staticmethod
            def was_content_visible():
                return False

        previous_manager = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            focus.activate_panel("console", view_id="console", record_history=False)
            changes = []
            focus.add_change_listener(changes.append)

            manager = _window_manager(Engine())
            manager.register_window_type(
                "particle_graph_editor",
                Panel,
                "Particle Graph",
            )
            manager.register_existing_window("scene_view", Panel())
            manager.register_existing_window("particle_graph_editor", Panel())
            monkeypatch.setattr(
                manager,
                "_native_window_presented_dock_peer",
                lambda window_id: "scene_view"
                if window_id == "particle_graph_editor"
                else "",
            )
            monkeypatch.setattr(
                manager,
                "is_window_content_visible",
                lambda _window_id: False,
            )

            manager.open_window_from_user("particle_graph_editor")

            assert len(changes) == 1
            assert changes[0].before.active_view_id == "console"
            assert changes[0].presentation_before_view_id == "scene_view"
        finally:
            WindowManager._instance = previous_manager
            FocusService._instance = previous_focus


    def test_native_focus_visibility_comes_from_the_event_source_instance(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.window_manager import WindowManager

        class StaleRegisteredPanel:
            is_open = True

            @staticmethod
            def is_content_visible():
                return False

            @staticmethod
            def was_content_visible():
                return False

        class RenderingSourcePanel:
            is_open = True

            @staticmethod
            def is_content_visible():
                return True

            @staticmethod
            def was_content_visible():
                return True

        previous_manager = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            changes = []
            focus.add_change_listener(changes.append)
            focus.activate_panel(
                "inspector",
                view_id="inspector",
                record_history=False,
            )
            changes.clear()

            manager = _window_manager(object())
            manager.register_existing_window("hierarchy", StaleRegisteredPanel())
            source = RenderingSourcePanel()
            callback = manager.native_panel_focus_callback(
                "hierarchy",
                source_instance=source,
            )
            callback(True, True)

            assert focus.snapshot.active_panel_id == "hierarchy"
            assert len(changes) == 1
            assert changes[0].record_history is False
        finally:
            WindowManager._instance = previous_manager
            FocusService._instance = previous_focus

    def test_panel_child_context_can_be_restored_by_its_owner(self):
        from infernux.engine.ui.window_manager import WindowManager

        class Panel:
            is_open = True

            def __init__(self):
                self.restored = []

            def restore_child_context(self, context_id):
                self.restored.append(context_id)
                return context_id in {"", "particle_graph.workspace.parameters"}

        previous = WindowManager._instance
        try:
            manager = _window_manager(object())
            panel = Panel()
            manager.register_existing_window("particle_graph_editor", panel)

            assert manager.restore_panel_child_context(
                "particle_graph_editor",
                "particle_graph.workspace.parameters",
            )
            assert panel.restored == ["particle_graph.workspace.parameters"]
            assert not manager.restore_panel_child_context(
                "particle_graph_editor",
                "particle_graph.workspace.missing",
            )
        finally:
            WindowManager._instance = previous

    def _fresh_manager(self):
        from infernux.engine.ui.window_manager import WindowManager
        mgr = WindowManager.instance()
        return mgr

    def test_singleton(self):
        from infernux.engine.ui.window_manager import WindowManager
        assert WindowManager.instance() is WindowManager.instance()

    def test_window_type_listener_fires_only_on_registration(self):
        from infernux.engine.ui.window_manager import WindowManager

        previous = WindowManager._instance
        try:
            manager = _window_manager(object())
            calls = []
            listener = lambda: calls.append(tuple(manager.get_registered_types()))
            manager.add_type_change_listener(listener)
            manager.register_window_type("sample", object, "Sample")
            manager.remove_type_change_listener(listener)
            manager.register_window_type("second", object, "Second")
            assert calls == [("sample",)]
        finally:
            WindowManager._instance = previous

    def test_explicit_dynamic_window_state_machine(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.registered = {}
                self.focused = []

            def register_gui(self, window_id, instance):
                self.registered[window_id] = instance

            def unregister_gui(self, window_id):
                self.registered.pop(window_id)

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        class Panel:
            def __init__(self):
                self._is_open = False

            @property
            def is_open(self):
                return self._is_open

            def set_window_manager(self, manager):
                self.manager = manager

            def open(self):
                self._is_open = True

        previous = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            engine = Engine()
            manager = _window_manager(engine)
            manager.register_window_type("dynamic", Panel, "Dynamic", factory=Panel)
            panel = manager.open_window("dynamic")
            assert manager.get_window_state("dynamic") is WindowState.OPENING
            manager.process_pending_actions()
            assert manager.get_window_state("dynamic") is WindowState.OPEN
            assert engine.registered["dynamic"] is panel

            assert manager.open_window("dynamic") is panel
            assert manager.get_window_state("dynamic") is WindowState.FOCUS_REQUESTED
            manager.process_pending_actions()
            assert manager.get_window_state("dynamic") is WindowState.FOCUSED
            assert engine.focused == ["dynamic"]
            focus.activate_panel("dynamic", view_id="dynamic", record_history=False)

            manager.close_window("dynamic")
            assert manager.get_window_state("dynamic") is WindowState.CLOSING
            assert focus.snapshot.active_panel_id == ""
            manager.observe_native_panel_focus("dynamic", True, view_id="dynamic")
            assert focus.snapshot.active_panel_id == ""
            manager.process_pending_actions()
            assert manager.get_window_state("dynamic") is WindowState.CLOSED
            assert "dynamic" not in engine.registered
        finally:
            FocusService._instance = previous_focus
            WindowManager._instance = previous

    def test_focus_window_requires_known_open_panel(self):
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.focused = []

            def register_gui(self, _window_id, _instance):
                pass

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        class Panel:
            def __init__(self):
                self.is_open = True

            def set_open(self, value):
                self.is_open = bool(value)

        previous = WindowManager._instance
        try:
            engine = Engine()
            manager = _window_manager(engine)
            panel = Panel()
            manager.register_window_type("game_view", Panel, "Game", factory=Panel)
            manager.register_existing_window("game_view", panel, "game_view")

            manager.focus_window("game_view")
            assert manager.get_window_state("game_view") is WindowState.FOCUS_REQUESTED
            manager.process_pending_actions()
            assert manager.get_window_state("game_view") is WindowState.FOCUSED
            assert engine.focused == ["game_view"]

            manager.close_window("game_view")
            assert manager.get_window_state("game_view") is WindowState.CLOSED
            with pytest.raises(RuntimeError, match="not open"):
                manager.focus_window("game_view")
            with pytest.raises(KeyError, match="Unknown window"):
                manager.focus_window("missing")
        finally:
            WindowManager._instance = previous

    def test_load_state_projects_restored_front_tab_into_focus_service(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.focused = []

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        class Panel:
            def __init__(self):
                self.is_open = True

            def set_open(self, value):
                self.is_open = bool(value)

        previous = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            focus.activate_panel(
                "particle_graph_editor",
                view_id="particle_graph_editor",
                record_history=False,
            )
            engine = Engine()
            manager = _window_manager(engine)
            manager.register_window_type("console", Panel, "Console", factory=Panel)
            manager.register_existing_window("console", Panel(), "console")

            manager.load_state(
                {
                    "open_windows": {"console": True},
                    "active_panel_id": "particle_graph_editor",
                    "project_console_front_id": "console",
                }
            )

            assert engine.focused == ["console"]
            assert manager.get_window_state("console") is WindowState.FOCUSED
            assert focus.snapshot.active_panel_id == "console"
            assert focus.snapshot.active_view_id == "console"
        finally:
            WindowManager._instance = previous
            FocusService._instance = previous_focus

    def test_completed_imgui_focus_reconciles_child_window_to_owning_panel(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Panel:
            def __init__(self):
                self.is_open = True

        previous = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            focus.activate_panel(
                "particle_graph_editor",
                view_id="particle_graph_editor",
                record_history=False,
            )
            manager = _window_manager(object())
            manager.register_existing_window("particle_graph_editor", Panel())
            manager.register_existing_window("project", Panel())

            child_id = "project/##project_browser/##file_grid"
            assert manager.resolve_native_gui_panel_id(child_id) == "project"
            assert manager.observe_native_gui_window_focus(child_id)
            assert focus.snapshot.active_panel_id == "project"
            assert manager.get_window_state("project") is WindowState.FOCUSED

            assert not manager.observe_native_gui_window_focus("##SaveResourceModal")
            assert focus.snapshot.active_panel_id == "project"
            assert not manager.observe_native_gui_window_focus(child_id)
        finally:
            WindowManager._instance = previous
            FocusService._instance = previous_focus

    def test_completed_imgui_focus_rebinds_document_for_already_focused_panel(self):
        from infernux.engine.interaction import (
            DocumentKind,
            DocumentRegistry,
            FocusService,
        )
        from infernux.engine.ui.window_manager import WindowManager

        class Panel:
            def __init__(self):
                self.is_open = True

        previous = WindowManager._instance
        previous_focus = FocusService._instance
        previous_registry = DocumentRegistry._instance
        try:
            focus = FocusService()
            registry = DocumentRegistry()
            manager = _window_manager(object())
            manager.register_existing_window("inspector", Panel())

            child_id = "inspector/##asset_inspector"
            assert manager.observe_native_gui_window_focus(child_id)
            assert focus.snapshot.active_panel_id == "inspector"
            assert focus.snapshot.active_document_id == ""

            document = registry.create(DocumentKind.MATERIAL, "Material")
            registry.attach_view(document.document_id, "inspector")

            # The native panel did not change, but its active document did.
            assert manager.observe_native_gui_window_focus(child_id)
            assert focus.snapshot.active_panel_id == "inspector"
            assert focus.snapshot.active_document_id == document.document_id
            assert not manager.observe_native_gui_window_focus(child_id)
        finally:
            WindowManager._instance = previous
            DocumentRegistry._instance = previous_registry
            FocusService._instance = previous_focus

    def test_dynamic_window_locator_restores_instance_and_focuses_after_register(self):
        from infernux.engine.interaction import ContextRestoreStatus, FocusService
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.registered = {}
                self.focused = []

            def register_gui(self, window_id, instance):
                self.registered[window_id] = instance

            def unregister_gui(self, window_id):
                self.registered.pop(window_id)

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        class Panel:
            def __init__(self):
                self._is_open = False

            @property
            def is_open(self):
                return self._is_open

            def set_open(self, value):
                self._is_open = bool(value)

            def open(self):
                self._is_open = True

            def set_window_manager(self, manager):
                self.manager = manager

        previous = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            focus_changes = []
            focus.add_change_listener(focus_changes.append)
            engine = Engine()
            manager = _window_manager(engine)
            manager.register_window_type("graph", Panel, "Graph", factory=Panel)
            manager.open_window("graph", instance_id="graph/asset-a")
            manager.process_pending_actions()
            locator = manager.locate_window("graph/asset-a")
            assert locator.window_id == "graph/asset-a"
            assert locator.type_id == "graph"

            manager.close_window("graph/asset-a")
            manager.process_pending_actions()
            assert manager.get_window_state("graph/asset-a") is WindowState.CLOSED

            assert manager.restore_window(locator) is ContextRestoreStatus.PENDING
            assert manager.get_window_state("graph/asset-a") is WindowState.OPENING
            manager.process_pending_actions()

            assert manager.get_window_state("graph/asset-a") is WindowState.FOCUSED
            assert "graph/asset-a" in engine.registered
            assert engine.focused == ["graph/asset-a"]
            assert manager.restore_window(locator) is ContextRestoreStatus.PENDING

            manager.observe_native_panel_focus(
                "graph/asset-a", True, view_id="graph/asset-a"
            )
            assert manager.restore_window(locator) is ContextRestoreStatus.READY
            assert [change.record_history for change in focus_changes] == [False]
        finally:
            WindowManager._instance = previous
            FocusService._instance = previous_focus

    def test_visible_window_locator_does_not_steal_focus(self):
        from infernux.engine.interaction import ContextRestoreStatus, FocusService
        from infernux.engine.ui.window_manager import WindowManager

        class Engine:
            def __init__(self):
                self.focused = []

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        class Panel:
            is_open = True

            @staticmethod
            def is_content_visible():
                return True

            @staticmethod
            def was_content_visible():
                return True

        previous = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            focus.activate_panel(
                "scene_view",
                view_id="scene_view",
                record_history=False,
            )
            engine = Engine()
            manager = _window_manager(engine)
            manager.register_existing_window("inspector", Panel())

            locator = manager.locate_window("inspector")
            assert manager.is_window_content_visible("inspector")
            assert manager.was_window_content_visible("inspector")
            assert manager.restore_window(locator) is ContextRestoreStatus.READY
            assert focus.snapshot.active_view_id == "scene_view"
            assert engine.focused == []
        finally:
            WindowManager._instance = previous
            FocusService._instance = previous_focus

    def test_hidden_dock_tab_still_requests_focus(self):
        from infernux.engine.interaction import ContextRestoreStatus, FocusService
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.focused = []

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        class Panel:
            is_open = True

            @staticmethod
            def is_content_visible():
                return False

            @staticmethod
            def was_content_visible():
                return False

        previous = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            FocusService()
            engine = Engine()
            manager = _window_manager(engine)
            manager.register_existing_window("console", Panel())
            locator = manager.locate_window("console")

            assert manager.restore_window(locator) is ContextRestoreStatus.PENDING
            assert manager.get_window_state("console") is WindowState.FOCUS_REQUESTED
            manager.process_pending_actions()
            assert engine.focused == ["console"]
        finally:
            WindowManager._instance = previous
            FocusService._instance = previous_focus

    def test_hidden_dock_tab_does_not_restore_from_logical_focus_alone(self):
        from infernux.engine.interaction import ContextRestoreStatus, FocusService
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.focused = []

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        class Panel:
            is_open = True
            presented = False

            def is_content_visible(self):
                return self.presented

            def was_content_visible(self):
                return self.presented

        previous = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            engine = Engine()
            panel = Panel()
            manager = _window_manager(engine)
            manager.register_existing_window("scene_view", panel)
            locator = manager.locate_window("scene_view")

            # Context restore applies its logical snapshot before asking the
            # WindowManager to reveal the recorded dock tab.
            focus.activate_panel(
                "scene_view",
                view_id="scene_view",
                record_history=False,
            )
            manager._window_states["scene_view"] = WindowState.FOCUSED

            assert manager.restore_window(locator) is ContextRestoreStatus.PENDING
            assert manager.get_window_state("scene_view") is WindowState.FOCUS_REQUESTED
            manager.process_pending_actions()
            assert engine.focused == ["scene_view"]

            assert manager.restore_window(locator) is ContextRestoreStatus.READY
        finally:
            WindowManager._instance = previous
            FocusService._instance = previous_focus

    def test_builtin_window_locator_restores_without_dynamic_type_registration(self):
        from infernux.engine.interaction import ContextRestoreStatus, FocusService
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.focused = []

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        class Panel:
            def __init__(self):
                self._is_open = True

            @property
            def is_open(self):
                return self._is_open

            def set_open(self, value):
                self._is_open = bool(value)

        previous = WindowManager._instance
        previous_focus = FocusService._instance
        try:
            focus = FocusService()
            engine = Engine()
            panel = Panel()
            manager = _window_manager(engine)
            manager.register_existing_window("hierarchy", panel)
            locator = manager.locate_window("hierarchy")

            manager.close_window("hierarchy")
            assert manager.get_window_state("hierarchy") is WindowState.CLOSED
            assert panel.is_open is False

            assert manager.restore_window(locator) is ContextRestoreStatus.PENDING
            assert manager.get_window_state("hierarchy") is WindowState.FOCUS_REQUESTED
            assert panel.is_open is True
            manager.process_pending_actions()
            manager.observe_native_panel_focus(
                "hierarchy", True, view_id="hierarchy"
            )

            assert manager.restore_window(locator) is ContextRestoreStatus.READY
            assert engine.focused == ["hierarchy"]
        finally:
            WindowManager._instance = previous
            FocusService._instance = previous_focus

    def test_builtin_window_closes_without_unregistering(self):
        from infernux.engine.interaction import DocumentKind, DocumentRegistry
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.registered = {}
                self.focused = []

            def register_gui(self, window_id, instance):
                self.registered[window_id] = instance

            def unregister_gui(self, window_id):
                raise AssertionError("builtin window must remain registered")

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        class Panel:
            def __init__(self):
                self.is_open = True

            def set_open(self, value):
                self.is_open = value

        previous = WindowManager._instance
        previous_registry = DocumentRegistry._instance
        try:
            registry = DocumentRegistry()
            engine = Engine()
            manager = _window_manager(engine)
            panel = Panel()
            engine.registered["builtin"] = panel
            manager.register_window_type("builtin", Panel, "Builtin", factory=Panel)
            manager.register_existing_window("builtin", panel, "builtin")
            document = registry.create(DocumentKind.SCENE, "Scene")
            registry.attach_view(document.document_id, "builtin")

            manager.close_window("builtin")
            assert manager.get_window_state("builtin") is WindowState.CLOSED
            assert engine.registered["builtin"] is panel
            assert panel.is_open is False
            assert registry.document_for_view("builtin") is document

            assert manager.open_window("builtin") is panel
            manager.process_pending_actions()
            assert manager.get_window_state("builtin") is WindowState.FOCUSED
            assert panel.is_open is True
            assert engine.focused == ["builtin"]
        finally:
            DocumentRegistry._instance = previous_registry
            WindowManager._instance = previous

    def test_destroyed_dynamic_window_releases_its_document_view(self):
        from infernux.engine.interaction import DocumentKind, DocumentRegistry
        from infernux.engine.ui.closable_panel import ClosablePanel
        from infernux.engine.ui.window_manager import WindowManager

        class Engine:
            @staticmethod
            def register_gui(_window_id, _instance):
                pass

            @staticmethod
            def unregister_gui(_window_id):
                pass

        previous_manager = WindowManager._instance
        previous_registry = DocumentRegistry._instance
        try:
            registry = DocumentRegistry()
            manager = _window_manager(Engine())
            manager.register_window_type(
                "graph", ClosablePanel, "Graph", factory=lambda: ClosablePanel("Graph", "graph")
            )
            panel = manager.open_window("graph")
            manager.process_pending_actions()
            document = registry.create(DocumentKind.PARTICLE_GRAPH, "Graph")
            panel.bind_document(document.document_id)

            manager.close_window("graph")

            assert panel.document_id == ""
            assert registry.document_for_view("graph") is None
            assert registry.get(document.document_id) is None
        finally:
            DocumentRegistry._instance = previous_registry
            WindowManager._instance = previous_manager

    def test_reopened_dynamic_singleton_restores_its_document_binding(self):
        from infernux.engine.interaction import DocumentKind, DocumentRegistry
        from infernux.engine.ui.closable_panel import ClosablePanel
        from infernux.engine.ui.window_manager import WindowManager

        class Engine:
            @staticmethod
            def register_gui(_window_id, _instance):
                pass

            @staticmethod
            def unregister_gui(_window_id):
                pass

        previous_manager = WindowManager._instance
        previous_registry = DocumentRegistry._instance
        try:
            registry = DocumentRegistry()
            manager = _window_manager(Engine())
            manager.register_window_type(
                "graph",
                ClosablePanel,
                "Graph",
                factory=lambda: ClosablePanel("Graph", "graph"),
            )
            panel = manager.open_window("graph")
            manager.process_pending_actions()
            document = registry.create(DocumentKind.PARTICLE_GRAPH, "Graph")
            panel.bind_document(document.document_id)
            original_stable_id = document.stable_id

            manager.close_window("graph")
            manager.process_pending_actions()
            assert panel.document_id == ""

            reopened = manager.open_window("graph")
            manager.process_pending_actions()

            assert reopened is panel
            assert reopened.document_id == document.document_id
            restored = registry.require(reopened.document_id)
            assert restored.stable_id == original_stable_id
            assert registry.document_for_view("graph") is restored
        finally:
            DocumentRegistry._instance = previous_registry
            WindowManager._instance = previous_manager

    def test_dynamic_window_reopen_waits_for_native_close_transaction(self):
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.registered = {}
                self.events = []

            def register_gui(self, window_id, instance):
                assert window_id not in self.registered
                self.registered[window_id] = instance
                self.events.append(("register", window_id))

            def unregister_gui(self, window_id):
                self.registered.pop(window_id)
                self.events.append(("unregister", window_id))

        class Panel:
            def __init__(self):
                self.is_open = True

            def open(self):
                self.is_open = True

            def set_open(self, value):
                self.is_open = bool(value)

            def request_close(self):
                return True

        previous = WindowManager._instance
        try:
            engine = Engine()
            manager = _window_manager(engine)
            manager.register_window_type("graph", Panel, "Graph", factory=Panel)
            panel = manager.open_window("graph")
            manager.process_pending_actions()
            assert manager.get_window_state("graph") is WindowState.OPEN

            manager.close_window("graph")
            assert manager.get_window_state("graph") is WindowState.CLOSING
            assert manager.open_window("graph") is panel
            assert manager.get_window_state("graph") is WindowState.CLOSING

            manager.process_pending_actions()

            assert engine.events == [
                ("register", "graph"),
                ("unregister", "graph"),
                ("register", "graph"),
            ]
            assert manager.get_window_state("graph") is WindowState.OPEN
            assert manager.get_window_instance("graph") is panel
            assert panel.is_open is True
        finally:
            WindowManager._instance = previous

    def test_user_reopen_during_close_publishes_focus_after_registration(self, monkeypatch):
        from infernux.engine.interaction import (
            EditorInteractionCore,
            PanelInteractionDescriptor,
        )
        from infernux.engine.ui.closable_panel import ClosablePanel
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            def __init__(self):
                self.registered = {}
                self.focused = []

            def register_gui(self, window_id, instance):
                assert window_id not in self.registered
                self.registered[window_id] = instance

            def unregister_gui(self, window_id):
                self.registered.pop(window_id)

            def select_docked_window(self, window_id):
                self.focused.append(window_id)

        previous_manager = WindowManager._instance
        core = EditorInteractionCore()
        changes = []
        core.focus.add_change_listener(changes.append)
        try:
            monkeypatch.setattr(
                WindowManager,
                "_native_window_content_visible",
                staticmethod(lambda _window_id: False),
            )
            monkeypatch.setattr(
                WindowManager,
                "_native_window_presented_dock_peer",
                staticmethod(lambda _window_id: "project"),
            )
            engine = Engine()
            manager = _window_manager(engine, core.panels)
            core.panels.register_type("graph", PanelInteractionDescriptor())
            manager.register_window_type(
                "graph",
                ClosablePanel,
                "Graph",
                factory=lambda: ClosablePanel("Graph", "graph"),
            )
            panel = manager.open_window("graph")
            manager.process_pending_actions()
            core.focus.activate_panel(
                "project",
                view_id="project",
                record_history=False,
            )
            changes.clear()

            manager.close_window("graph")
            assert manager.get_window_state("graph") is WindowState.CLOSING
            assert manager.open_window_from_user("graph") is panel
            assert core.focus.snapshot.active_view_id == "project"

            manager.process_pending_actions()

            assert manager.get_window_state("graph") is WindowState.FOCUSED
            assert engine.registered["graph"] is panel
            assert engine.focused == ["graph"]
            assert core.focus.snapshot.active_view_id == "graph"
            recorded = [change for change in changes if change.record_history]
            assert len(recorded) == 1
            assert recorded[0].reason == "window_open_command"
        finally:
            core.shutdown()
            WindowManager._instance = previous_manager

    def test_window_menu_close_respects_panel_close_deferral(self):
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        class Engine:
            @staticmethod
            def unregister_gui(_window_id):
                raise AssertionError("deferred window must remain registered")

        class Panel:
            def __init__(self):
                self._is_open = True
                self.close_requests = 0

            @property
            def is_open(self):
                return self._is_open

            def request_close(self):
                self.close_requests += 1
                return False

        previous = WindowManager._instance
        try:
            manager = _window_manager(Engine())
            panel = Panel()
            manager._window_states["dirty"] = WindowState.OPEN
            manager._window_instances["dirty"] = panel
            manager._registered_instance_ids.add("dirty")

            manager.close_window("dirty")

            assert panel.close_requests == 1
            assert panel.is_open is True
            assert manager.get_window_state("dirty") is WindowState.OPEN
        finally:
            WindowManager._instance = previous


class TestSceneViewMath:
    def test_dot_and_cross(self):
        from infernux.engine.ui._scene_view_math import SceneViewMathMixin as m
        assert m._dot3((1, 0, 0), (0, 1, 0)) == 0
        assert m._dot3((1, 2, 3), (4, 5, 6)) == 32
        cx, cy, cz = m._cross3((1, 0, 0), (0, 1, 0))
        assert (cx, cy, cz) == (0, 0, 1)


class TestIGUIFilters:
    def test_searchable_combo_filter_semantics(self):
        labels = ["MeshRenderer", "SkinnedMeshRenderer", "Rigidbody", "BoxCollider"]
        filt = "mesh"
        filtered = [l for l in labels if filt.lower() in l.lower()]
        assert filtered == ["MeshRenderer", "SkinnedMeshRenderer"]


class TestUICanvasRaycast:
    def test_inactive_hierarchy_does_not_receive_raycast(self, scene):
        from infernux.ui import UICanvas, UIButton

        canvas = UICanvas()
        root = scene.create_game_object('Canvas')
        root.add_py_component(canvas)
        controls = []
        for index in range(2):
            obj = scene.create_game_object(f'Button {index}')
            obj.set_parent(root)
            button = UIButton()
            obj.add_py_component(button)
            button.width = button.height = 100
            button.set_rect(0, 0, 100, 100, 1920, 1080)
            controls.append(button)
        visible, hidden_on_top = controls
        hidden_on_top.game_object.active = False

        assert canvas.raycast(50.0, 50.0) is visible
        assert canvas.raycast_all(50.0, 50.0) == [visible]


class TestUICanvasCollectionCache:
    class _Root:
        def __init__(self, canvas):
            self.id = id(self)
            self._components = [] if canvas is None else [canvas]

        def get_py_components(self):
            return list(self._components)

        @staticmethod
        def get_children():
            return []

    class _Scene:
        name = "SameName"
        structure_version = 7

        def __init__(self, canvas):
            self._root = TestUICanvasCollectionCache._Root(canvas)
            self.world_id = id(self)
            self.temporal_discontinuity_revision = 0
            self.root_queries = 0
            if canvas is not None:
                canvas._set_game_object(self._root)
                canvas._native_scene = self
                canvas._try_get_game_object = lambda: self._root

        def get_root_objects(self):
            self.root_queries += 1
            return [self._root]

    def test_same_name_and_version_do_not_alias_distinct_scenes(self):
        from infernux.ui import UICanvas
        from infernux.ui.ui_canvas_utils import collect_canvases, invalidate_canvas_cache

        first_canvas = UICanvas()
        second_canvas = UICanvas()
        first_scene = self._Scene(first_canvas)
        second_scene = self._Scene(second_canvas)

        invalidate_canvas_cache()
        assert collect_canvases(first_scene) == [first_canvas]
        assert collect_canvases(second_scene) == [second_canvas]

    def test_temporal_discontinuity_rebuilds_same_scene_canvas_cache(self):
        from infernux.ui import UICanvas
        from infernux.ui.ui_canvas_utils import collect_canvases, invalidate_canvas_cache

        first_canvas = UICanvas()
        second_canvas = UICanvas()
        scene = self._Scene(first_canvas)

        invalidate_canvas_cache()
        assert collect_canvases(scene) == [first_canvas]

        first_canvas._set_game_object(None)
        scene._root = self._Root(second_canvas)
        second_canvas._set_game_object(scene._root)
        second_canvas._native_scene = scene
        second_canvas._try_get_game_object = lambda: scene._root
        scene.temporal_discontinuity_revision += 1
        assert collect_canvases(scene) == [second_canvas]

    def test_runtime_collection_includes_persistent_scene_canvases(self):
        from infernux.ui import UICanvas
        from infernux.ui.ui_canvas_utils import (
            collect_sorted_runtime_canvases,
            invalidate_canvas_cache,
        )

        active_canvas = UICanvas()
        active_canvas.sort_order = 10
        persistent_canvas = UICanvas()
        persistent_canvas.sort_order = 5

        invalidate_canvas_cache()
        canvases = collect_sorted_runtime_canvases(
            self._Scene(active_canvas),
            self._Scene(persistent_canvas),
        )

        assert canvases == [persistent_canvas, active_canvas]

    def test_runtime_collection_with_owners_includes_persistent_scene(self):
        from infernux.ui import UICanvas
        from infernux.ui.ui_canvas_utils import (
            collect_runtime_canvases_with_go,
            invalidate_canvas_cache,
        )

        active_canvas = UICanvas()
        persistent_canvas = UICanvas()
        active_scene = self._Scene(active_canvas)
        persistent_scene = self._Scene(persistent_canvas)

        invalidate_canvas_cache()
        pairs = collect_runtime_canvases_with_go(active_scene, persistent_scene)

        assert [canvas for _, canvas in pairs] == [active_canvas, persistent_canvas]
        assert [owner for owner, _ in pairs] == [
            active_scene._root,
            persistent_scene._root,
        ]

    def test_runtime_collection_does_not_duplicate_same_scene(self):
        from infernux.ui import UICanvas
        from infernux.ui.ui_canvas_utils import (
            collect_runtime_canvases_with_go,
            invalidate_canvas_cache,
        )

        canvas = UICanvas()
        scene = self._Scene(canvas)

        invalidate_canvas_cache()
        assert collect_runtime_canvases_with_go(scene, scene) == [
            (scene._root, canvas),
        ]

    def test_runtime_collection_tracks_dynamic_canvas_attach_and_detach(self):
        from infernux.ui import UICanvas
        import infernux.ui.ui_canvas_utils as canvas_utils

        scene = self._Scene(None)
        canvas_utils.invalidate_canvas_cache()
        assert canvas_utils.collect_runtime_canvases_with_go(scene) == []

        canvas = UICanvas()
        scene._root._components.append(canvas)
        canvas._set_game_object(scene._root)
        canvas._native_scene = scene
        canvas._try_get_game_object = lambda: scene._root
        assert canvas_utils.collect_runtime_canvases_with_go(scene) == [
            (scene._root, canvas),
        ]

        scene._root._components.remove(canvas)
        canvas._set_game_object(None)
        assert canvas_utils.collect_runtime_canvases_with_go(scene) == []

    def test_runtime_collection_ignores_unrelated_scene_structure_changes(self):
        from infernux.ui import UICanvas
        from infernux.ui.ui_canvas_utils import (
            collect_sorted_runtime_canvases,
            invalidate_canvas_cache,
        )

        canvas = UICanvas()
        scene = self._Scene(canvas)
        invalidate_canvas_cache()

        assert collect_sorted_runtime_canvases(scene) == [canvas]
        assert scene.root_queries == 0

        scene.structure_version += 100_000
        assert collect_sorted_runtime_canvases(scene) == [canvas]
        assert scene.root_queries == 0


def test_screen_ui_rect_cache_survives_frames_and_invalidates_on_geometry_change(scene, monkeypatch):
    from infernux.ui import UICanvas, UIText
    from infernux.ui.inx_ui_screen_component import clear_rect_cache

    element = UIText()
    root = scene.create_game_object('Rect cache Canvas')
    root.add_py_component(UICanvas())
    child = scene.create_game_object('Rect cache Text')
    child.set_parent(root)
    child.add_py_component(element)
    calls = 0
    def counted_parent_rect(width, height):
        nonlocal calls
        calls += 1
        return (0.0, 0.0, width, height)

    monkeypatch.setattr(element, "_get_parent_world_rect", counted_parent_rect)
    clear_rect_cache(("scene", 1))

    first = element.get_rect(1920.0, 1080.0)
    assert element.get_rect(1920.0, 1080.0) == first
    assert calls == 1

    element.width = 320.0
    assert element.get_rect(1920.0, 1080.0)[2] == 320.0
    assert calls == 2


def test_runtime_ui_packet_cache_reuses_static_text_and_tracks_mutation(monkeypatch):
    import infernux.ui.ui_render_dispatch as dispatch_module
    from infernux.ui.ui_command_packets import UICommandPackets
    from infernux.ui import UIText

    class Renderer:
        def __init__(self):
            self.text_calls = []
            self.recording = None

        def begin_command_packet(self):
            self.recording = []

        def end_command_packet(self):
            packet, self.recording = self.recording, None
            return tuple(packet)

        def abort_command_packet(self):
            self.recording = None

        def append_command_packets(self, packets):
            for packet in packets:
                self.text_calls.extend(packet)

        def add_text(self, *args):
            self.recording.append(args)

    element = UIText()
    renderer = Renderer()
    extract_calls = 0
    original_extract = dispatch_module.extract_common

    def counted_extract(value, **kwargs):
        nonlocal extract_calls
        extract_calls += 1
        return original_extract(value, **kwargs)

    monkeypatch.setattr(dispatch_module, "extract_common", counted_extract)
    kwargs = {
        "renderer": renderer,
        "ui_list": 0,
        "sx": 10.0,
        "sy": 20.0,
        "sw": 160.0,
        "sh": 40.0,
        "ref_w": 1920.0,
        "ref_h": 1080.0,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "text_scale": 1.0,
        "get_tex_id": lambda _path: 0,
    }

    renderer.measure_text = lambda *_: (160.0, 40.0)
    packets = UICommandPackets()
    def draw(current, target):
        assert dispatch_module.dispatch(current, "runtime", **{**kwargs, "renderer": target})
    packets.submit_elements((element,), renderer, draw)
    packets.submit_elements((element,), renderer, draw)
    packets.flush(renderer)
    assert extract_calls == 1
    assert len(renderer.text_calls) == 2

    element.text = "Updated"
    packets.submit_elements((element,), renderer, draw)
    packets.flush(renderer)
    assert extract_calls == 2
    assert renderer.text_calls[-1][5] == "Updated"


def test_runtime_text_packet_tracks_parent_group_alpha(scene):
    from infernux.ui import UIText, UIGroup
    from infernux.ui.ui_render_dispatch import dispatch

    parent = scene.create_game_object("Text group")
    group = UIGroup()
    parent.add_py_component(group)
    child = scene.create_game_object("Text")
    child.set_parent(parent)
    text = UIText()
    child.add_py_component(text)
    packets = []

    class Renderer:
        def add_text(self, *args):
            packets.append(args)

    renderer = Renderer()
    for alpha in (1., .25, .5):
        group.alpha = alpha
        dispatch(text, "runtime", renderer=renderer, ui_list=0,
                 sx=0., sy=0., sw=100., sh=40., ref_w=1920., ref_h=1080.,
                 scale_x=1., scale_y=1., text_scale=1., get_tex_id=lambda _: 0)
        assert packets[-1][9] == pytest.approx(alpha)


def test_text_overflow_clip_reaches_runtime_draw_packet():
    import infernux.ui.ui_render_dispatch as dispatch_module
    from infernux.ui import UIText, TextOverflow

    class Renderer:
        def __init__(self):
            self.text_calls = []

        def add_text(self, *args):
            self.text_calls.append(args)

    element = UIText()
    element.overflow = TextOverflow.Clip
    renderer = Renderer()
    assert dispatch_module.dispatch(
        element,
        "runtime",
        renderer=renderer,
        ui_list=0,
        sx=10.0,
        sy=20.0,
        sw=160.0,
        sh=40.0,
        ref_w=1920.0,
        ref_h=1080.0,
        scale_x=1.0,
        scale_y=1.0,
        text_scale=1.0,
        get_tex_id=lambda _path: 0,
    )
    assert renderer.text_calls[-1][-1] is True


def test_explicit_fallback_font_chain_reaches_runtime_draw_packet(monkeypatch):
    import infernux.ui.ui_render_dispatch as dispatch_module
    from infernux.application import Application
    from infernux.core.asset_ref import create_asset_ref
    from infernux.engine.project_context import set_runtime_asset_resolver
    from infernux.ui import UIText, TextOverflow

    class Renderer:
        def __init__(self):
            self.text_calls = []

        def add_text(self, *args):
            self.text_calls.append(args)

    element = UIText()
    element.overflow = TextOverflow.Clip
    paths = {
        "font-cjk": "Assets/Fonts/CJK.ttf",
        "font-emoji": "Assets/Fonts/Emoji.ttf",
    }
    monkeypatch.setattr(Application, "is_player", staticmethod(lambda: True))
    set_runtime_asset_resolver(lambda guid: paths.get(guid))
    element.fallback_fonts = [
        create_asset_ref("Font", guid=guid, path_hint="stale/path.ttf")
        for guid in paths
    ]
    renderer = Renderer()
    try:
        assert dispatch_module.dispatch(
            element,
            "runtime",
            renderer=renderer,
            ui_list=0,
            sx=10.0,
            sy=20.0,
            sw=160.0,
            sh=40.0,
            ref_w=1920.0,
            ref_h=1080.0,
            scale_x=1.0,
            scale_y=1.0,
            text_scale=1.0,
            get_tex_id=lambda _path: 0,
        )
    finally:
        set_runtime_asset_resolver(None)
    assert renderer.text_calls[-1][-2] is True
    assert renderer.text_calls[-1][-1] == [
        "Assets/Fonts/CJK.ttf",
        "Assets/Fonts/Emoji.ttf",
    ]


def test_text_font_guids_resolve_through_active_player_catalog(monkeypatch):
    import infernux.ui.ui_render_dispatch as dispatch_module
    from infernux.application import Application
    from infernux.core.asset_ref import create_asset_ref
    from infernux.engine.project_context import set_runtime_asset_resolver
    from infernux.ui import UIText

    resolved = {
        "font-primary": "Player/Library/Artifacts/Blob/primary.ttf",
        "font-cjk": "Player/Library/Artifacts/Blob/cjk.ttf",
    }
    monkeypatch.setattr(Application, "is_player", staticmethod(lambda: True))
    set_runtime_asset_resolver(lambda guid: resolved.get(guid))
    try:
        element = UIText()
        element.font = create_asset_ref(
            "Font", guid="font-primary", path_hint="Assets/Fonts/Old.ttf"
        )
        element.fallback_fonts = [
            create_asset_ref(
                "Font", guid="font-cjk", path_hint="Assets/Fonts/OldCJK.ttf"
            )
        ]
        attrs = dispatch_module._extract_text_attrs(element)
    finally:
        set_runtime_asset_resolver(None)

    assert attrs["font_path"] == resolved["font-primary"]
    assert attrs["fallback_font_paths"] == [resolved["font-cjk"]]


@pytest.mark.parametrize("component_type", ["UIText", "UIButton"])
def test_ui_font_fields_serialize_guid_references_and_ignore_path_schema(component_type):
    from infernux.core.asset_ref import create_asset_ref
    from infernux.ui import UIButton, UIText

    component = {"UIText": UIText, "UIButton": UIButton}[component_type]()
    component.font = create_asset_ref(
        "Font", guid="font-primary", path_hint="Assets/Fonts/Primary.ttf"
    )
    component.fallback_fonts = [
        create_asset_ref(
            "Font", guid="font-cjk", path_hint="Assets/Fonts/CJK.ttf"
        )
    ]

    document = component._serialize_fields_document()
    assert document["font"] == {
        "$type": "asset_ref",
        "asset_type": "Font",
        "guid": "font-primary",
    }
    assert document["fallback_fonts"][0]["guid"] == "font-cjk"
    assert "path_hint" not in document["fallback_fonts"][0]
    assert "font_path" not in document
    assert "fallback_font_paths" not in document

    component._deserialize_fields_document({
        "font_path": "Assets/Fonts/Old.ttf",
        "fallback_font_paths": ["Assets/Fonts/OldFallback.ttf"],
    })
    assert component._serialize_fields_document() == document


def test_runtime_ui_revision_is_stable_and_tracks_visual_state(scene):
    from infernux.ui.ui_render_dispatch import runtime_ui_revision
    from infernux.ui.ui_render_revision import mark_runtime_ui_dirty
    from infernux.ui import UICanvas, UIButton

    owner = scene.create_game_object("Canvas")
    canvas = UICanvas()
    owner.add_py_component(canvas)
    button = scene.create_game_object("Button")
    button.set_parent(owner)
    element = UIButton()
    button.add_py_component(element)
    canvases = [canvas]

    first = runtime_ui_revision(scene, canvases, 1280, 720, 2)
    assert runtime_ui_revision(scene, canvases, 1280, 720, 2) == first

    element._current_state = "hovered"
    mark_runtime_ui_dirty()
    assert runtime_ui_revision(scene, canvases, 1280, 720, 2) != first

    element._current_state = "normal"
    mark_runtime_ui_dirty()
    assert runtime_ui_revision(scene, canvases, 1280, 720, 2) != first


def test_canvas_reference_resolution_scales_without_center_crop():
    from infernux.ui import UICanvas

    canvas = UICanvas()
    canvas.reference_width = 1920
    canvas.reference_height = 1080
    canvas.match_width_or_height = 0.5

    wide_scale_x, wide_scale_y, _ = canvas.compute_scale(3200, 1440)
    wide_width, wide_height = canvas.compute_logical_size(3200, 1440)
    assert wide_scale_x == pytest.approx(wide_scale_y)
    assert wide_width * wide_scale_x == pytest.approx(3200.0)
    assert wide_height * wide_scale_y == pytest.approx(1440.0)

    portrait_scale_x, portrait_scale_y, _ = canvas.compute_scale(824, 1830)
    portrait_width, portrait_height = canvas.compute_logical_size(824, 1830)
    assert portrait_scale_x == pytest.approx(portrait_scale_y)
    assert portrait_width * portrait_scale_x == pytest.approx(824.0)
    assert portrait_height * portrait_scale_y == pytest.approx(1830.0)
