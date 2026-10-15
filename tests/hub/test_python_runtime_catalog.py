from __future__ import annotations


import pytest


from python_runtime_catalog import (
    DEFAULT_PYTHON_RUNTIME,
    PythonRuntimeId,
    SUPPORTED_PYTHON_RUNTIMES,
    runtime_directory_name,
    runtime_release,
)


def test_default_runtime_catalog_identity_layout_and_archive_are_consistent() -> None:
    runtime = PythonRuntimeId.parse("3.13")
    assert runtime == DEFAULT_PYTHON_RUNTIME == PythonRuntimeId(3, 13)
    assert SUPPORTED_PYTHON_RUNTIMES == (runtime, PythonRuntimeId(3, 12))
    assert runtime.series == "3.13"
    assert runtime.directory_name == runtime_directory_name("3.13") == "python313"
    assert runtime_directory_name("3.12") == "python312"
    assert runtime.cp_tag == "cp313"
    assert runtime.windows_library_stem == "python313"
    assert runtime.unix_library_stem == "python3.13"
    release = runtime_release("3.13")
    assert release.patch_version == "3.13.15"
    assert release.build_release == "20260825"
    assert PythonRuntimeId.parse(release.patch_version) == runtime


def test_unknown_python_runtime_is_rejected() -> None:
    with pytest.raises(ValueError, match="not in the Hub runtime catalog"):
        runtime_release("3.11")
