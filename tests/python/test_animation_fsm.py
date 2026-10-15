"""Animation playback, authored assets and event publication contracts."""

from __future__ import annotations


import pytest

from infernux.core.anim_state_machine import (
    AnimCondition,
    AnimParameter,
    AnimState,
    AnimStateMachine,
    AnimTransition,
)
from infernux.graph import TypeRef, ValueType
from infernux.components.skeletal_animator import SkeletalAnimator
from infernux.components.spirit_animator import SpiritAnimator


def _make_animator() -> SkeletalAnimator:
    """Bare SkeletalAnimator with a hand-built FSM (no renderer / no scene)."""
    anim = SkeletalAnimator()
    anim._parameters = {}
    anim._duration_cache = {}
    anim._clip_cache = {}
    return anim


def _condition(parameter: AnimParameter, operator: str = "==", threshold: float = 1.0):
    return AnimCondition(
        parameter_id=parameter.stable_id,
        operator=operator,
        threshold=threshold,
    )


class _FakeClip:
    def __init__(
        self,
        take_name="Walk",
        duration_hint=2.0,
        source_model_guid="",
        default_loop=True,
        apply_root_motion=False,
        bone_mask=(),
        curves=(),
    ):
        self.take_name = take_name
        self.duration_hint = duration_hint
        self.source_model_guid = source_model_guid
        self.source_model_path = ""
        self.default_loop = default_loop
        self.apply_root_motion = apply_root_motion
        self.bone_mask = list(bone_mask)
        self.curves = list(curves)


class _RendererBinding:
    def __init__(self, native):
        self.native = native

    def _get_bound_native_component(self):
        return self.native


class _RendererOwner:
    def __init__(self, renderer):
        self.renderer = renderer
        self.lookups = 0

    def get_component(self, _component_type):
        self.lookups += 1
        return self.renderer

    def get_children(self):
        return []


class _HierarchyOwner:
    def __init__(self, renderer=None, children=()):
        self.renderer = renderer
        self.children = list(children)

    def get_component(self, _component_type):
        return self.renderer

    def get_children(self):
        return list(self.children)


class _NativePoseRecorder:
    def __init__(self):
        self.calls = []
        self.pose_stacks = []

    def submit_animation_pose(self, *args):
        self.calls.append(args)

    def submit_pose_stack(self, layers):
        self.pose_stacks.append(layers)


def test_imported_root_motion_updates_owner_transform_once_per_frame(monkeypatch):
    class Rotation:
        def __init__(self, value):
            self.value = value

        def __mul__(self, other):
            return Rotation((self.value, other.value))

    class Transform:
        def __init__(self):
            self.translations = []
            self.local_rotation = Rotation("base")

        def translate_local(self, value):
            self.translations.append(value)

    class Native(_NativePoseRecorder):
        def __init__(self):
            super().__init__()
            self.root_calls = []

        def get_root_motion_delta(self, *args):
            self.root_calls.append(args)
            return "move", Rotation("delta")

        def get_animation_duration_seconds(self, *_args):
            return 2.0

    native = Native()
    renderer = _RendererBinding(native)
    transform = Transform()
    owner = _HierarchyOwner(renderer=renderer)
    owner.transform = transform
    animator = _make_animator()
    animator._fsm = AnimStateMachine(states=[AnimState(name="Walk", loop=True)], default_state="Walk")
    animator._current_state_name = "Walk"
    animator._current_clip = _FakeClip("Walk", duration_hint=2.0, source_model_guid="d" * 32, apply_root_motion=True)
    animator._elapsed = 0.5
    animator._playing = True
    animator._last_native_pose_key = None
    monkeypatch.setattr(SkeletalAnimator, "game_object", property(lambda _self: owner))

    animator.update(0.25)

    assert native.root_calls == [("Walk", 0.5, 0.75, True, "d" * 32)]
    assert transform.translations == ["move"]
    assert transform.local_rotation.value == ("base", "delta")
    assert len(native.calls) == 1


def test_imported_non_looping_clip_cannot_be_forced_to_wrap_by_fsm(monkeypatch):
    animator = _make_animator()
    native = _NativePoseRecorder()
    owner = _HierarchyOwner(renderer=_RendererBinding(native))
    monkeypatch.setattr(SkeletalAnimator, "game_object", property(lambda _self: owner))
    animator._fsm = AnimStateMachine(states=[AnimState(name="Once", loop=True)], default_state="Once")
    animator._current_state_name = "Once"
    animator._current_clip = _FakeClip("Once", duration_hint=1.0, default_loop=False)
    animator._elapsed = 0.9
    animator._playing = True

    animator.update(0.2)

    assert animator._elapsed == pytest.approx(1.0)
    assert animator.is_playing is False
    assert native.calls[-1][6] is False


