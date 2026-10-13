"""Fail-fast runtime dependency check for deployment and CI.

The application imports LangGraph checkpoint providers during startup. A mixed
global Python environment can otherwise fail with an opaque symbol-import error.
This checker derives minimum versions from ``pyproject.toml`` instead of
duplicating version policy in code.
"""

from __future__ import annotations

import importlib.metadata as metadata
from pathlib import Path
import tomllib
from packaging.specifiers import SpecifierSet
from packaging.version import Version


def _requirements() -> dict[str, str]:
    document = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    deps = document.get("project", {}).get("dependencies", [])
    names = {"langgraph", "langgraph-checkpoint", "langgraph-checkpoint-sqlite", "langgraph-checkpoint-postgres"}
    result: dict[str, str] = {}
    for item in deps:
        name = item.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0].strip().lower()
        if name in names:
            result[name] = item
    # The checkpoint base package is transitive and may not be listed directly;
    # it is still reported when installed because it must match LangGraph.
    return result


def main() -> int:
    requirements = _requirements()
    failed = False
    for package, requirement in requirements.items():
        try:
            installed = metadata.version(package)
        except metadata.PackageNotFoundError:
            print(f"MISSING {package}: required by {requirement}")
            failed = True
            continue
        specifier = requirement[len(package):]
        if Version(installed) not in SpecifierSet(specifier):
            print(f"FAIL {package}=={installed} does not satisfy {requirement}")
            failed = True
        else:
            print(f"OK {package}=={installed} (declared {requirement})")
    try:
        import langgraph.checkpoint.sqlite.aio  # noqa: F401
        print("OK LangGraph checkpoint provider import")
    except Exception as exc:  # noqa: BLE001 - report exact deployment failure
        print(f"FAIL LangGraph checkpoint provider import: {type(exc).__name__}: {exc}")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
