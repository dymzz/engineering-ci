"""Enumerate the files a script check must cover.

The scope is declared as repository-relative paths. This lists the files of
the requested kind inside that scope, so the tool is given exactly the files
the scope contains and the covered input set is observable rather than
assumed.

The scope is read, never written, and nothing outside the declared scope is
reachable from here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKFLOW_SUFFIXES = (".yml", ".yaml")
POWERSHELL_SUFFIXES = (".ps1", ".psm1")
KINDS = {
    "workflow": WORKFLOW_SUFFIXES,
    "powershell": POWERSHELL_SUFFIXES,
}


def select(root: Path, scope: list[str], kind: str) -> list[str]:
    """List the files of `kind` inside `scope`, relative to `root`."""
    if kind not in KINDS:
        raise ValueError(f"Unknown scope kind: {kind}")
    suffixes = KINDS[kind]
    found: list[str] = []
    for entry in scope:
        target = root / entry
        if target.is_dir():
            found += [
                item.relative_to(root).as_posix()
                for item in sorted(target.rglob("*"))
                if item.is_file() and item.suffix.lower() in suffixes
            ]
        elif target.is_file():
            # An explicitly named file is requested on its own terms.
            found.append(target.relative_to(root).as_posix())
        else:
            raise ValueError(f"Scope path not found: {entry}")
    unique: list[str] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("usage: script_scope.py KIND SCOPE [SCOPE...]", file=sys.stderr)
        return 2
    try:
        files = select(Path("."), args[1:], args[0])
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