def test_skeletal_animator_reacquires_renderer_after_scene_replacement(monkeypatch):
    animator = _make_animator()
    stale = _RendererBinding(None)
    live = _RendererBinding(object())
    owner = _RendererOwner(live)
    animator._skinned_renderer = stale
    monkeypatch.setattr(
        SkeletalAnimator,
        "game_object",
        property(lambda _self: owner),
    )

    assert animator._resolve_skinned_renderer() is live
    assert animator._skinned_renderer is live
    assert owner.lookups == 1


def test_skeletal_animator_submits_same_pose_to_all_descendant_renderers(monkeypatch):
    animator = _make_animator()
    body_native = _NativePoseRecorder()
    visor_native = _NativePoseRecorder()
    owner = _HierarchyOwner(
        children=[
            _HierarchyOwner(renderer=_RendererBinding(body_native)),
            _HierarchyOwner(renderer=_RendererBinding(visor_native)),
        ]
    )
    monkeypatch.setattr(
        SkeletalAnimator,
        "game_object",
        property(lambda _self: owner),
    )
    animator._fsm = AnimStateMachine(
        states=[AnimState(name="Walk", loop=True)],
        default_state="Walk",
    )
    animator._current_state_name = "Walk"
    animator._current_clip = _FakeClip("Walk", duration_hint=2.0)
    animator._elapsed = 0.5
    animator._playing = True
    animator._last_native_pose_key = None

    animator._sync_native_runtime_playback()

    assert len(body_native.calls) == 1
    assert visor_native.calls == body_native.calls


def test_skeletal_animator_submits_clip_source_separately_from_render_model(monkeypatch):
    animator = _make_animator()
    native = _NativePoseRecorder()
    owner = _HierarchyOwner(renderer=_RendererBinding(native))
    monkeypatch.setattr(SkeletalAnimator, "game_object", property(lambda _self: owner))
    animator._fsm = AnimStateMachine(
        states=[AnimState(name="Walk", loop=True)],
        default_state="Walk",
    )
    animator._current_state_name = "Walk"
    animator._current_clip = _FakeClip("Walk", source_model_guid="d" * 32)
    animator._elapsed = 0.5
    animator._playing = True
    animator._last_native_pose_key = None

    animator._sync_native_runtime_playback()

    assert len(native.calls) == 1
    assert native.calls[0][7] == "d" * 32


def test_imported_bone_mask_uses_native_pose_stack(monkeypatch):
    animator = _make_animator()
    native = _NativePoseRecorder()
    owner = _HierarchyOwner(renderer=_RendererBinding(native))
    monkeypatch.setattr(SkeletalAnimator, "game_object", property(lambda _self: owner))
    animator._fsm = AnimStateMachine(states=[AnimState(name="Upper", loop=True)], default_state="Upper")
    animator._current_state_name = "Upper"
    animator._current_clip = _FakeClip("Wave", bone_mask=["Spine", "Arm"])
    animator._elapsed = 0.5
    animator._playing = True
    animator._last_native_pose_key = None

    animator._sync_native_runtime_playback()

    assert native.calls == []
    assert native.pose_stacks == [
        [
            {
                "take_name": "Wave",
                "source_model_guid": "",
                "time": 0.5,
                "weight": 1.0,
                "loop": True,
                "bone_mask": ["Spine", "Arm"],
            }
        ]
    ]


def test_blend_state_pose_cache_includes_both_clip_masks(monkeypatch):
    animator = _make_animator()
    native = _NativePoseRecorder()
    clip_b = _FakeClip("Run", bone_mask=["Leg"])
    monkeypatch.setattr(animator, "_resolve_clip_b", lambda _state: clip_b)
    animator._current_clip = _FakeClip("Walk", bone_mask=["Spine"])
    animator._elapsed = 0.5
    state = AnimState(name="Locomotion", kind="blend", blend_value=0.5)

    assert animator._submit_blend_state([native], state)
    animator._current_clip.bone_mask = ["Arm"]
    assert animator._submit_blend_state([native], state)

    assert len(native.pose_stacks) == 2
    assert native.pose_stacks[-1][0]["bone_mask"] == ["Arm"]
    assert native.pose_stacks[-1][1]["bone_mask"] == ["Leg"]


