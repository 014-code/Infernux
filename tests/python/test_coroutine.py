"""Tests for infernux.coroutine — yield instructions, Coroutine, CoroutineScheduler."""

from __future__ import annotations

import time as stdlib_time

import pytest

from infernux.coroutine import (
    CoroutineScheduler,
    WaitForEndOfFrame,
    WaitForFrames,
    WaitForFixedUpdate,
    WaitForSeconds,
    WaitForSecondsRealtime,
    WaitUntil,
    WaitWhile,
    notify_runtime_epoch_published,
)
from infernux.engine.runtime_dispatch import RuntimeRevisionEpoch


def iter_wait(instruction):
    yield instruction


class TestWaitForSecondsRealtime:
    def test_system_clock_adjustments_do_not_change_wait_duration(self, monkeypatch):
        clocks = {"system": 1000.0, "monotonic": 20.0}
        monkeypatch.setattr(stdlib_time, "time", lambda: clocks["system"])
        monkeypatch.setattr(stdlib_time, "monotonic", lambda: clocks["monotonic"])
        scheduler = CoroutineScheduler()
        handle = scheduler.start(iter_wait(WaitForSecondsRealtime(0.25)))
        clocks.update(system=2000.0, monotonic=20.1)
        scheduler.tick_update(100.0)
        assert not handle.is_finished
        clocks.update(system=500.0, monotonic=20.3)
        scheduler.tick_update(0.0)
        assert handle.is_finished


class TestWaitForEndOfFrame:

    def test_rejects_invalid_frame_count(self):
        with pytest.raises(ValueError):
            WaitForEndOfFrame(0)
        with pytest.raises(TypeError):
            WaitForEndOfFrame(1.5)


