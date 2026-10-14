"""Keep the contributor workflow map tied to executable tests and CI."""

import re
import yaml
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_regression_guide_references_existing_test_modules():
    guide = (ROOT / "tests/README.md").read_text(encoding="utf-8")
    modules = set(re.findall(r"(?:tests/hub|tests/python|tests/contracts|tests/isolated)/test_\w+\.py", guide))
    assert modules, "The critical-workflow map must name its owning tests"
    for module in sorted(modules):
        assert (ROOT / module).is_file(), f"Update tests/README.md: {module} moved or was removed"


def test_documented_portable_command_matches_ci():
    guide = (ROOT / "tests/README.md").read_text(encoding="utf-8")
    command = guide.split("```sh\n", 1)[1].split("```", 1)[0]
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    portable_job = yaml.safe_load(workflow)["jobs"]["portable-hub"]
    runs = "\n".join(step.get("run", "") for step in portable_job["steps"])
    pattern = r"\btests/(?:hub|contracts)(?:/test_\w+\.py)?\b"
    expected = {"tests/hub", "tests/contracts"}
    assert set(re.findall(pattern, command)) == expected
    assert set(re.findall(pattern, runs)) == expected
