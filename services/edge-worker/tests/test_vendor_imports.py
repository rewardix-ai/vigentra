"""Every import in the vendored ANPR tree must resolve in this deployment.

The engine was vendored from a larger research project that ships an `eval/`
harness, GPU readers and training tooling we deliberately do not carry. Most of
its third-party imports sit *inside functions*, so none of them fail at import
time: a missing package surfaces as a crash on the first pass that reaches that
branch, or - worse - as a reader that silently reads nothing.

Two such gaps shipped and were only caught by running the engine on real
footage:

* `anpr/pipeline.py` imported `eval.metrics._iou` inside `_merge_fragments`,
  which `flush()` calls on every pass. `eval/` is not part of this repository,
  so the first track fragmentation would have raised ModuleNotFoundError in
  production.
* `onnxruntime` - which the CRNN reader and the denoise/deblur stages need -
  was absent from `requirements-anpr.txt` while every import of it was lazy.

These tests are the guard. They read the source rather than importing it, so
they catch the lazy imports an import sweep cannot see.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ANPR = pathlib.Path(__file__).resolve().parent.parent / "anpr"
REQUIREMENTS = pathlib.Path(__file__).resolve().parent.parent / "requirements-anpr.txt"

#: Packages the vendored tree may import only on paths this deployment does not
#: enable. Each is optional by design and documented in docs/anpr-reading.md.
OPTIONAL = {
    "paddle",        # anpr/read/awiros.py - PaddleOCR reader, off by default
    "ppocr",         # same
    "safetensors",   # same
    "anthropic",     # anpr/read/claude_reader.py, off by default
    "matplotlib",    # anpr/fuse/calibrate.py - plotting a calibration curve
    "imageio_ffmpeg",  # anpr/sources/grid.py - an ingest path we do not use
    "src",           # anpr/detect/rtdetr_vehicle.py - an alternative backend
}

#: Provided by the yolo image stage rather than requirements-anpr.txt.
FROM_YOLO_STAGE = {"torch", "torchvision", "ultralytics"}


def _imported_packages() -> dict[str, set[str]]:
    """Top-level third-party package -> the files importing it, lazily or not."""
    found: dict[str, set[str]] = {}
    for path in sorted(ANPR.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top in sys.stdlib_module_names or top in {"anpr", "app", "tools"}:
                    continue
                found.setdefault(top, set()).add(path.name)
    return found


def _declared_requirements() -> set[str]:
    """Package names from real requirement lines, ignoring comments."""
    declared = set()
    for line in REQUIREMENTS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("=")[0].split(">")[0].split("<")[0].split(";")[0].strip()
        # opencv ships as opencv-contrib-python-headless but imports as cv2
        declared.add("cv2" if name.startswith("opencv") else name.lower())
    return declared


def test_no_module_imports_the_upstream_eval_harness():
    """`eval/` is the upstream project's evaluation harness and is not shipped.

    It was imported inside _merge_fragments, on a path flush() always takes.
    """
    offenders = {f for f, files in _imported_packages().items() if f == "eval" for f in files}
    assert not offenders, f"eval/ is not part of this repository, imported by: {sorted(offenders)}"


def test_every_required_package_is_declared_or_deliberately_optional():
    """A lazy import of an undeclared package is a crash waiting for traffic."""
    declared = _declared_requirements()
    missing = {}
    for pkg, files in _imported_packages().items():
        if pkg in OPTIONAL or pkg in FROM_YOLO_STAGE:
            continue
        # PyYAML imports as yaml; numpy and editdistance match their own names
        candidates = {pkg.lower(), "pyyaml" if pkg == "yaml" else pkg.lower()}
        if not candidates & declared:
            missing[pkg] = sorted(files)
    assert not missing, (
        "imported but neither declared in requirements-anpr.txt nor marked "
        f"optional: {missing}"
    )


def test_onnxruntime_is_declared_because_every_import_of_it_is_lazy():
    """The reader loads .onnx weights; without the wheel it reads nothing.

    Named explicitly rather than left to the sweep above, because this is the
    one whose absence is silent instead of loud.
    """
    assert "onnxruntime" in _declared_requirements()


@pytest.mark.parametrize("module", ["pipeline", "api"])
def test_core_modules_parse(module):
    """A guard against a bad edit to the two modules most often patched here."""
    ast.parse((ANPR / f"{module}.py").read_text())