def test_imported_float_curve_drives_animator_float_parameter():
    from infernux.core.animation_clip3d import ImportedFloatCurve

    animator = _make_animator()
    clip = _FakeClip(curves=[ImportedFloatCurve("FootPlant", ((0.0, 0.0), (1.0, 2.0)))])
    animator._sample_imported_curves(clip, 0.25)

    assert animator.get_curve_value("FootPlant") == pytest.approx(0.5)
    assert animator.get_float("FootPlant") == pytest.approx(0.5)


def test_imported_events_dispatch_every_occurrence_across_multiple_wraps(monkeypatch):
    from infernux.core.animation_event import AnimationEvent

    calls = []

    class Receiver:
        def step(self, text, number):
            calls.append((text, number))

    native = _NativePoseRecorder()
    owner = _HierarchyOwner(renderer=_RendererBinding(native))
    owner.get_py_components = lambda: [Receiver()]
    monkeypatch.setattr(SkeletalAnimator, "game_object", property(lambda _self: owner))
    animator = _make_animator()
    animator._fsm = AnimStateMachine(states=[AnimState(name="Loop", loop=True)], default_state="Loop")
    animator._current_state_name = "Loop"
    animator._current_clip = _FakeClip("Loop", duration_hint=1.0)
    animator._current_clip.events = [AnimationEvent(0.25, "step", "L", 1.0)]
    animator._elapsed = 0.9
    animator._prev_event_norm = 0.9
    animator._playing = True

    animator.update(2.2)

    assert calls == [("L", 1.0), ("L", 1.0)]


def test_native_pose_submission_failure_propagates(monkeypatch):
    animator = _make_animator()

    class Native:
        @staticmethod
        def submit_animation_pose(*_args):
            raise RuntimeError("native pose rejected")

    owner = _HierarchyOwner(renderer=_RendererBinding(Native()))
    monkeypatch.setattr(SkeletalAnimator, "game_object", property(lambda _self: owner))
    animator._fsm = AnimStateMachine(states=[AnimState(name="Walk")], default_state="Walk")
    animator._current_state_name = "Walk"
    animator._current_clip = _FakeClip("Walk")

    with pytest.raises(RuntimeError, match="native pose rejected"):
        animator._sync_native_runtime_playback()


class TestTriggerConsumption:
    def test_spirit_animator_same_semantics(self):
        sp = SpiritAnimator()
        attack = AnimParameter(name="attack", value_type=TypeRef(ValueType.BOOL), default=False)
        attacking = AnimParameter(name="is_attacking", value_type=TypeRef(ValueType.BOOL), default=False)
        sp._fsm = AnimStateMachine(parameters=[attack, attacking])
        sp.set_trigger("attack")
        sp._consume_triggers(AnimTransition(conditions=[_condition(attacking)]))
        assert sp._parameters["attack"] is True


class TestEmptyConditionTransition:
    def _animator_with_state(self, loop: bool, duration: float):
        anim = _make_animator()
        fsm = AnimStateMachine(name="t", mode="3d")
        state = AnimState(name="S", loop=loop)
        fsm.states.append(state)
        anim._fsm = fsm
        anim._current_state_name = "S"
        anim._current_clip = _FakeClip("S_take", duration_hint=duration)
        return anim, state


# ── Safe condition evaluator (replaces eval()) ──────────────────────────────


# ── Animation events ────────────────────────────────────────────────────────


class _EventSink:
    def __init__(self):
        self.calls = []
        self.footsteps = 0

    def on_animation_event(self, name, string_arg, number_arg):
        self.calls.append((name, string_arg, number_arg))

    def footstep(self, string_arg, number_arg):
        self.footsteps += 1


class _FakeGameObject:
    def __init__(self, comps):
        self._comps = comps

    def get_py_components(self):
        return list(self._comps)


