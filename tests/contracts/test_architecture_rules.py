"""Dependency and ownership rules; implementation behavior belongs in runtime tests."""

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
RENDERER = "cpp/infernux/function/renderer"
SCENE_RENDERER = "cpp/infernux/function/scene/SceneRenderer"
NATIVE_OBJECT = r"\b(?:Scene|SceneManager|GameObject|Component|Transform|MeshRenderer|SkinnedMeshRenderer|Camera)\s*\*"

# Each rule describes a forbidden dependency, not a preferred implementation.
RULES = [
    (f"{RENDERER}/rhi/**", r"#\s*include\s*[<\"](?:vulkan/|vk_mem_alloc\.h)|\bVk[A-Z]\w*", ()),
    (f"{RENDERER}/vk/**", r"#\s*include\s*[<\"][^\n]*?(?:function/(?:scene|editor)/|bindings/python/|python/)", ()),
    (f"{SCENE_RENDERER}.*", NATIVE_OBJECT, ()),
    (f"{SCENE_RENDERER}.*", r"#\s*include\s*[<\"][^\n]*?(?:SceneSystem|SceneManager|GameObject|Component|Transform|MeshRenderer|SkinnedMeshRenderer|Camera)\.h", ()),
    (f"{RENDERER}/RenderWorld.h", NATIVE_OBJECT + r"|\bInxMaterial\s*\*", ()),
    (f"{RENDERER}/**", r"\bvk(?:CreateDescriptorPool|AllocateDescriptorSets|DestroyDescriptorPool)\s*\(", (f"{RENDERER}/vk/VkDescriptorManager.cpp",)),
    (f"{RENDERER}/FullscreenRenderer.cpp", r"\b(?:Vk[A-Z]\w*|VulkanRhiDevice|vkDeviceWaitIdle)\b", ()),
    ("cpp/**/*Capture*.cpp", r"\b(?:BitBlt|PrintWindow|GetDC|XGetImage|CGWindowListCreateImage)\s*\(", ()),
]


@pytest.mark.parametrize("pattern,forbidden,owners", RULES)
def test_native_dependency_direction(pattern, forbidden, owners):
    paths = sorted(path for path in ROOT.glob(pattern) if path.suffix in {".h", ".hpp", ".cpp"})
    assert paths, pattern
    violation = []
    for path in paths:
        if path.relative_to(ROOT).as_posix() in owners:
            continue
        source = path.read_text(encoding="utf-8")
        source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
        if re.search(forbidden, source):
            violation.append(path.relative_to(ROOT).as_posix())
    assert not violation, f"Forbidden dependency {forbidden}: {violation}"


def test_material_publication_does_not_idle_the_device():
    source = (ROOT / RENDERER / "MaterialDescriptor.cpp").read_text(encoding="utf-8")
    publication = re.search(
        r"bool MaterialDescriptorManager::PublishDescriptorReplacement\b.*?(?=\n(?:void|bool) MaterialDescriptorManager::|\Z)",
        source, re.S,
    )
    assert publication is not None
    assert not re.search(r"\bvkDeviceWaitIdle\s*\(", publication.group())


@pytest.mark.parametrize(
    "target,required,forbidden",
    [
        ("InfernuxRenderCore", "InfernuxFoundation", {"InfernuxVulkanBackend", "InfernuxRuntime"}),
        ("InfernuxVulkanBackend", "InfernuxRenderCore", {"InfernuxRuntime"}),
        ("InfernuxRendererRuntime", "InfernuxFoundation", {"InfernuxRuntime"}),
    ],
)
def test_native_library_dependency_direction(target, required, forbidden):
    sources = "\n".join(path.read_text(encoding="utf-8") for path in [
        ROOT / "CMakeLists.txt", ROOT / "cmake/InfernuxNativeTargets.cmake",
    ])
    sources = re.sub(r"#[^\n]*", "", sources)
    linked = set()
    for command in re.findall(r"target_link_libraries\s*\(([^)]*)\)", sources, re.S):
        arguments = command.split()
        if arguments[0] == target:
            linked.update(arguments[1:])
    assert required in linked
    assert not linked & forbidden
