from __future__ import annotations


def test_search_query_model_normalizes_and_revisions_only_real_changes():
    from infernux.engine.interaction.search import SearchQueryModel

    model = SearchQueryModel()
    assert model.revision == 0
    assert not model.active

    assert model.set_query("  Smoke ")
    assert model.query == "  Smoke "
    assert model.normalized_query == "smoke"
    assert model.revision == 1
    assert model.matches("GPU SMOKE Emitter")
    assert not model.matches("Water")

    assert not model.set_query("  Smoke ")
    assert model.revision == 1


def test_search_tokens_reject_stale_query_source_and_scope():
    from infernux.engine.interaction.search import SearchQueryModel

    model = SearchQueryModel("cube")
    token = model.token(source_generation=7, scope_key="Assets")
    assert model.accepts(token, source_generation=7, scope_key="Assets")
    assert not model.accepts(token, source_generation=8, scope_key="Assets")
    assert not model.accepts(token, source_generation=7, scope_key="Packages")

    model.set_query("sphere")
    assert not model.accepts(token, source_generation=7, scope_key="Assets")


def test_search_clear_is_a_revisioned_transition():
    from infernux.engine.interaction.search import SearchQueryModel

    model = SearchQueryModel("camera")
    revision = model.revision
    assert model.clear()
    assert model.revision == revision + 1
    assert not model.active
    assert model.matches("anything")
    assert not model.clear()
