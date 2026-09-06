from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
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
        for name in (".env", "models.yml", "state/cache.json", ".git/config", "site/notes.txt"):
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


if __name__ == "__main__":
    unittest.main()
