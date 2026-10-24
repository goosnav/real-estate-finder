"""Release-boundary checks that do not require a native desktop."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = load_module("package_universal")


class ReleaseBoundaryTests(unittest.TestCase):
    @staticmethod
    def valid_payload(root):
        (root / "launcher").mkdir(parents=True)
        for directory, file in (("src", "entry.py"), ("static", "index.html")):
            (root / directory).mkdir()
            (root / directory / file).write_text("content")
        for name in ("pyproject.toml", "uv.lock", "launcher/bootstrap.py"):
            (root / name).write_text("fixture")
        (root / ".python-version").write_text("3.12.12")
        (root / "launcher/manifest.json").write_text(json.dumps({"schema_version": 1, "app_id": "com.goosnav.fixture", "python": "3.12.12"}))
        checks = []
        for name in package.REQUIRED_TOOLS:
            tool = root / name
            tool.parent.mkdir(parents=True, exist_ok=True)
            tool.write_bytes(b"fixture binary")
            tool.chmod(0o755)
            checks.append(hashlib.sha256(tool.read_bytes()).hexdigest() + "  " + name)
        (root / "launcher/checksums.sha256").write_text("\n".join(checks) + "\n")

    def test_unix_setup_tool_without_execute_bit_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.valid_payload(root)
            package.validate_app(root)
            (root / "launcher/tools/macos-x64/uv").chmod(0o644)
            with self.assertRaisesRegex(SystemExit, "not executable"):
                package.validate_app(root)

    def test_interrupted_tool_download_is_not_a_release_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.valid_payload(root)
            (root / "launcher/tools/windows-x64/.uv.exe.partial").write_bytes(b"interrupted")
            with self.assertRaisesRegex(SystemExit, "exactly the six"):
                package.validate_app(root)

    def test_extra_checksum_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.valid_payload(root)
            with (root / "launcher/checksums.sha256").open("a") as manifest:
                manifest.write("0" * 64 + "  launcher/tools/unexpected\n")
            with self.assertRaisesRegex(SystemExit, "exactly the six"):
                package.validate_app(root)

    def test_secret_paths_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / ".env"
            secret.write_text("provider=private")
            with zipfile.ZipFile(root / "test.zip", "w") as archive:
                with self.assertRaises(SystemExit):
                    package.add_path(archive, secret, Path("app/.env"))

    def test_executable_mode_survives_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "launcher"
            launcher.write_text("executable")
            launcher.chmod(0o755)
            with zipfile.ZipFile(root / "test.zip", "w") as archive:
                package.add_path(archive, launcher, Path("Product/launcher"))
            with zipfile.ZipFile(root / "test.zip") as archive:
                self.assertTrue(archive.getinfo("Product/launcher").external_attr >> 16 & 0o100)

    def test_source_checkout_cannot_masquerade_as_release(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                package.validate_app(Path(directory))

    def test_placeholder_customer_documents_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "LICENSE.txt"
            document.write_text("Replace this placeholder with the real license.")
            with self.assertRaises(SystemExit):
                package.validate_customer_document(document)


if __name__ == "__main__":
    unittest.main()
