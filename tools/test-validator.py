#!/usr/bin/env python3
"""Focused fail-closed tests for tools/validate.py."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path


sys.dont_write_bytecode = True
CHECKER = Path(__file__).with_name("validate.py")
SPEC = importlib.util.spec_from_file_location("cmdy_registry_validator", CHECKER)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)


def archive_info(name: str, mode: int = stat.S_IFREG | 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    return info


def write_channel_archive(path: Path, extra: list[zipfile.ZipInfo] | None = None) -> None:
    manifest = {
        "capabilities": ["channels"],
        "entrypoint": "channel.py",
        "id": "dev.termite.fixture",
        "manifestVersion": 1,
        "version": "1.0.0",
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            archive_info("fixture/", stat.S_IFDIR | 0o755), b""
        )
        archive.writestr(
            archive_info("fixture/manifest.json"),
            json.dumps(manifest).encode(),
        )
        archive.writestr(
            archive_info("fixture/channel.py", stat.S_IFREG | 0o755),
            b"#!/usr/bin/env python3\n",
        )
        for info in extra or []:
            archive.writestr(info, b"fixture\n")


class ValidatorTests(unittest.TestCase):
    def test_safe_relative_rejects_escape_and_noncanonical_paths(self) -> None:
        for value in ("../x", "/x", "a//b", "a\\b", "./x", "a/../b"):
            with self.subTest(value=value):
                with self.assertRaises(validator.RegistryError):
                    validator.safe_relative(value, "fixture")

    def test_safe_relative_accepts_registry_path(self) -> None:
        self.assertEqual(
            validator.safe_relative("shaders/cmdy/drift.metal", "fixture"),
            "shaders/cmdy/drift.metal",
        )

    def test_external_archive_url_requires_safe_cmdyext_location(self) -> None:
        expected = "browser-2.1.0.cmdyext"
        valid = f"https://example.com/releases/{expected}"
        self.assertEqual(
            validator.safe_external_archive_url(valid, expected, "fixture"), valid
        )
        for value in (
            f"http://example.com/{expected}",
            f"https://user@example.com/{expected}",
            f"https://example.com/{expected}?download=1",
            "https://example.com/other.cmdyext",
        ):
            with self.subTest(value=value):
                with self.assertRaises(validator.RegistryError):
                    validator.safe_external_archive_url(value, expected, "fixture")

    def test_plugin_requires_exactly_one_archive_location(self) -> None:
        entry = {
            "arch": ["arm64"], "author": "cmdy", "description": "fixture",
            "id": "dev.termite.fixture", "kind": "plugin", "license": "MIT",
            "name": "Fixture", "sdk": "v1", "sha256": "a" * 64,
            "version": "1.0.0",
        }
        checker = validator.Validator(Path("."))
        with self.assertRaisesRegex(validator.RegistryError, "exactly one"):
            checker.check_entry(entry, 0)
        with self.assertRaisesRegex(validator.RegistryError, "exactly one"):
            checker.check_entry(dict(
                entry,
                file="dist/fixture-1.0.0.cmdyext",
                url="https://example.com/fixture-1.0.0.cmdyext",
            ), 0)

    def test_duplicate_json_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"value":1,"value":2}\n', encoding="utf-8")
            with self.assertRaisesRegex(validator.RegistryError, "duplicate"):
                validator.load_json(path, "fixture")

    def test_symlinked_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / "actual.json"
            actual.write_text("{}\n", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(actual)
            with self.assertRaisesRegex(validator.RegistryError, "real regular"):
                validator.load_json(link, "fixture")

    def test_secret_default_is_rejected(self) -> None:
        with self.assertRaises(validator.RegistryError):
            validator.check_default_type(
                {"default": "not-allowed", "type": "secret"}, "fixture"
            )

    def test_configuration_default_types_are_strict(self) -> None:
        validator.check_default_type(
            {"default": ["one", "two"], "type": "string-list"}, "fixture"
        )
        with self.assertRaises(validator.RegistryError):
            validator.check_default_type(
                {"default": [1], "type": "string-list"}, "fixture"
            )

    def test_nested_example_secret_is_rejected(self) -> None:
        with self.assertRaisesRegex(validator.RegistryError, "secret-like"):
            validator.check_example_secrets(
                {"provider": {"apiToken": "credential"}}, "fixture"
            )

    def test_empty_nested_example_secret_is_accepted(self) -> None:
        validator.check_example_secrets(
            {"provider": {"apiToken": ""}}, "fixture"
        )

    def test_duplicate_governed_file_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "rigs/cmdy/one.conf"
            path.parent.mkdir(parents=True)
            path.write_text("theme = Dark\n", encoding="utf-8")
            checker = validator.Validator(root)
            first = {
                "author": "cmdy", "description": "fixture", "file": "rigs/cmdy/one.conf",
                "id": "cmdy/one", "kind": "rig", "license": "MIT",
                "name": "One", "version": "1.0.0",
            }
            checker.check_entry(first, 0)
            second = dict(first, id="cmdy/two", name="Two")
            with self.assertRaisesRegex(validator.RegistryError, "more than one entry"):
                checker.check_entry(second, 1)

    def test_retired_shader_symbol_is_rejected_before_compile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.metal"
            path.write_text(
                "float4 cmdy_main(float2 uv) { return float4(uv, 0, 1); }\n"
                "// retired termite_helper\n",
                encoding="utf-8",
            )
            checker = validator.Validator(Path(directory))
            with self.assertRaisesRegex(validator.RegistryError, "retired shader symbol"):
                checker.check_shader({}, path, "fixture")

    def test_valid_channel_archive_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.zip"
            write_channel_archive(path)
            checker = validator.Validator(root)
            checker.check_archive({
                "capabilities": ["channels"],
                "id": "dev.termite.fixture",
                "kind": "channel",
                "version": "1.0.0",
            }, path, "fixture")

    def test_archive_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.zip"
            write_channel_archive(path, [archive_info("../escape")])
            checker = validator.Validator(root)
            with self.assertRaisesRegex(validator.RegistryError, "unsafe path"):
                checker.check_archive({
                    "capabilities": ["channels"],
                    "id": "dev.termite.fixture",
                    "kind": "channel",
                    "version": "1.0.0",
                }, path, "fixture")

    def test_archive_noncanonical_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.zip"
            write_channel_archive(path, [archive_info("fixture//extra")])
            checker = validator.Validator(root)
            with self.assertRaisesRegex(validator.RegistryError, "noncanonical"):
                checker.check_archive({
                    "capabilities": ["channels"],
                    "id": "dev.termite.fixture",
                    "kind": "channel",
                    "version": "1.0.0",
                }, path, "fixture")

    def test_archive_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.zip"
            write_channel_archive(
                path,
                [archive_info("fixture/link", stat.S_IFLNK | 0o777)],
            )
            checker = validator.Validator(root)
            with self.assertRaisesRegex(validator.RegistryError, "symlink"):
                checker.check_archive({
                    "capabilities": ["channels"],
                    "id": "dev.termite.fixture",
                    "kind": "channel",
                    "version": "1.0.0",
                }, path, "fixture")

    def test_archive_live_configuration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.zip"
            write_channel_archive(
                path,
                [archive_info("fixture/config.json")],
            )
            checker = validator.Validator(root)
            with self.assertRaisesRegex(validator.RegistryError, "forbidden file"):
                checker.check_archive({
                    "capabilities": ["channels"],
                    "id": "dev.termite.fixture",
                    "kind": "channel",
                    "version": "1.0.0",
                }, path, "fixture")

    def test_archive_duplicate_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(path, "w") as archive:
                    info = archive_info("fixture/value")
                    archive.writestr(info, b"one")
                    archive.writestr(info, b"two")
            checker = validator.Validator(root)
            with self.assertRaisesRegex(validator.RegistryError, "duplicate"):
                checker.check_archive({
                    "capabilities": ["channels"],
                    "id": "dev.termite.fixture",
                    "kind": "channel",
                    "version": "1.0.0",
                }, path, "fixture")


if __name__ == "__main__":
    unittest.main(verbosity=2)
