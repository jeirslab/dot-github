#!/usr/bin/env python3
"""CODEOWNERS managed block from org.toml `[codeowners]` (ideas.md 20, ADR-002 R9).

Writes/refreshes a block between `# BEGIN ORG CODEOWNERS` and `# END ORG CODEOWNERS`
in .github/CODEOWNERS (created if missing); lines outside the block are the repo's own.
Path patterns and owner handles come from the repository's org.toml, so the org name
never appears here. Idempotent.

  gen_codeowners.py [--root .] [--dry-run]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_cfg, parse_common  # noqa: E402

BEGIN = "# BEGIN ORG CODEOWNERS"
END = "# END ORG CODEOWNERS"


def render(cfg: dict) -> str:
    rows = [f"{glob} {' '.join(owners)}" for glob, owners in cfg["codeowners"].items()]
    body = "\n".join(rows) if rows else "# (no sensitive paths declared in org.toml [codeowners])"
    return f"{BEGIN}\n# Generated from org.toml [codeowners] by the org sync — edit org.toml, not this block.\n{body}\n{END}\n"


def apply(root: Path, dry_run: bool = False) -> str:
    cfg = load_cfg(root)
    path = root / ".github" / "CODEOWNERS"
    block = render(cfg)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if BEGIN in existing and END in existing:
        i, j = existing.index(BEGIN), existing.index(END) + len(END)
        if existing[j:j + 1] == "\n":
            j += 1
        new = existing[:i] + block + existing[j:]
    elif existing:
        new = block + "\n" + existing
    else:
        new = block
    if new == existing:
        return "unchanged"
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    return "updated" if existing else "created"


def main(argv: list[str] | None = None) -> int:
    ap = parse_common(argv or sys.argv[1:], __doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    print(f".github/CODEOWNERS: {apply(args.root.resolve(), args.dry_run)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
