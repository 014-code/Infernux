from __future__ import annotations

import pytest

from infernux.engine.interaction import (
    CollectionInteractionModel,
    TreeProjectionModel,
)


def test_collection_range_toggle_and_keyboard_navigation_share_one_anchor():
    model = CollectionInteractionModel(("a", "b", "c", "d"))

    model.activate("b")
    assert model.activate("d", extend=True).selected_ids == ("b", "c", "d")
    assert model.selection.anchor_id == "b"
    assert model.activate("c", toggle=True).selected_ids == ("b", "d")
    assert model.move_cursor(-1).primary_id == "b"
    assert model.move_to_edge(last=True, extend=True).selected_ids == ("b", "c", "d")


def test_collection_reconcile_preserves_valid_state_and_retires_removed_transients():
    model = CollectionInteractionModel(("a", "b", "c"))
    model.activate("b")
    model.begin_rename("b", "Before")
    model.set_insertion(3, ("b",))

    assert model.set_items(("a", "c"))
    assert model.selection.selected_ids == ()
    assert model.rename_session is None
    assert model.insertion is not None
    assert model.insertion.index == 2
    assert model.insertion.dragged_ids == ()


def test_tree_projection_preserves_foldout_across_reorder_and_reparent():
    tree = TreeProjectionModel(("parent", "child"))
    rows = tree.project(
        ("parent", "other"),
        {"parent": ("child",), "child": ("leaf",)},
    )
    assert [(row.item_id, row.depth) for row in rows] == [
        ("parent", 0),
        ("child", 1),
        ("leaf", 2),
        ("other", 0),
    ]

    rows = tree.project(
        ("other", "parent"),
        {"other": ("child",), "child": ("leaf",)},
    )
    assert [(row.item_id, row.depth) for row in rows] == [
        ("other", 0),
        ("parent", 0),
    ]
    assert tree.is_expanded("parent")
    assert tree.is_expanded("child")


def test_tree_reveal_reconcile_and_validation_fail_closed():
    tree = TreeProjectionModel()
    assert tree.reveal_ancestors(
        "leaf",
        {"leaf": "child", "child": "root", "root": ""},
    )
    assert tree.expanded_ids == frozenset({"root", "child"})
    assert tree.reconcile(("root", "leaf"))
    assert tree.expanded_ids == frozenset({"root"})

    tree.set_expanded("a", True)
    tree.set_expanded("b", True)
    with pytest.raises(ValueError, match="cycle"):
        tree.project(("a",), {"a": ("b",), "b": ("a",)})
    with pytest.raises(ValueError, match="multiple parents"):
        tree.project(("a", "b"), {"a": ("shared",), "b": ("shared",)})
