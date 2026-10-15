"""Tests for infernux.renderstack — InjectionPoint, RenderPass, ResourceBus, FullScreenEffect (real C++ backend)."""

from __future__ import annotations


import sys


import types


import pytest


from infernux.renderstack.injection_point import InjectionPoint


from infernux.renderstack.resource_bus import ResourceBus


from infernux.renderstack.render_pass import RenderPass


from infernux.renderstack.fullscreen_effect import FullScreenEffect


def test_pipeline_file_callback_adopts_transactionally_published_module(monkeypatch):
    from infernux.renderstack._render_pipeline_reload import PipelineReloadMixin
    import infernux.renderstack.discovery as discovery

    module_name = "_infernux_test_pipeline_reload"
    previous_module = types.ModuleType(module_name)
    published_module = types.ModuleType(module_name)
    monkeypatch.setitem(sys.modules, module_name, published_module)
    invalidations = []
    monkeypatch.setattr(
        discovery,
        "invalidate_discovery_cache",
        lambda: invalidations.append("discovery"),
    )

    class Subject(PipelineReloadMixin):
        def __init__(self):
            self._pipeline_module = previous_module
            self._pipeline = object()
            self.saved = 0
            self.invalidated = 0

        def _save_current_pipeline_params(self):
            self.saved += 1

        def invalidate_graph(self):
            self.invalidated += 1

    subject = Subject()
    subject._on_pipeline_file_changed("pipeline.py")

    assert subject._pipeline_module is published_module
    assert subject._pipeline is None
    assert subject.saved == 1
    assert subject.invalidated == 1
    assert invalidations == ["discovery"]


def test_player_missing_custom_pipeline_is_not_silently_replaced(monkeypatch):
    from infernux.renderstack.render_stack import RenderStack

    stack = RenderStack()
    stack.pipeline_class_name = "Missing Packaged Pipeline"
    monkeypatch.setenv("_INFERNUX_PLAYER_MODE", "1")
    monkeypatch.setattr(stack, "discover_pipelines", lambda: {})

    with pytest.raises(RuntimeError, match="Missing Packaged Pipeline"):
        stack._create_pipeline()

    assert stack.pipeline_class_name == "Missing Packaged Pipeline"


def test_removed_pipeline_keeps_selection_and_invalidates_for_transactional_rebuild():
    from infernux.renderstack._render_pipeline_reload import PipelineReloadMixin

    class Subject(PipelineReloadMixin):
        DEFAULT_PIPELINE_NAME = "Default Forward"

        def __init__(self):
            self.pipeline_class_name = "Authored Pipeline"
            self._pipeline_catalog_signature = ("Authored Pipeline",)
            self._pipeline = object()
            self._cached_ips = object()
            self.invalidations = 0
            self.saved = 0

        def discover_pipelines(self):
            return {}

        def _save_current_pipeline_params(self):
            assert self._pipeline is not None
            self.saved += 1

        def invalidate_graph(self):
            self.invalidations += 1

    subject = Subject()
    with pytest.warns(RuntimeWarning, match="Authored Pipeline"):
        subject._sync_pipeline_catalog()

    assert subject.pipeline_class_name == "Authored Pipeline"
    assert subject._pipeline is None
    assert subject._cached_ips is None
    assert subject.invalidations == 1
    assert subject.saved == 1


def test_set_pipeline_replaces_runtime_pipeline_and_invalidates_graph_once(monkeypatch):
    from infernux.renderstack.render_stack import RenderStack

    stack = RenderStack()
    stack.pipeline_class_name = "Pipeline A"
    stack._pipeline = object()
    stack._cached_ips = object()
    invalidations = []
    monkeypatch.setattr(stack, "_save_current_pipeline_params", lambda: None)
    monkeypatch.setattr(stack, "invalidate_graph", lambda: invalidations.append(True))

    stack.set_pipeline("Pipeline B")

    assert stack.pipeline_class_name == "Pipeline B"
    assert stack._pipeline is None
    assert stack._cached_ips is None
    assert invalidations == [True]

    # Reapplying the active pipeline is a no-op. Runtime showcases can update
    # their material/effect state without rebuilding the same graph twice.
    stack.set_pipeline("Pipeline B")
    assert invalidations == [True]