class TestAnimationEventWindowing:
    def test_dispatch_uses_precomputed_arity_without_signature_reflection(self, monkeypatch):
        from infernux.core import animation_event
        from infernux.core.animation_event import AnimationEvent
        from infernux.engine import runtime_dispatch
        from infernux.engine.runtime_dispatch import publish_runtime_dispatch_epoch

        class AritySink:
            def __init__(self):
                self.calls = []

            def one_arg(self, value):
                self.calls.append(("one", value))

            def broken(self, _value, _number):
                self.calls.append("broken")
                raise TypeError("handler body")

        sink = AritySink()
        publication = publish_runtime_dispatch_epoch((AritySink,))
        publication.commit()
        try:
            descriptor = runtime_dispatch.current_runtime_epoch().descriptor_for(AritySink)
            assert descriptor is not None
            assert descriptor.methods["one_arg"].accepted_arg_counts == (1,)

            def reject_signature(_callback):
                raise AssertionError("event dispatch must not inspect signatures")

            monkeypatch.setattr(runtime_dispatch.inspect, "signature", reject_signature)
            go = _FakeGameObject([sink])
            animation_event.dispatch_animation_events(
                go,
                [
                    AnimationEvent(0.5, "one_arg", "L", 2.0),
                    AnimationEvent(0.5, "broken", "R", 3.0),
                ],
                0.4,
                0.6,
                looped=False,
            )
            assert sink.calls == [("one", "L"), "broken"]
        finally:
            publication.rollback()

    def test_generic_sink_and_named_alias_are_invoked_once(self):
        from infernux.core.animation_event import AnimationEvent, dispatch_animation_events
        from infernux.engine.runtime_dispatch import publish_runtime_dispatch_epoch

        class AliasSink:
            def __init__(self):
                self.calls = 0

            def on_animation_event(self, _name, _text, _number):
                self.calls += 1

            alias = on_animation_event

        sink = AliasSink()
        publication = publish_runtime_dispatch_epoch((AliasSink,))
        publication.commit()
        try:
            dispatch_animation_events(
                _FakeGameObject([sink]),
                [AnimationEvent(0.5, "alias", "", 0.0)],
                0.4,
                0.6,
                looped=False,
            )
            assert sink.calls == 1
        finally:
            publication.rollback()


# ── Serialization round-trips for new fields ────────────────────────────────


def _write_texture_asset_metadata(
    texture_path,
    *,
    guid: str,
    texture_type,
    sprite_frame_ids=(),
):
    import json

    from infernux.core.asset_types import SpriteFrame, TextureImportSettings

    texture_path.parent.mkdir(parents=True, exist_ok=True)
    texture_path.write_bytes(b"test texture")
    settings = TextureImportSettings(
        texture_type=texture_type,
        sprite_frames=[
            SpriteFrame(stable_id=stable_id, name=f"frame_{index}", w=16, h=16)
            for index, stable_id in enumerate(sprite_frame_ids)
        ],
    )

    def tagged(value):
        if type(value) is bool:
            tag = "bool"
        elif type(value) is int:
            tag = "int"
        elif type(value) is list:
            tag = "json_array"
        else:
            tag = "string"
        return {"type": tag, "value": value}

    metadata = {"guid": tagged(guid)}
    metadata.update({key: tagged(value) for key, value in settings.to_dict().items()})
    texture_path.with_name(texture_path.name + ".meta").write_text(
        json.dumps({"metadata": metadata}, indent=2),
        encoding="utf-8",
    )


class TestAnimationClipSpriteFrameReferences:
    FRAME_A = "1" * 32
    FRAME_B = "2" * 32
    TEXTURE_GUID = "a" * 32

    def test_valid_sprite_frame_references_resolve_guid_mapping(self, tmp_path):
        from infernux.core.animation_clip import AnimationClip, AnimationFrame
        from infernux.core.asset_types import TextureType

        project = tmp_path / "project"
        texture = project / "Assets" / "Sprites" / "sheet.png"
        _write_texture_asset_metadata(
            texture,
            guid=self.TEXTURE_GUID,
            texture_type=TextureType.SPRITE,
            sprite_frame_ids=(self.FRAME_A, self.FRAME_B),
        )
        clip = AnimationClip(
            authoring_texture_guid=self.TEXTURE_GUID,
            frames=[AnimationFrame(sprite_frame_id=self.FRAME_B)],
        )

        resolved = clip.validate_sprite_frame_references(
            project_root=str(project),
            guid_paths={self.TEXTURE_GUID: "Assets/Sprites/sheet.png"},
        )

        assert resolved == str(texture)

    def test_guid_survives_file_round_trip_and_resolves_moved_texture(self, tmp_path, monkeypatch):
        from infernux.core.animation_clip import AnimationClip, AnimationFrame
        from infernux.core.asset_types import TextureType
        from infernux.core.assets import AssetManager

        project = tmp_path / "project"
        texture = project / "Assets" / "Sprites" / "sheet.png"
        clip_path = project / "Assets" / "Animations" / "walk.animclip2d"
        clip_path.parent.mkdir(parents=True)
        _write_texture_asset_metadata(
            texture,
            guid=self.TEXTURE_GUID,
            texture_type=TextureType.SPRITE,
            sprite_frame_ids=(self.FRAME_A,),
        )
        clip = AnimationClip(
            name="walk",
            authoring_texture_guid=self.TEXTURE_GUID,
            frames=[AnimationFrame(sprite_frame_id=self.FRAME_A)],
        )
        database = type(
            "Database",
            (),
            {"get_path_from_guid": staticmethod(lambda guid: str(texture) if guid == self.TEXTURE_GUID else "")},
        )()
        monkeypatch.setattr(AssetManager, "_asset_database", database)
        assert clip.save(str(clip_path)) is True

        loaded = AnimationClip.load(str(clip_path))

        assert loaded is not None
        assert loaded.authoring_texture_guid == self.TEXTURE_GUID
        assert loaded.validate_sprite_frame_references(
            project_root=str(project),
            guid_paths={self.TEXTURE_GUID: str(texture)},
        ) == str(texture)


