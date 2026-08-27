"""Assert that the role table in docs/api.md still matches the code.

A permission table maintained by hand goes stale the first time a role changes,
and a stale one in the API reference is worse than none: it tells an integrator
that an account can do something it cannot. This regenerates the table from
`ROLE_PERMISSIONS` and either checks it or rewrites it.

    python scripts/check_api_doc_roles.py          # check, exit 1 on drift
    python scripts/check_api_doc_roles.py --write  # rewrite the table
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "central-api"))

from app.config import ALL_ROLES, ROLE_PERMISSIONS  # noqa: E402

DOC = ROOT / "docs" / "api.md"
START = "| Role | Permissions |\n|---|---|\n"


def render() -> str:
    rows = []
    for role in ALL_ROLES:
        permissions = sorted(ROLE_PERMISSIONS.get(role, set()))
        rendered = ", ".join(f"`{permission}`" for permission in permissions)
        rows.append(f"| `{role}` | {rendered} |")
    return START + "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite the table in place")
    args = parser.parse_args()

    text = io.open(DOC, encoding="utf-8").read()
    if START not in text:
        print(f"{DOC}: could not find the role table header", file=sys.stderr)
        return 2

    head, _, rest = text.partition(START)
    # The table runs to the first blank line after the header.
    body, separator, tail = rest.partition("\n\n")
    current = START + body + "\n"
    expected = render()

    if current == expected:
        print("docs/api.md role table matches the code")
        return 0

    if not args.write:
        print("docs/api.md role table has drifted from ROLE_PERMISSIONS.", file=sys.stderr)
        print("Run: python scripts/check_api_doc_roles.py --write", file=sys.stderr)
        return 1

    io.open(DOC, "w", encoding="utf-8").write(head + expected + separator + tail)
    print("docs/api.md role table rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
