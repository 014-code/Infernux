"""GUID reference resolution, cache invalidation and typed identity."""

from infernux.core.asset_ref import AssetRefBase, AudioClipRef, MaterialRef, ShaderRef, TextureRef


def test_reference_cache_follows_guid_changes_and_explicit_invalidation():
    calls = []

    class ResolvingReference(AssetRefBase):
        def _do_resolve(self):
            value = object()
            calls.append((self.guid, value))
            return value

    reference = ResolvingReference()
    assert not reference and reference.resolve() is None
    reference.guid = "first-guid"
    first = reference.resolve()
    assert reference.resolve() is first
    reference.guid = "first-guid"
    assert reference.resolve() is first and len(calls) == 1
    reference.guid = "second-guid"
    second = reference.resolve()
    assert second is not first and calls[-1][0] == "second-guid"
    reference.invalidate()
    assert reference.resolve() is not second and len(calls) == 3


def test_asset_reference_documents_persist_guid_and_ignore_display_paths():
    reference = AssetRefBase(guid="asset-guid", path_hint="Assets/Display.png")
    assert reference.to_dict() == {"guid": "asset-guid"}
    restored = AssetRefBase.from_dict({
        "guid": "asset-guid", "path_hint": "Assets/Obsolete.png", "path": "old",
    })
    assert restored.guid == reference.guid and restored.path_hint == ""
    assert AssetRefBase.from_dict(None).guid == ""
    assert AssetRefBase.from_dict({"guid": 7, "path_hint": "old"}).guid == ""
    assert reference.display_name == "Display.png"
    assert "GUID:" in AssetRefBase(guid="abcdefgh1234").display_name


def test_asset_reference_equality_hash_and_typed_empty_identity():
    reference = AssetRefBase(guid="same-guid")
    other = AssetRefBase(guid="same-guid")
    assert reference == other and hash(reference) == hash(other)
    assert reference != AssetRefBase(guid="another-guid")
    assert not AssetRefBase().is_missing
    assert AssetRefBase().display_name == "None"
    assert TextureRef() != ShaderRef()
    assert MaterialRef() == None  # noqa: E711
    assert hash(MaterialRef()) == hash(None)


def test_asset_reference_types_keep_their_public_resource_identity():
    for reference_type in (TextureRef, ShaderRef, AudioClipRef, MaterialRef):
        reference = reference_type(guid="resource-guid")
        assert isinstance(reference, AssetRefBase)
        assert reference.guid == "resource-guid"
        assert reference_type.__name__ in repr(reference)
