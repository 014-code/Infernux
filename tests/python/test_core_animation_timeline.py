"""Public animation authoring workflows and invalid document boundaries."""
from __future__ import annotations


import pytest


from infernux.core.animation_timeline import (
    AnimationTimeline,
    TimelineKeyframe,
    INTERP_CONSTANT,
    INTERP_LINEAR,
    INTERP_EASE_IN,
    INTERP_EASE_OUT,
    INTERP_EASE_IN_OUT,
    APPLY_ABSOLUTE,
)


def test_keyframe_default_lists_are_independent():
    a = TimelineKeyframe()
    b = TimelineKeyframe()
    a.position[0] = 9.0
    assert b.position[0] == 0.0
    assert a.stable_id != b.stable_id


def test_keyframe_from_dict_invalid_interp_is_rejected():
    document = TimelineKeyframe().to_dict()
    document["interp"] = "bogus"
    with pytest.raises(ValueError, match="interpolation"):
        TimelineKeyframe.from_dict(document)


def test_keyframe_from_dict_missing_fields_is_rejected():
    with pytest.raises(ValueError, match="fields mismatch"):
        TimelineKeyframe.from_dict({})


def test_keyframe_from_dict_rejects_empty_stable_id():
    document = TimelineKeyframe().to_dict()
    document["stable_id"] = ""
    with pytest.raises(ValueError, match="stable_id"):
        TimelineKeyframe.from_dict(document)


def test_keyframe_from_dict_short_vector_is_rejected():
    document = TimelineKeyframe().to_dict()
    document["position"] = [1, 2]
    with pytest.raises(TypeError, match="three finite numbers"):
        TimelineKeyframe.from_dict(document)


def test_keyframe_from_dict_non_numeric_vector_is_rejected():
    document = TimelineKeyframe().to_dict()
    document["scale"] = ["a", "b", "c"]
    with pytest.raises(TypeError, match="three finite numbers"):
        TimelineKeyframe.from_dict(document)


def test_sample_empty_returns_none():
    assert AnimationTimeline().sample(0.5) is None


def test_sample_zero_span_coincident_keys():
    tl = AnimationTimeline(keyframes=[
        TimelineKeyframe(time=1.0, position=[1, 0, 0]),
        TimelineKeyframe(time=1.0, position=[2, 0, 0]),
    ])
    result = tl.sample(1.0)
    assert result is not None  # must not divide by zero


def test_timeline_rejects_duplicate_keyframe_ids():
    first = TimelineKeyframe(stable_id="same")
    second = TimelineKeyframe(stable_id="same")
    timeline = AnimationTimeline(keyframes=[first, second])

    with pytest.raises(ValueError, match="must be unique"):
        timeline.to_dict()


def test_timeline_from_dict_invalid_apply_mode_is_rejected():
    document = AnimationTimeline().to_dict()
    document["apply_mode"] = "weird"
    with pytest.raises(ValueError, match="apply_mode"):
        AnimationTimeline.from_dict(document)


def test_timeline_from_dict_rejects_noncanonical_keyframes():
    document = AnimationTimeline().to_dict()
    document["keyframes"] = ["bad"]
    with pytest.raises(ValueError, match="keyframe fields mismatch"):
        AnimationTimeline.from_dict(document)


@pytest.mark.parametrize('interp,fraction', [
    (INTERP_CONSTANT, 0.), (INTERP_LINEAR, .25), (INTERP_EASE_IN, .0625),
    (INTERP_EASE_OUT, .4375), (INTERP_EASE_IN_OUT, .15625),
])
def test_authored_timeline_samples_all_transform_channels_and_boundaries(interp, fraction):
    first = TimelineKeyframe(time=0., position=[0, 0, 0], rotation=[0, 0, 0], scale=[1, 1, 1])
    last = TimelineKeyframe(time=2., position=[10, 0, 0], rotation=[0, 90, 0], scale=[3, 3, 3], interp=interp)
    timeline = AnimationTimeline(keyframes=[last, first])
    position, rotation, scale = timeline.sample(.5)
    assert position == pytest.approx([10 * fraction, 0, 0])
    assert rotation == pytest.approx([0, 90 * fraction, 0])
    assert scale == pytest.approx([1 + 2 * fraction] * 3)
    assert timeline.sample(-1.)[0] == [0., 0., 0.]
    assert timeline.sample(3.)[0] == [10., 0., 0.]
    assert timeline.keyframes == [last, first]
    position[0] = 999
    assert first.position == [0, 0, 0] and last.position == [10, 0, 0]


def test_timeline_document_and_file_preserve_stable_keyframe_identity(tmp_path):
    first = TimelineKeyframe(time=0., position=[1, 2, 3])
    last = TimelineKeyframe(time=3.5, position=[7, 8, 9], rotation=[10, 20, 30], scale=[2, 2, 2])
    timeline = AnimationTimeline(name='Spin', duration=3.5, apply_mode=APPLY_ABSOLUTE, keyframes=[first, last])
    assert not timeline.save()
    document = timeline.to_dict()
    assert 'file_path' not in document
    restored = AnimationTimeline.from_dict(document)
    assert restored.to_dict() == document
    assert restored.find_keyframe(first.stable_id).position == [1., 2., 3.]
    assert restored.find_keyframe('missing') is None
    path = str(tmp_path / 'Spin.animtimeline')
    assert timeline.save(path)
    loaded = AnimationTimeline.load(path)
    assert loaded.to_dict() == document
    loaded.keyframes[0].position[0] = 100
    assert first.position == [1, 2, 3]


@pytest.mark.parametrize('contents', [None, '{', '[]'], ids=['missing', 'malformed-json', 'non-object'])
def test_timeline_load_rejects_unreadable_documents(tmp_path, contents):
    path = tmp_path / 'Invalid.animtimeline'
    if contents is not None:
        path.write_text(contents, encoding='utf-8')
    assert AnimationTimeline.load(str(path)) is None