class TestCoroutineScheduler:
    @pytest.mark.parametrize("wait", [WaitUntil, WaitWhile])
    def test_failed_wait_predicate_closes_its_coroutine_and_preserves_other_work(self, wait):
        calls = []

        def predicate():
            raise ValueError("wait predicate failed")

        def failing():
            try:
                yield wait(predicate)
            finally:
                calls.append("closed")

        def healthy():
            yield None
            calls.append("healthy advanced")
            yield None

        scheduler = CoroutineScheduler()
        failed = scheduler.start(failing())
        following = scheduler.start(healthy())
        with pytest.raises(ValueError, match="wait predicate failed"):
            scheduler.tick_update(0.016)
        assert failed.is_finished and not following.is_finished
        assert calls == ["closed", "healthy advanced"]
        assert scheduler.count == 1
        scheduler.tick_update(0.016)
        assert following.is_finished and scheduler.count == 0

    def test_multiple_unsupported_yields_report_all_errors_after_advancing_healthy_work(self):
        calls = []

        def failing():
            try:
                yield object()
            finally:
                calls.append("closed")

        def healthy():
            yield None
            calls.append("healthy advanced")

        scheduler = CoroutineScheduler()
        handles = [scheduler.start(failing()), scheduler.start(failing()), scheduler.start(healthy())]
        with pytest.raises(ExceptionGroup) as reported:
            scheduler.tick_update(0.016)
        assert len(reported.value.exceptions) == 2
        assert all(isinstance(error, TypeError) for error in reported.value.exceptions)
        assert calls == ["closed", "closed", "healthy advanced"]
        assert all(handle.is_finished for handle in handles) and scheduler.count == 0

    def test_wait_failure_keeps_original_error_when_generator_cleanup_also_fails(self):
        def predicate():
            raise ValueError("wait predicate failed")

        def failing():
            try:
                yield WaitUntil(predicate)
            finally:
                raise RuntimeError("generator cleanup failed")

        scheduler = CoroutineScheduler()
        handle = scheduler.start(failing())
        with pytest.raises(ExceptionGroup) as reported:
            scheduler.tick_update(0.016)
        assert [type(error) for error in reported.value.exceptions] == [ValueError, RuntimeError]
        assert handle.is_finished and scheduler.count == 0

    def test_invalid_generator_input_is_rejected_before_scheduler_mutation(self, monkeypatch):
        candidate = iter([None])
        scheduler = CoroutineScheduler()
        observed = scheduler.diagnostics()
        monkeypatch.setattr(CoroutineScheduler, "_advance", lambda _self, _co: pytest.fail("invalid input must not reach coroutine execution"))
        with pytest.raises(TypeError, match="generator"):
            scheduler.start(candidate)
        assert scheduler.diagnostics() == observed

    def test_bound_generator_method_must_be_called_before_start(self, monkeypatch):
        class Owner:
            def sequence(self):
                yield None

        scheduler = CoroutineScheduler()
        monkeypatch.setattr(CoroutineScheduler, "_advance", lambda _self, _co: pytest.fail("a method must not reach coroutine execution"))
        with pytest.raises(TypeError, match="generator"):
            scheduler.start(Owner().sequence)
        assert scheduler.count == 0

    def test_generator_protocol_adapter_preserves_execution_and_cleanup(self):
        from collections.abc import Generator

        cleaned = []

        def sequence():
            try:
                yield None
                yield None
            finally:
                cleaned.append(True)

        class Adapter(Generator):
            def __init__(self, inner):
                self.inner = inner

            def send(self, value):
                return self.inner.send(value)

            def throw(self, *args):
                return self.inner.throw(*args)

            def close(self):
                self.inner.close()

        scheduler = CoroutineScheduler()
        handle = scheduler.start(Adapter(sequence()))
        scheduler.tick_update(0.016)
        assert not handle.is_finished and scheduler.count == 1
        scheduler.stop(handle)
        assert handle.is_finished and scheduler.count == 0 and cleaned == [True]


    def test_stop_propagates_generator_close_failure_after_detaching(self):
        def gen():
            try:
                yield None
            finally:
                raise RuntimeError("close failed")

        sched = CoroutineScheduler()
        co = sched.start(gen())

        with pytest.raises(RuntimeError, match="close failed"):
            sched.stop(co)

        assert co.is_finished is True
        assert sched.count == 0


    def test_stop_all_reports_close_failures_after_detaching_every_coroutine(self):
        closed = []

        def gen(index):
            try:
                yield None
            finally:
                closed.append(index)
                if index == 1:
                    raise RuntimeError("close failed")

        sched = CoroutineScheduler()
        c1 = sched.start(gen(1))
        c2 = sched.start(gen(2))

        with pytest.raises(ExceptionGroup, match="failed to close coroutines"):
            sched.stop_all()

        assert closed == [1, 2]
        assert c1.is_finished is True
        assert c2.is_finished is True
        assert sched.count == 0

    def test_unsupported_yield_is_rejected_and_detached(self):
        def gen():
            yield object()

        transitions = []
        sched = CoroutineScheduler(on_active_changed=transitions.append)
        co = sched.start(gen())

        with pytest.raises(TypeError, match="unsupported coroutine yield value object"):
            sched.tick_update(0.016)

        assert co.is_finished is True
        assert sched.count == 0
        assert transitions == [True, False]


    def test_exception_in_generator_finishes_coroutine(self):
        def gen():
            raise ValueError("boom")
            yield None  # unreachable

        sched = CoroutineScheduler()
        co = sched.start(gen())
        assert co.is_finished is True

    def test_many_coroutines(self):
        results = []

        def gen(idx):
            yield None
            results.append(idx)

        sched = CoroutineScheduler()
        for i in range(50):
            sched.start(gen(i))
        assert sched.count == 50

        sched.tick_update(0.016)
        assert len(results) == 50
        assert sched.count == 0

    def test_old_coroutine_keeps_old_body_and_new_start_uses_new_epoch(self, monkeypatch):
        import infernux.engine.runtime_dispatch as runtime_dispatch

        old_epoch = RuntimeRevisionEpoch(101, {})
        new_epoch = RuntimeRevisionEpoch(102, {})
        monkeypatch.setattr(runtime_dispatch, "_current_epoch", old_epoch)
        steps = []

        def old_body():
            steps.append("old-start")
            yield None
            steps.append("old-resume")

        scheduler = CoroutineScheduler()
        old = scheduler.start(old_body())
        assert old.creation_epoch is old_epoch
        assert old.creation_epoch_id == 101

        monkeypatch.setattr(runtime_dispatch, "_current_epoch", new_epoch)
        notify_runtime_epoch_published(new_epoch)
        assert old.is_stale_epoch is True
        assert scheduler.stale_epoch_coroutine_count == 1

        def new_body():
            steps.append("new-start")
            yield None
            steps.append("new-resume")

        new = scheduler.start(new_body())
        assert new.creation_epoch is new_epoch
        assert new.creation_epoch_id == 102
        assert new.is_stale_epoch is False

        scheduler.tick_update(0.016, epoch=new_epoch)
        assert steps == ["old-start", "new-start", "old-resume", "new-resume"]
        assert scheduler.stale_epoch_coroutine_count == 0

    def test_epoch_diagnostic_and_stop_cleanup(self, monkeypatch):
        import infernux.engine.runtime_dispatch as runtime_dispatch

        old_epoch = RuntimeRevisionEpoch(201, {})
        new_epoch = RuntimeRevisionEpoch(202, {})
        monkeypatch.setattr(runtime_dispatch, "_current_epoch", old_epoch)

        def gen():
            yield None

        scheduler = CoroutineScheduler()
        coroutine = scheduler.start(gen())
        monkeypatch.setattr(runtime_dispatch, "_current_epoch", new_epoch)
        notify_runtime_epoch_published(new_epoch)
        assert scheduler.diagnostics() == {
            "active_count": 1,
            "stale_epoch_count": 1,
            "creation_epoch_id": 201,
            "observed_epoch_id": 202,
        }
        scheduler.stop(coroutine)
        assert scheduler.stale_epoch_coroutine_count == 0
        scheduler.stop_all()
        assert scheduler.diagnostics()["active_count"] == 0

    def test_rolled_back_epoch_does_not_leave_future_epoch_state(self, monkeypatch):
        import infernux.engine.runtime_dispatch as runtime_dispatch

        old_epoch = RuntimeRevisionEpoch(301, {})
        candidate_epoch = RuntimeRevisionEpoch(302, {})
        monkeypatch.setattr(runtime_dispatch, "_current_epoch", old_epoch)

        def gen():
            yield None

        scheduler = CoroutineScheduler()
        coroutine = scheduler.start(gen())
        publication = runtime_dispatch.RuntimeDispatchPublication(
            old_epoch,
            candidate_epoch,
        )
        monkeypatch.setattr(runtime_dispatch, "_current_epoch", candidate_epoch)

        assert coroutine.is_stale_epoch is False
        publication.rollback()

        assert runtime_dispatch.current_runtime_epoch() is old_epoch
        assert coroutine.is_stale_epoch is False
        assert scheduler.stale_epoch_coroutine_count == 0
        assert scheduler.diagnostics()["observed_epoch_id"] == 301

    def test_coroutine_created_in_future_epoch_is_stale_after_owner_rollback(
        self,
        monkeypatch,
    ):
        import infernux.engine.runtime_dispatch as runtime_dispatch

        stable_epoch = RuntimeRevisionEpoch(401, {})
        candidate_epoch = RuntimeRevisionEpoch(402, {})
        monkeypatch.setattr(runtime_dispatch, "_current_epoch", candidate_epoch)

        def gen():
            yield None

        scheduler = CoroutineScheduler()
        coroutine = scheduler.start(gen())
        assert coroutine.creation_epoch_id == 402

        monkeypatch.setattr(runtime_dispatch, "_current_epoch", stable_epoch)
        notify_runtime_epoch_published(stable_epoch)

        assert coroutine.is_stale_epoch is True
        assert scheduler.stale_epoch_coroutine_count == 1
        assert scheduler.diagnostics()["observed_epoch_id"] == 401


