#!/usr/bin/env python3
"""Build a static site and source ZIP from an explicit public-file allowlist."""
from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

PUBLIC_FILES = (
    ".github/workflows/ci.yml",
    ".gitignore",
    "AGENTS.md",
    "README.md",
    "README.ko.md",
    "examples/spend-snapshot.schema-example.json",
    "gjc_preset_sync/__init__.py",
    "gjc_preset_sync/__main__.py",
    "gjc_preset_sync/core.py",
    "gjc_preset_sync/quality.py",
    "gjc_preset_sync/eval_state.py",
    "gjc_preset_sync/gjc_runner.py",
    "gjc_preset_sync/eval_suite.py",
    "gjc_preset_sync/evaluate.py",
    "pyproject.toml",
    "scripts/build_site.py",
    "scripts/install.py",
    "scripts/install.sh",
    "skills/openrouter-preset-sync/SKILL.md",
    "systemd/gjc-preset-sync.service",
    "systemd/gjc-preset-sync.timer",
    "tests/test_sync.py",
    "tests/test_public_build.py",
    "tests/eval_helpers.py",
    "tests/test_quality.py",
    "tests/test_eval_state.py",
    "tests/test_evaluate.py",
    "site/index.html",
    "site/style.css",
    "site/app.js",
)
SITE_FILES = ("index.html", "style.css", "app.js")
ARCHIVE_ROOT = "gjc-openrouter-preset-sync-public"


def checksums(payloads: dict[str, bytes]) -> bytes:
    return "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(payloads.items())
    ).encode("utf-8")


def ensure_regular_path(root: Path, relative: str) -> Path:
    current = root
    for part in Path(relative).parts:
        if part in (".", ".."):
            raise ValueError("Relative traversal is not permitted")
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlinks are not permitted in public artifacts")
    return current


def build(root: Path) -> Path:
    root = root.resolve()
    payloads = {}
    for name in PUBLIC_FILES:
        source = ensure_regular_path(root, name)
        if not source.is_file():
            raise ValueError(f"Missing public source file: {name}")
        payloads[name] = source.read_bytes()
    output = ensure_regular_path(root, "dist/site")
    output.mkdir(parents=True, exist_ok=True)
    for name in (*SITE_FILES, "source.zip", "SHA256SUMS"):
        target = ensure_regular_path(root, f"dist/site/{name}")
        if target.exists() and not target.is_file():
            raise ValueError("Build output must be a regular file")
    for name in SITE_FILES:
        (output / name).write_bytes(payloads[f"site/{name}"])
    with zipfile.ZipFile(output / "source.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        bundled = {**payloads, "SHA256SUMS": checksums(payloads)}
        for name, data in sorted(bundled.items()):
            info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{name}", (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    site_payloads = {name: (output / name).read_bytes() for name in (*SITE_FILES, "source.zip")}
    (output / "SHA256SUMS").write_bytes(checksums(site_payloads))
    print(f"Built {len(payloads)} public source files; no directory-wide copy or Git history")
    return output


if __name__ == "__main__":
    build(Path(__file__).resolve().parent.parent)
