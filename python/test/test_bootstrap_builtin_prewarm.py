import threading
import time
from types import SimpleNamespace

import pytest

from Infernux.engine.bootstrap import EditorBootstrap
from Infernux.engine.player_runtime import PlayerRuntimeSession
from Infernux.engine.startup_warmup import PlayerStartupWarmup


class _Registry:
    def __init__(self, materials):
        self.materials = materials

    def get_builtin_material(self, name):
        return self.materials.get(name)


class _Native:
    def __init__(self):
        self.refreshed = []

    def refresh_material_pipeline(self, material):
        self.refreshed.append(material)


def _bootstrap(native):
    bootstrap = EditorBootstrap.__new__(EditorBootstrap)
    bootstrap.engine = SimpleNamespace(get_native_engine=lambda: native)
    return bootstrap


def _install_registry(monkeypatch, registry):
    monkeypatch.setattr(
        "Infernux.lib.AssetRegistry.instance",
        lambda: registry,
    )


def test_builtin_pipeline_prewarm_reuses_default_lit_and_builds_skybox(monkeypatch):
    native = _Native()
    materials = {"DefaultLit": object(), "SkyboxProcedural": object()}
    _install_registry(monkeypatch, _Registry(materials))

    _bootstrap(native)._prewarm_builtin_pipelines()

    assert native.refreshed == [materials["SkyboxProcedural"]]


def test_builtin_pipeline_prewarm_rejects_a_missing_required_material(monkeypatch):
    _install_registry(monkeypatch, _Registry({"DefaultLit": object()}))

    with pytest.raises(RuntimeError, match="SkyboxProcedural"):
        _bootstrap(_Native())._prewarm_builtin_pipelines()


def test_builtin_pipeline_prewarm_propagates_native_refresh_failure(monkeypatch):
    class _RejectingNative:
        def refresh_material_pipeline(self, _material):
            raise RuntimeError("pipeline rejected")

    _install_registry(
        monkeypatch,
        _Registry({"DefaultLit": object(), "SkyboxProcedural": object()}),
    )

    with pytest.raises(RuntimeError, match="pipeline rejected"):
        _bootstrap(_RejectingNative())._prewarm_builtin_pipelines()


def test_player_schedules_project_warmup_without_blocking_first_present():
    class _Warmup:
        def prepare_cpu(self):
            raise AssertionError("startup scheduling must not wait for CPU declarations")

    session = PlayerRuntimeSession.__new__(PlayerRuntimeSession)
    session._startup_warmup = None
    warmup = _Warmup()

    session.schedule_startup_warmup(warmup)

    assert session._startup_warmup is warmup


def test_player_runtime_pumps_and_releases_completed_startup_warmup():
    class _Warmup:
        def __init__(self):
            self.calls = 0

        def pump(self):
            self.calls += 1
            return self.calls == 2

    session = PlayerRuntimeSession.__new__(PlayerRuntimeSession)
    warmup = _Warmup()
    session._startup_warmup = warmup

    assert session.pump_startup_warmup() is False
    assert session._startup_warmup is warmup
    assert session.pump_startup_warmup() is True
    assert session._startup_warmup is None


def test_player_cpu_warmup_resolves_on_owner_and_executes_on_worker(monkeypatch):
    owner_thread = threading.get_ident()
    observed = {}

    def hook():
        observed["hook_thread"] = threading.get_ident()
        return True

    def resolve(record):
        observed["resolve_thread"] = threading.get_ident()
        observed["record"] = record
        return hook, 0.0

    monkeypatch.setattr(
        "Infernux.engine.startup_warmup._resolve_player_warmup_record", resolve
    )
    monkeypatch.setattr(
        "Infernux.engine.startup_warmup.wait_cpu_runtime_preload", lambda: None
    )
    monkeypatch.setattr(
        "Infernux.engine.startup_warmup.Debug.log", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "Infernux.engine.player_log.write_player_log", lambda *_args, **_kwargs: None
    )
    record = {"module": "Scripts.Cpu", "qualname": "Cpu"}
    warmup = PlayerStartupWarmup(
        "project", (), cpu_records=(record,), scope="test"
    )

    deadline = time.monotonic() + 2.0
    while not warmup.pump():
        assert time.monotonic() < deadline
        time.sleep(0.001)

    assert observed["record"] is record
    assert observed["resolve_thread"] == owner_thread
    assert observed["hook_thread"] != owner_thread


def test_player_gpu_warmup_advances_while_cpu_worker_is_busy(monkeypatch):
    cpu_release = threading.Event()
    prepared = []

    def cpu_hook():
        assert cpu_release.wait(timeout=2.0)
        return True

    monkeypatch.setattr(
        "Infernux.engine.startup_warmup.wait_cpu_runtime_preload", lambda: None
    )
    monkeypatch.setattr(
        "Infernux.compute._prepare_now",
        lambda declaration, params: prepared.append((declaration, params)),
    )
    monkeypatch.setattr(
        "Infernux.engine.startup_warmup.Debug.log", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "Infernux.engine.player_log.write_player_log", lambda *_args, **_kwargs: None
    )
    declaration = object()
    warmup = PlayerStartupWarmup("project", (), scope="test")
    warmup._delay_frames = 0
    warmup._cpu_records_resolved = True
    warmup._cpu_jobs.append(
        ({"module": "Scripts.Cpu", "qualname": "Cpu"}, cpu_hook, 0.0)
    )
    warmup._prepares.append((declaration, (1, 2, 3)))

    try:
        assert warmup.pump() is False
        assert prepared == [(declaration, (1, 2, 3))]
        assert not warmup._cpu_done.is_set()
    finally:
        cpu_release.set()
        if warmup._cpu_thread is not None:
            warmup._cpu_thread.join(timeout=2.0)
