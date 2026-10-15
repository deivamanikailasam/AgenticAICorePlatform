"""``agent-spec validate PATH...`` and ``agent-spec schema``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_spec.loader import parse
from agent_spec.reference import render_reference, schema


def _files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(
                f for f in p.rglob("*.y*ml") if f.name.endswith(("agent.yaml", ".yaml", ".yml"))
            )
        else:
            out.append(p)
    return out


def validate(paths: list[str], strict: bool) -> int:
    files = _files(paths)
    if not files:
        print("validate: no spec files found")
        return 1
    failed = 0
    for f in files:
        spec, issues = parse(f.read_text(), str(f))
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        bad = bool(errors) or (strict and bool(warnings))
        failed += bad
        status = "FAIL" if bad else "ok  "
        label = f"{spec.name}@{spec.version}" if spec else ""
        print(f"{status} {f} {label}".rstrip())
        for i in errors + warnings:
            print(f"     {i.severity}: {i.render(str(f))}")
    print(f"validate: {len(files)} file(s), {failed} failed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-spec")
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate agent.yaml files or directories")
    v.add_argument("paths", nargs="+")
    v.add_argument("--strict", action="store_true", help="treat warnings as errors")
    s = sub.add_parser("schema", help="print the JSON Schema")
    s.add_argument("--markdown", action="store_true", help="print the reference page instead")
    args = parser.parse_args(argv)
    if args.cmd == "validate":
        return validate(args.paths, args.strict)
    print(render_reference() if args.markdown else json.dumps(schema(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