class TestSpiritAnimatorAssetReload:
    @staticmethod
    def _clip(frame_count: int, fps: float):
        from infernux.core.animation_clip import AnimationClip, AnimationFrame

        return AnimationClip(
            frames=[AnimationFrame(sprite_frame_id=f"{index + 1:032x}") for index in range(frame_count)],
            fps=fps,
        )

    def test_clip_hot_reload_preserves_state_progress_and_playback(self, tmp_path, monkeypatch):
        import json
        from infernux.components import spirit_animator as animator_module

        path = tmp_path / "walk.animclip2d"
        clip_guid = "a" * 32
        old_clip = self._clip(4, 4.0)
        path.write_text(json.dumps(old_clip.to_dict()), encoding="utf-8")
        database = type(
            "Database",
            (),
            {"get_path_from_guid": staticmethod(lambda guid: str(path) if guid == clip_guid else "")},
        )()
        monkeypatch.setattr(animator_module, "_get_asset_database", lambda: database)

        state = AnimState(name="Walk", clip_guid=clip_guid)
        animator = SpiritAnimator()
        animator._fsm = AnimStateMachine(
            states=[state],
            default_state="Walk",
        )
        animator._clip_cache = {"Walk": old_clip}
        animator._current_state_name = "Walk"
        animator._current_clip = old_clip
        animator._elapsed = old_clip.duration * 0.5
        animator._playing = True
        animator._parameters = {"speed": 3.0}

        class Renderer:
            frame_id = ""
            sync_count = 0

            def sync_visual(self):
                self.sync_count += 1

        renderer = Renderer()
        animator._sprite_renderer = renderer

        replacement = self._clip(8, 8.0)
        path.write_text(json.dumps(replacement.to_dict()), encoding="utf-8")
        from infernux.engine.interaction import AssetMutation, AssetMutationKind

        animator._on_asset_changed(
            AssetMutation(
                AssetMutationKind.MODIFIED,
                f"{path}.meta",
                guid=clip_guid,
            )
        )

        assert animator._current_state_name == "Walk"
        assert animator._current_clip is animator._clip_cache["Walk"]
        assert animator._current_clip.frame_count == 8
        assert animator.normalized_time == pytest.approx(0.5)
        assert animator._playing is True
        assert animator._parameters == {"speed": 3.0}
        assert renderer.frame_id == replacement.frames[4].sprite_frame_id
        assert renderer.sync_count == 1

    def test_invalid_clip_hot_reload_keeps_previous_runtime_clip(self, tmp_path, monkeypatch):
        import json
        from infernux.components import spirit_animator as animator_module

        path = tmp_path / "walk.animclip2d"
        clip_guid = "b" * 32
        clip = self._clip(2, 2.0)
        path.write_text(json.dumps(clip.to_dict()), encoding="utf-8")
        database = type(
            "Database",
            (),
            {"get_path_from_guid": staticmethod(lambda guid: str(path) if guid == clip_guid else "")},
        )()
        monkeypatch.setattr(animator_module, "_get_asset_database", lambda: database)
        state = AnimState(name="Walk", clip_guid=clip_guid)
        animator = SpiritAnimator()
        animator._fsm = AnimStateMachine(states=[state], default_state="Walk")
        animator._clip_cache = {"Walk": clip}
        animator._current_state_name = "Walk"
        animator._current_clip = clip
        animator._elapsed = 0.25
        animator._playing = True
        animator._sprite_renderer = None

        path.write_text("not json", encoding="utf-8")
        from infernux.engine.interaction import AssetMutation, AssetMutationKind

        animator._on_asset_changed(
            AssetMutation(
                AssetMutationKind.MODIFIED,
                str(path),
                guid=clip_guid,
            )
        )

        assert animator._current_clip is clip
        assert animator._clip_cache["Walk"] is clip
        assert animator._elapsed == pytest.approx(0.25)
        assert animator._playing is True


