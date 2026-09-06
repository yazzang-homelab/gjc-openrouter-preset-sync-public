from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_site", REPO / "scripts/build_site.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PublicBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in builder.PUBLIC_FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((REPO / name).read_bytes())

    def test_allowlist_excludes_private_and_unlisted_files(self):
        marker = b"PRIVATE_FIXTURE_" + b"NOT_FOR_RELEASE"
        for name in (".env", "models.yml", "state/cache.json", ".git/config", "site/notes.txt",
                     "evaluation-state/quota.json", "evaluation-state/evidence/private.json",
                     "approval.local.json", ".gjc/ledger.jsonl"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(marker)
        output = builder.build(self.root)
        with zipfile.ZipFile(output / "source.zip") as archive:
            expected = {f"{builder.ARCHIVE_ROOT}/{name}" for name in builder.PUBLIC_FILES}
            expected.add(f"{builder.ARCHIVE_ROOT}/SHA256SUMS")
            self.assertEqual(set(archive.namelist()), expected)
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                self.assertNotIn(marker, archive.read(name))
            manifest = archive.read(f"{builder.ARCHIVE_ROOT}/SHA256SUMS").decode()
            for line in manifest.splitlines():
                digest, name = line.split("  ", 1)
                data = archive.read(f"{builder.ARCHIVE_ROOT}/{name}")
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        self.assertEqual({p.name for p in output.iterdir()}, {*builder.SITE_FILES, "source.zip", "SHA256SUMS"})
        for line in (output / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)

    def test_zip_is_reproducible(self):
        output = builder.build(self.root)
        first = (output / "source.zip").read_bytes()
        builder.build(self.root)
        self.assertEqual((output / "source.zip").read_bytes(), first)

    def test_missing_source_fails_before_output(self):
        (self.root / "site/app.js").unlink()
        with self.assertRaises(ValueError):
            builder.build(self.root)
        self.assertFalse((self.root / "dist").exists())

    def test_license_and_readmes_ship_verbatim_in_source_zip(self):
        self.assertIn("LICENSE", builder.PUBLIC_FILES)
        output = builder.build(self.root)
        with zipfile.ZipFile(output / "source.zip") as archive:
            self.assertEqual(len(archive.namelist()), len(builder.PUBLIC_FILES) + 1)
            for name in ("LICENSE", "README.md", "README.ko.md"):
                self.assertEqual(archive.read(f"{builder.ARCHIVE_ROOT}/{name}"), (REPO / name).read_bytes())
            license_text = archive.read(f"{builder.ARCHIVE_ROOT}/LICENSE").decode("utf-8")
        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn("Copyright (c) 2026 GJC OpenRouter Preset Sync contributors", license_text)
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND', license_text)

    def test_missing_license_fails_before_output(self):
        (self.root / "LICENSE").unlink()
        with self.assertRaisesRegex(ValueError, "LICENSE"):
            builder.build(self.root)
        self.assertFalse((self.root / "dist").exists())

    def test_pyproject_links_license_file_and_english_readme(self):
        lines = [line.strip() for line in (REPO / "pyproject.toml").read_text(encoding="utf-8").splitlines()]
        self.assertIn('license = "MIT"', lines)
        self.assertIn('license-files = ["LICENSE"]', lines)
        self.assertIn('readme = "README.md"', lines)

    def test_symlink_source_is_rejected(self):
        source = self.root / "site/app.js"
        source.unlink()
        source.symlink_to(self.root / "site/style.css")
        with self.assertRaises(ValueError):
            builder.build(self.root)
        self.assertFalse((self.root / "dist").exists())

    def test_symlink_output_directory_is_rejected(self):
        external = self.root / "outside-output"
        external.mkdir()
        (self.root / "dist").symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            builder.build(self.root)
        self.assertEqual(list(external.iterdir()), [])


class InstallerDistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.agent = Path(self.temp.name) / "agent"
        self.home.mkdir()
        self.agent.mkdir()
        (self.agent / "models.yml").write_text("untouched: true\n")

    def test_installer_ships_both_readmes_and_license_without_activation(self):
        completed = subprocess.run(
            [sys.executable, str(REPO / "scripts/install.py"), "--home", str(self.home), "--agent-dir", str(self.agent)],
            capture_output=True, text=True, check=True, timeout=60,
        )
        report = json.loads(completed.stdout)
        share = self.home / ".local/share/gjc-preset-sync"
        for name in ("README.md", "README.ko.md", "LICENSE"):
            self.assertEqual((share / name).read_bytes(), (REPO / name).read_bytes(), name)
        self.assertEqual((share / ".installer-owner").read_text().strip(), "gjc-openrouter-preset-sync")
        self.assertEqual(report["timer_enabled"], False)
        self.assertEqual(report["models_changed"], False)
        self.assertEqual(report["network_calls"], 0)
        self.assertEqual((self.agent / "models.yml").read_text(), "untouched: true\n")
        unitdir = self.home / ".config/systemd/user"
        self.assertTrue((unitdir / "gjc-preset-sync.timer").is_file())
        self.assertFalse((unitdir / "timers.target.wants").exists())
        self.assertFalse((unitdir / "default.target.wants").exists())
        self.assertTrue((self.agent / "skills/openrouter-preset-sync/SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
