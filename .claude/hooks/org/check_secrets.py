#!/usr/bin/env python3
"""Secrets that should not be there (ideas.md 19, ADR-002 R8).

Layer 2 of the org secrets defence: pattern scan over changed (or all tracked) files,
SOPS files must be encrypted, `.env` must not be tracked. Layer 1 is GitHub push
protection; layer 3 is gitleaks in the guardrails workflow. Exit 1 on any hit.

  check_secrets.py [--root .] [--range base..head]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import annotate, changed_files, load_cfg, matches_any, parse_common  # noqa: E402

PATTERNS = [
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----")),
    ("age secret key", re.compile(r"\bAGE-SECRET-KEY-1[0-9A-Z]{50,}\b")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{20,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{40,}\b")),
    ("Bitcoin extended private key", re.compile(r"\b[xyzt]prv[1-9A-HJ-NP-Za-km-z]{100,}\b")),
    ("Bitcoin WIF private key", re.compile(r"\b[5KL][1-9A-HJ-NP-Za-km-z]{50,51}\b")),
    ("Infisical token", re.compile(r"\bst\.[0-9a-f-]{36}\.[0-9a-f]{16,}\.[0-9a-f]{32,}\b")),
    # A quoted value that is a template reference (${var.x}, {{ secrets.X }}, $ENV) is a
    # pointer to a secret, not the secret; the negative lookahead skips those.
    ("password assignment", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"](?!\\?\$\{|\{\{|\$[A-Z_])[^'\"\s]{12,}['\"]")),
]
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".lock", ".sum")
ALLOW_MARK = "org-secrets: allow"


def is_sops_encrypted(text: str) -> bool:
    return "sops:" in text and ("mac:" in text or '"mac"' in text) and "ENC[" in text


def main(argv: list[str] | None = None) -> int:
    ap = parse_common(argv or sys.argv[1:], __doc__)
    args = ap.parse_args(argv)
    root: Path = args.root.resolve()
    cfg = load_cfg(root)
    hits = 0

    files = changed_files(root, args.rng)

    # 1. .env must never be tracked (checked over the whole tree regardless of range).
    for f in changed_files(root, None):
        name = Path(f).name
        if name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template"))):
            annotate("error", ".env file is tracked; secrets come from Infisical, delete it and rotate anything in it", f)
            hits += 1

    for f in files:
        p = root / f
        if not p.is_file() or f.endswith(SKIP_SUFFIXES):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # 2. SOPS-managed paths must be encrypted.
        if matches_any(f, cfg["secrets"]["sops_paths"]) and f.endswith((".yaml", ".yml", ".json", ".env", ".ini")):
            if not is_sops_encrypted(text):
                annotate("error", "file is on a SOPS-managed path but is not SOPS-encrypted (no sops/mac/ENC[ metadata)", f)
                hits += 1
            continue  # encrypted blobs would trip the pattern scan

        if matches_any(f, cfg["secrets"]["allow_paths"]):
            continue

        # 3. Pattern scan.
        for i, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARK in line:
                continue
            for label, rx in PATTERNS:
                if rx.search(line):
                    annotate("error", f"{label} in tracked content — remove it and rotate the credential", f, i)
                    hits += 1
                    break

    if args.json:
        import json
        print(json.dumps({"hits": hits, "files_scanned": len(files)}))
    else:
        print("secrets: clean" if hits == 0 else f"secrets: {hits} finding(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