def test_scaled_wait_completes_only_after_accumulated_duration():
    steps = []
    def sequence():
        steps.append("start")
        yield WaitForSeconds(.5)
        steps.append("done")
    scheduler = CoroutineScheduler()
    handle = scheduler.start(sequence())
    assert steps == ["start"]
    scheduler.tick_update(.3)
    assert steps == ["start"] and not handle.is_finished
    scheduler.tick_update(.3)
    assert steps == ["start", "done"] and handle.is_finished and scheduler.count == 0


def test_gameplay_coroutine_waits_for_conditions_frame_phases_and_child_completion():
    scheduler = CoroutineScheduler()
    ready = [False]
    steps = []
    def child():
        yield None
        steps.append("child")
    def sequence():
        yield WaitUntil(lambda: ready[0])
        steps.append("ready")
        yield WaitWhile(lambda: ready[0])
        steps.append("released")
        yield WaitForFixedUpdate()
        steps.append("fixed")
        yield WaitForEndOfFrame()
        steps.append("late")
        yield WaitForEndOfFrame(3)
        steps.append("late-3")
        yield WaitForFrames(3)
        steps.append("update-3")
        yield scheduler.start(child())
        steps.append("complete")
    handle = scheduler.start(sequence())
    scheduler.tick_update(.016)
    assert steps == []
    ready[0] = True
    scheduler.tick_update(.016)
    assert steps == ["ready"]
    scheduler.tick_update(.016)
    assert steps == ["ready"]
    ready[0] = False
    scheduler.tick_update(.016)
    assert steps[-1] == "released"
    scheduler.tick_update(.016)
    assert steps[-1] == "released"
    scheduler.tick_fixed_update(.02)
    assert steps[-1] == "fixed"
    scheduler.tick_update(.016)
    assert steps[-1] == "fixed"
    scheduler.tick_late_update(.016)
    assert steps[-1] == "late"
    scheduler.tick_late_update(.016)
    scheduler.tick_late_update(.016)
    assert steps[-1] == "late"
    scheduler.tick_late_update(.016)
    assert steps[-1] == "late-3"
    scheduler.tick_late_update(.016)
    scheduler.tick_update(.016)
    scheduler.tick_update(.016)
    assert steps[-1] == "late-3"
    scheduler.tick_update(.016)
    assert steps[-1] == "update-3" and not handle.is_finished
    scheduler.tick_update(.016)
    assert steps[-1] == "child" and not handle.is_finished
    scheduler.tick_update(.016)
    assert steps[-1] == "complete" and handle.is_finished and scheduler.count == 0


def test_scheduler_stops_one_handle_then_the_remaining_handles():
    def sequence():
        yield None
        yield None
    scheduler = CoroutineScheduler()
    first, second, third = scheduler.start(sequence()), scheduler.start(sequence()), scheduler.start(sequence())
    scheduler.stop(first)
    assert first.is_finished and not second.is_finished and not third.is_finished and scheduler.count == 2
    scheduler.stop_all()
    assert first.is_finished and second.is_finished and third.is_finished and scheduler.count == 0