def test_trigger_consumption_uses_exact_declared_identifiers():
    animator = _make_animator()
    attack = AnimParameter(name="attack", value_type=TypeRef(ValueType.BOOL), default=False)
    attacking = AnimParameter(name="is_attacking", value_type=TypeRef(ValueType.BOOL), default=False)
    jump = AnimParameter(name="jump", value_type=TypeRef(ValueType.BOOL), default=False)
    animator._fsm = AnimStateMachine(parameters=[attack, attacking, jump])
    animator.set_trigger("attack")
    animator.set_trigger("jump")
    animator.set_bool("idle", True)
    animator._consume_triggers(AnimTransition(conditions=[_condition(attacking)]))
    assert animator._parameters["attack"] and animator._parameters["jump"]
    animator._consume_triggers(AnimTransition(conditions=[_condition(attack), _condition(jump)]))
    assert not animator._parameters["attack"] and not animator._parameters["jump"]
    assert animator._parameters["idle"]


def test_reconfiguring_crossfade_duration_preserves_override_and_hard_cut_semantics():
    animator = _make_animator()
    animator.cross_fade_duration = 0.25
    a, b = _FakeClip("A"), _FakeClip("B")
    animator._start_blend_if_needed(a, 0.5, 1.0, b, fade_duration=0.6)
    assert animator._blend_duration == pytest.approx(0.6) and animator._blend_from_take_name == "A"
    animator._start_blend_if_needed(b, 0.2, 1.0, a, fade_duration=None)
    assert animator._blend_duration == pytest.approx(0.25) and animator._blend_from_take_name == "B"
    animator._start_blend_if_needed(a, 1.2, 1.0, a, fade_duration=None)
    assert animator._blend_duration == pytest.approx(0.25) and animator._blend_from_take_name == "A"
    animator._start_blend_if_needed(a, 0.0, 1.0, b, fade_duration=0.0)
    assert animator._blend_duration == 0.0 and animator._blend_from_clip is None


def test_crossfade_can_sync_phase_then_restart_the_return_state():
    animator = _make_animator()
    animator._fsm = AnimStateMachine(
        states=[AnimState(name="Walk", loop=True), AnimState(name="Run", loop=True)], default_state="Walk"
    )
    animator._clip_cache = {"Walk": _FakeClip("Walk", duration_hint=2.0), "Run": _FakeClip("Run", duration_hint=4.0)}
    animator._current_state_name = "Walk"
    animator._current_clip = animator._clip_cache["Walk"]
    animator._elapsed, animator._playing = 1.5, True
    animator._apply_active_take = lambda: None
    animator._sync_native_runtime_playback = lambda: None
    assert animator.cross_fade("Run", 0.2, preserve_phase=True)
    assert animator._elapsed == pytest.approx(3.0)
    assert animator._prev_event_norm == pytest.approx(0.75)
    assert animator._blend_from_elapsed == pytest.approx(1.5)
    assert animator.cross_fade("Walk", 0.2)
    assert animator._elapsed == 0.0 and animator._prev_event_norm == 0.0


def test_non_looping_clip_end_condition_follows_clamped_progress():
    animator = _make_animator()
    state = AnimState(name="Once", loop=False)
    animator._fsm = AnimStateMachine(states=[state], default_state=state.name)
    animator._current_state_name = state.name
    animator._current_clip = _FakeClip("Once", duration_hint=1.0)
    transition = AnimTransition(target_state="Next")
    animator._elapsed = 0.4
    assert not animator._evaluate_condition(transition)
    animator._elapsed = 1.5
    assert animator._evaluate_condition(transition) and animator.normalized_time == 1.0
