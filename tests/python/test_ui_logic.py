"""Pure-logic tests for the editor UI layer (no ImGui frame required).

Covers: theme color math + C++ SSOT override, viewport math, window manager
state machine, panel state persistence, scene-view math helpers, UI-editor
geometry, and selection manager behavior.
"""

from __future__ import annotations


import pytest as pytest


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


def test_ui_resize_preparation_and_commit_preserve_untouched_layout_axis():
    from infernux.engine.ui.ui_rect_manipulation import (
        apply_layout_size,
        layout_snapshot,
        prepare_layout_resize,
    )
    from infernux.ui import UIText
    from infernux.ui.enums import TextResizeMode, UILayoutSizing

    text = UIText()
    text.width = 120.0
    text.height = 48.0
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

    text.resize_mode = TextResizeMode.AutoHeight
    apply_layout_size(text, width=260.0)
    assert text.width == 260.0 and text.height == 48.0
    assert text.width_sizing == UILayoutSizing.Fixed
    assert text.height_sizing == UILayoutSizing.Hug
    assert text.resize_mode == TextResizeMode.FixedSize


def test_engine_gui_registration_rejects_duplicate_renderable_identity():
    from types import SimpleNamespace

    from infernux.engine.engine import Engine

    calls = []
    engine = Engine.__new__(Engine)
    engine._engine = SimpleNamespace(
        register_gui_renderable=lambda name, renderable, priority: calls.append((name, renderable, priority))
    )
    existing = object()
    engine._gui_objects = {"scene_view": existing}

    with pytest.raises(RuntimeError, match="already registered"):
        engine.register_gui("scene_view", object())

    assert calls == []
    assert engine._gui_objects["scene_view"] is existing


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


class TestViewportInfo:
    def test_viewport_dimensions_coordinate_conversion_and_hit_boundaries(self):
        from infernux.engine.ui.viewport_utils import ViewportInfo

        viewport = ViewportInfo(image_min_x=100, image_min_y=50, image_max_x=300, image_max_y=250)
        assert (viewport.width, viewport.height) == (200, 200)
        assert viewport.mouse_local(_FakeCtx(150, 75)) == (50, 25)
        assert viewport.is_mouse_inside(_FakeCtx(100, 50))
        assert viewport.is_mouse_inside(_FakeCtx(300, 250))
        assert not viewport.is_mouse_inside(_FakeCtx(99, 50))
        assert not viewport.is_mouse_inside(_FakeCtx(301, 250))


class TestWindowManager:
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

    def test_reset_layout_discards_then_formally_retires_dynamic_view(self):
        from infernux.engine.ui.window_manager import WindowManager, WindowState

        previous = WindowManager._instance
        core = None
        try:
            core, engine, manager, _scene, graph, _document, controller = self._layout_reset_fixture()

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
            manager._window_type_ids["particle_graph_editor"] = "particle_graph_editor"

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
            _reset_editor_interaction_state.abandon_session_changes(document.document_id)

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

            assert manager.get_window_state("particle_graph_editor") is WindowState.CLOSED
            assert panel.document_id == ""
            assert _reset_editor_interaction_state.get(document.document_id) is None
            assert _reset_editor_interaction_state.resolve_locator(locator) is None
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
                    "window_types": {"particle_graph_editor": "particle_graph_editor"},
                }
            )

            assert manager.get_window_state("particle_graph_editor") is WindowState.OPENING
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

    def test_dynamic_window_registration_focus_and_close_lifecycle(self):
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
            assert WindowManager.instance() is manager
            calls = []
            listener = lambda: calls.append(tuple(manager.get_registered_types()))
            manager.add_type_change_listener(listener)
            manager.register_window_type("dynamic", Panel, "Dynamic", factory=Panel)
            assert calls == [("dynamic",)]
            manager.remove_type_change_listener(listener)
            manager.register_window_type("second", object, "Second")
            assert calls == [("dynamic",)]
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

    def test_runtime_canvas_collection_keeps_persistent_owners_order_and_scene_identity(self):
        from infernux.ui import UICanvas
        from infernux.ui.ui_canvas_utils import (
            collect_runtime_canvases_with_go,
            collect_sorted_runtime_canvases,
            invalidate_canvas_cache,
        )

        active, persistent = UICanvas(), UICanvas()
        active.sort_order, persistent.sort_order = 10, 5
        active_scene, persistent_scene = self._Scene(active), self._Scene(persistent)
        invalidate_canvas_cache()
        assert collect_sorted_runtime_canvases(active_scene, persistent_scene) == [persistent, active]
        assert collect_runtime_canvases_with_go(active_scene, persistent_scene) == [
            (active_scene._root, active),
            (persistent_scene._root, persistent),
        ]
        assert collect_runtime_canvases_with_go(active_scene, active_scene) == [(active_scene._root, active)]

    def test_canvas_membership_changes_without_unrelated_structure_traversal(self):
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

        assert canvas_utils.collect_sorted_runtime_canvases(scene) == [canvas]
        assert scene.root_queries == 0
        scene.structure_version += 100_000
        assert canvas_utils.collect_sorted_runtime_canvases(scene) == [canvas]
        assert scene.root_queries == 0

        scene._root._components.remove(canvas)
        canvas._set_game_object(None)
        assert canvas_utils.collect_runtime_canvases_with_go(scene) == []


def test_screen_ui_rect_cache_survives_frames_and_invalidates_on_geometry_change(scene, monkeypatch):
    from infernux.ui import UICanvas, UIText
    from infernux.ui.inx_ui_screen_component import clear_rect_cache

    element = UIText()
    root = scene.create_game_object("Rect cache Canvas")
    root.add_py_component(UICanvas())
    child = scene.create_game_object("Rect cache Text")
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
    element.fallback_fonts = [create_asset_ref("Font", guid=guid, path_hint="stale/path.ttf") for guid in paths]
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
        element.font = create_asset_ref("Font", guid="font-primary", path_hint="Assets/Fonts/Old.ttf")
        element.fallback_fonts = [create_asset_ref("Font", guid="font-cjk", path_hint="Assets/Fonts/OldCJK.ttf")]
        attrs = dispatch_module._extract_text_attrs(element)
    finally:
        set_runtime_asset_resolver(None)

    assert attrs["font_path"] == resolved["font-primary"]
    assert attrs["fallback_font_paths"] == [resolved["font-cjk"]]


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
