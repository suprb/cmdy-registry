#!/usr/bin/env python3
"""Fail-closed validation for the public cmdy Marketplace registry."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


KINDS = ("shader", "theme", "rig", "channel", "plugin")
CONTENT_KINDS = ("shader", "theme", "rig")
NATIVE_KINDS = ("channel", "plugin")
EXPECTED_TOP_KEYS = {"api", "entries", "featured", "name"}
COMMON_KEYS = {
    "author", "description", "id", "kind", "license", "name",
    "version",
}
EXPECTED_ENTRY_KEYS = {
    "shader": COMMON_KEYS | {"file"},
    "theme": COMMON_KEYS | {"file"},
    "rig": COMMON_KEYS | {"file"},
    "channel": COMMON_KEYS | {"file"} | {
        "capabilities", "configuration", "homepage", "mode", "sdk",
        "setup", "sha256",
    },
    "plugin": COMMON_KEYS | {"arch", "sdk", "sha256"},
}
CONTENT_DIRECTORIES = {
    "shader": ("shaders/cmdy", ".metal"),
    "theme": ("themes/cmdy", ".json"),
    "rig": ("rigs/cmdy", ".conf"),
}
CHANNEL_MODES = {"two-way", "inbound-only", "read-only"}
CHANNEL_FIELD_TYPES = {
    "text", "secret", "boolean", "integer", "string-list", "integer-list",
    "path", "json",
}
FIELD_KEYS = {
    "default", "help", "key", "keychainService", "label", "placeholder",
    "required", "type",
}
REQUIRED_FIELD_KEYS = {"key", "label", "required", "type"}
ARCHITECTURES = {"arm64", "x86_64"}
CANONICAL_REPOSITORY = "https://github.com/suprb/cmdy-registry"
LEGACY_SOURCE_COMMIT = "8e8b9f2d3c561357a40624dfb837d923df971007"
SOURCE_SNAPSHOT_SHA256 = "ec1d22e8d5704ba52da6470a6e38bee89a3916b97a23203b54d68f4273d78645"
SEMVER_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
CONTENT_ID_RE = re.compile(r"cmdy/[a-z0-9][a-z0-9-]*\Z")
NATIVE_ID_RE = re.compile(r"dev\.termite\.[a-z0-9][a-z0-9-]*\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
FIELD_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
CAPABILITY_RE = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")
HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?\Z")
SECRET_KEY_RE = re.compile(
    r"(?:password|secret|token|api[_-]?key|bot[_-]?token)", re.IGNORECASE
)
FORBIDDEN_PUBLIC_TEXT = (
    "github.com/" + "andreas-pihlstrom/",
    "github.com/suprb/" + "termite-registry",
    "github.com/suprb/" + "term64-registry",
)
SOURCE_TEXT_SUFFIXES = {
    ".conf", ".json", ".md", ".metal", ".py", ".sh", ".yaml", ".yml",
}
MAX_ARCHIVE_FILES = 256
MAX_ARCHIVE_FILE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_DOWNLOAD_BYTES = 512 * 1024 * 1024
SHADER_ABI_PROBE = """
struct CmdyRasterOut {
    float4 position [[position]];
    float2 uv;
};
vertex CmdyRasterOut cmdy_user_vertex(
    uint vertexID [[vertex_id]],
    const device QuadVertex *vertices [[buffer(0)]],
    constant RasterUniforms &r [[buffer(1)]]) {
    QuadVertex v = vertices[vertexID];
    CmdyRasterOut out;
    float2 safeResolution = max(r.resolution, float2(1.0));
    out.position = float4(v.position.x / safeResolution.x * 2.0 - 1.0,
                          1.0 - v.position.y / safeResolution.y * 2.0,
                          0.0, 1.0);
    out.uv = v.uv;
    return out;
}
fragment float4 cmdy_user_fragment(
    CmdyRasterOut in [[stage_in]],
    texture2d<float> scene [[texture(0)]],
    sampler smp [[sampler(0)]],
    constant CmdyUniforms &u [[buffer(0)]]) {
    float4 sceneColor = scene.sample(smp, in.uv);
    return cmdy_main(in.uv, sceneColor, u, scene, smp);
}
"""


class RegistryError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path, label: str) -> dict[str, object]:
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise RegistryError(f"{label} is missing: {path}") from error
    if path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise RegistryError(f"{label} must be a real regular file: {path}")
    raw = path.read_bytes()
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=object_without_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RegistryError) as error:
        raise RegistryError(f"{label} is not valid duplicate-free UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise RegistryError(f"{label} root must be an object")
    return value


def exact_keys(value: dict[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RegistryError(
            f"{label} fields differ (missing={missing}, extra={extra})"
        )


def real_file(path: Path, root: Path, label: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise RegistryError(f"{label} leaves the repository: {path}") from error
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError as error:
            raise RegistryError(f"{label} is missing: {relative.as_posix()}") from error
        if stat.S_ISLNK(info.st_mode):
            raise RegistryError(f"{label} has a symlink component: {relative.as_posix()}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RegistryError(f"{label} must be a regular file: {relative.as_posix()}")


def safe_relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegistryError(f"{label} must be a nonempty path")
    if "\\" in value or any(ord(character) < 32 for character in value):
        raise RegistryError(f"{label} is not a canonical POSIX path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value or any(
        part in ("", ".", "..") for part in pure.parts
    ):
        raise RegistryError(f"{label} is not a safe relative path: {value!r}")
    return value


def safe_external_archive_url(value: object, expected_name: str, label: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise RegistryError(f"{label} must be a nonempty HTTPS URL")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment
        or parsed.path.rsplit("/", 1)[-1] != expected_name
    ):
        raise RegistryError(
            f"{label} must be an HTTPS URL ending in /{expected_name} without credentials, query, or fragment"
        )
    return value


def nonempty_text(value: object, label: str, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RegistryError(f"{label} must be a nonempty trimmed string")
    if len(value.encode("utf-8")) > maximum or any(ord(c) < 32 for c in value):
        raise RegistryError(f"{label} is invalid or exceeds {maximum} bytes")
    return value


def exact_string_list(
    value: object, label: str, *, allowed: set[str] | None = None
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RegistryError(f"{label} must be a nonempty array")
    result: list[str] = []
    for index, item in enumerate(value):
        text = nonempty_text(item, f"{label}[{index}]", 128)
        if allowed is not None and text not in allowed:
            raise RegistryError(f"{label}[{index}] is unsupported: {text}")
        result.append(text)
    if len(set(result)) != len(result):
        raise RegistryError(f"{label} contains duplicates")
    return result


def check_default_type(field: dict[str, object], label: str) -> None:
    if "default" not in field:
        return
    value = field["default"]
    field_type = field["type"]
    valid = {
        "text": isinstance(value, str),
        "secret": False,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "string-list": isinstance(value, list)
            and all(isinstance(item, str) for item in value),
        "integer-list": isinstance(value, list)
            and all(isinstance(item, int) and not isinstance(item, bool) for item in value),
        "path": isinstance(value, str),
        "json": True,
    }.get(str(field_type), False)
    if not valid:
        raise RegistryError(f"{label}.default does not match type {field_type!r}")


def check_example_secrets(value: object, label: str, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            field = f"{path}.{key}" if path else key
            if SECRET_KEY_RE.search(key) and child not in ("", None, False, [], {}):
                raise RegistryError(f"{label} contains a secret-like value at {field}")
            check_example_secrets(child, label, field)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_example_secrets(child, label, f"{path}[{index}]")


class Validator:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.errors: list[str] = []
        self.entries: list[dict[str, object]] = []
        self.ids: set[str] = set()
        self.expected_files: set[str] = set()
        self.source_channel_ids: set[str] = set()
        self.compiled_shaders = 0
        self.archive_count = 0
        self.archive_bytes = 0

    def error(self, message: str) -> None:
        self.errors.append(message)
        print(f"  x {message}", file=sys.stderr)

    def guard(self, function, *args):
        try:
            return function(*args)
        except (OSError, RegistryError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
            self.error(str(error))
            return None

    def validate(self) -> None:
        self.guard(self.check_repository_paths)
        registry = self.guard(load_json, self.root / "registry.json", "registry")
        self.guard(self.check_schema)
        if registry is not None:
            self.guard(self.check_registry, registry)
            self.guard(self.check_governed_file_set)
            self.guard(self.check_source_channels)
            self.guard(self.check_lock, registry)
        self.guard(self.check_public_text)
        if self.errors:
            raise RegistryError(f"registry validation failed with {len(self.errors)} error(s)")

    def check_repository_paths(self) -> None:
        for path in self.root.rglob("*"):
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                continue
            if relative.parts and relative.parts[0] == ".git":
                continue
            if path.is_symlink():
                raise RegistryError(f"repository symlink is forbidden: {relative.as_posix()}")
            if path.name in {".DS_Store", "__pycache__"} or path.suffix == ".pyc":
                raise RegistryError(f"generated/junk path is forbidden: {relative.as_posix()}")

    def check_schema(self) -> None:
        schema_path = self.root / "schema/registry-v1.schema.json"
        schema = load_json(schema_path, "registry schema")
        exact_keys(schema, {
            "$defs", "$id", "$schema", "additionalProperties", "properties",
            "required", "title", "type",
        }, "registry schema")
        if schema["$schema"] != "https://json-schema.org/draft/2020-12/schema":
            raise RegistryError("registry schema must use JSON Schema 2020-12")
        if schema["$id"] != (
            "https://raw.githubusercontent.com/suprb/cmdy-registry/main/"
            "schema/registry-v1.schema.json"
        ):
            raise RegistryError("registry schema has the wrong canonical $id")
        definitions = schema["$defs"]
        if not isinstance(definitions, dict) or set(definitions) != {
            "arch", "capabilities", "channel", "configuration",
            "configurationField", "id", "path", "plugin", "rig", "shader",
            "sha256", "text", "theme", "version",
        }:
            raise RegistryError("registry schema definitions differ")

        def check_references(value: object) -> None:
            if isinstance(value, dict):
                reference = value.get("$ref")
                if reference is not None and (
                    not isinstance(reference, str)
                    or not reference.startswith("#/$defs/")
                    or reference.removeprefix("#/$defs/") not in definitions
                ):
                    raise RegistryError(f"registry schema has unresolved reference {reference!r}")
                for child in value.values():
                    check_references(child)
            elif isinstance(value, list):
                for child in value:
                    check_references(child)

        check_references(schema)

    def check_registry(self, registry: dict[str, object]) -> None:
        exact_keys(registry, EXPECTED_TOP_KEYS, "registry")
        if registry["api"] != 1 or isinstance(registry["api"], bool):
            raise RegistryError("registry.api must be integer 1")
        if registry["name"] != "the cmdy marketplace":
            raise RegistryError("registry.name must be 'the cmdy marketplace'")
        raw_entries = registry["entries"]
        if not isinstance(raw_entries, list) or not raw_entries:
            raise RegistryError("registry.entries must be a nonempty array")
        for index, value in enumerate(raw_entries):
            if not isinstance(value, dict):
                self.error(f"entry[{index}] must be an object")
                continue
            try:
                self.check_entry(value, index)
                self.entries.append(value)
            except (OSError, RegistryError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
                self.error(str(error))
        featured = registry["featured"]
        if not isinstance(featured, list):
            raise RegistryError("registry.featured must be an array")
        featured_ids: list[str] = []
        for index, value in enumerate(featured):
            featured_ids.append(nonempty_text(value, f"featured[{index}]", 180))
        if len(featured_ids) != len(set(featured_ids)):
            raise RegistryError("registry.featured contains duplicates")
        unknown = sorted(set(featured_ids) - self.ids)
        if unknown:
            raise RegistryError(f"featured IDs are absent from entries: {unknown}")

    def check_entry(self, entry: dict[str, object], index: int) -> None:
        kind = entry.get("kind")
        label = f"entry[{index}] {entry.get('id', '?')}"
        if kind not in KINDS:
            raise RegistryError(f"{label}: unsupported kind {kind!r}")
        assert isinstance(kind, str)
        expected_keys = EXPECTED_ENTRY_KEYS[kind]
        if kind == "plugin":
            locations = set(entry) & {"file", "url"}
            if len(locations) != 1:
                raise RegistryError(f"{label} must declare exactly one of file or url")
            expected_keys = expected_keys | locations
        exact_keys(entry, expected_keys, label)
        identifier = nonempty_text(entry["id"], f"{label}.id", 180)
        if identifier in self.ids:
            raise RegistryError(f"duplicate entry id: {identifier}")
        self.ids.add(identifier)
        for field in ("name", "description", "author"):
            nonempty_text(entry[field], f"{label}.{field}")
        if entry["license"] != "MIT":
            raise RegistryError(f"{label}.license must be MIT for the launch catalog")
        if not isinstance(entry["version"], str) or not SEMVER_RE.fullmatch(entry["version"]):
            raise RegistryError(f"{label}.version must be x.y.z")
        if "file" in entry:
            relative = safe_relative(entry["file"], f"{label}.file")
            real_file(self.root / relative, self.root, f"{label}.file")
            if relative in self.expected_files:
                raise RegistryError(f"{label}.file is referenced by more than one entry")
            self.expected_files.add(relative)
        if kind in CONTENT_KINDS:
            self.check_content(entry, label)
        else:
            self.check_native(entry, label)

    def check_content(self, entry: dict[str, object], label: str) -> None:
        kind = str(entry["kind"])
        identifier = str(entry["id"])
        if not CONTENT_ID_RE.fullmatch(identifier):
            raise RegistryError(f"{label}.id must use cmdy/<slug>")
        directory, extension = CONTENT_DIRECTORIES[kind]
        slug = identifier.split("/", 1)[1]
        expected = f"{directory}/{slug}{extension}"
        if entry["file"] != expected:
            raise RegistryError(f"{label}.file must be {expected}")
        path = self.root / expected
        if kind == "shader":
            self.check_shader(entry, path, label)
        elif kind == "theme":
            self.check_theme(entry, path, label)
        else:
            self.check_rig(path, label)

    def check_shader(self, entry: dict[str, object], path: Path, label: str) -> None:
        source = path.read_text(encoding="utf-8")
        if len(source.encode("utf-8")) > 256 * 1024:
            raise RegistryError(f"{label} shader exceeds 256 KiB")
        if len(re.findall(r"\bfloat4\s+cmdy_main\s*\(", source)) != 1:
            raise RegistryError(f"{label} must define exactly one float4 cmdy_main(...)")
        for retired in ("termite_main", "TermiteUniforms", "termite_"):
            if retired in source:
                raise RegistryError(f"{label} contains retired shader symbol {retired}")
        if "#include" in source:
            raise RegistryError(f"{label} may not add preprocessor includes")
        preamble = (self.root / "tools/preamble.metal").read_text(encoding="utf-8")
        metal = shutil.which("xcrun")
        if metal is None:
            raise RegistryError("xcrun is required to compile marketplace shaders")
        with tempfile.TemporaryDirectory(prefix="cmdy-registry-metal-") as temp:
            combined = Path(temp) / f"{path.stem}.metal"
            output = Path(temp) / f"{path.stem}.air"
            combined.write_text(
                preamble + "\n" + source + "\n" + SHADER_ABI_PROBE,
                encoding="utf-8",
            )
            result = subprocess.run(
                [metal, "-sdk", "macosx", "metal", "-c", str(combined), "-o", str(output)],
                check=False, capture_output=True, text=True,
            )
            if result.returncode != 0 or not output.is_file():
                detail = (result.stderr or result.stdout).strip()
                raise RegistryError(f"{label} shader does not compile: {detail[:800]}")
        self.compiled_shaders += 1

    def check_theme(self, entry: dict[str, object], path: Path, label: str) -> None:
        theme = load_json(path, f"{label} theme")
        allowed = {"ansi", "background", "border", "cursor", "foreground", "name"}
        required = {"ansi", "background", "foreground", "name"}
        if not required.issubset(theme) or not set(theme).issubset(allowed):
            raise RegistryError(f"{label} theme has missing or unsupported fields")
        if theme["name"] != entry["name"]:
            raise RegistryError(f"{label} theme name differs from registry metadata")
        for field in ("background", "foreground", "cursor", "border"):
            if field in theme and (
                not isinstance(theme[field], str) or not HEX_COLOR_RE.fullmatch(theme[field])
            ):
                raise RegistryError(f"{label} theme {field} is not #RRGGBB/#RRGGBBAA")
        ansi = theme["ansi"]
        if not isinstance(ansi, list) or len(ansi) != 16 or any(
            not isinstance(value, str) or not HEX_COLOR_RE.fullmatch(value)
            for value in ansi
        ):
            raise RegistryError(f"{label} theme needs exactly 16 valid ANSI colors")

    def check_rig(self, path: Path, label: str) -> None:
        allowed = {
            "blur", "cursor-blink", "cursor-style", "font-family", "font-size",
            "ghost-text", "hide-border", "line-height", "margin", "opacity",
            "shader", "smooth-cursor", "theme",
        }
        seen: set[str] = set()
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise RegistryError(f"{label} rig line {number} is not key = value")
            key, value = (part.strip() for part in line.split("=", 1))
            if key not in allowed:
                raise RegistryError(f"{label} rig line {number} uses unknown key {key!r}")
            if key in seen or not value:
                raise RegistryError(f"{label} rig line {number} duplicates a key or has no value")
            seen.add(key)

    def check_native(self, entry: dict[str, object], label: str) -> None:
        identifier = str(entry["id"])
        if not NATIVE_ID_RE.fullmatch(identifier):
            raise RegistryError(f"{label}.id must preserve stable dev.termite.<slug> identity")
        if entry["author"] != "cmdy" or entry["sdk"] != "v1":
            raise RegistryError(f"{label} must use author=cmdy and sdk=v1")
        digest = entry["sha256"]
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise RegistryError(f"{label}.sha256 must be lowercase SHA-256")
        slug = identifier.removeprefix("dev.termite.")
        archive_name = f"{slug}-{entry['version']}.cmdyext"
        expected_file = f"dist/{archive_name}"
        if "file" in entry:
            if entry["file"] != expected_file:
                raise RegistryError(f"{label}.file must be {expected_file}")
            path = self.root / expected_file
            self.check_native_archive(entry, path, digest, label)
        else:
            safe_external_archive_url(entry.get("url"), archive_name, f"{label}.url")
            override = os.environ.get("CMDY_REGISTRY_EXTERNAL_ASSET_DIR")
            if override:
                root = Path(override).expanduser().resolve()
                path = root / archive_name
                real_file(path, root, f"{label}.url override")
                self.check_native_archive(entry, path, digest, label)
            else:
                with tempfile.TemporaryDirectory(prefix="cmdy-registry-download-") as temp:
                    path = Path(temp) / archive_name
                    self.download_archive(str(entry["url"]), path, label)
                    self.check_native_archive(entry, path, digest, label)

    def download_archive(self, url: str, path: Path, label: str) -> None:
        request = urllib.request.Request(
            url, headers={"User-Agent": "cmdy-registry-validator/1"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, path.open("wb") as output:
                if urllib.parse.urlsplit(response.geturl()).scheme != "https":
                    raise RegistryError(f"{label} archive redirected away from HTTPS")
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_DOWNLOAD_BYTES:
                        raise RegistryError(f"{label} archive download exceeds 512 MiB")
                    output.write(chunk)
        except RegistryError:
            raise
        except Exception as error:
            raise RegistryError(f"{label} archive download failed: {error}") from error

    def check_native_archive(
        self, entry: dict[str, object], path: Path, digest: str, label: str
    ) -> None:
        actual = sha256_file(path)
        if actual != digest:
            raise RegistryError(f"{label} archive SHA-256 is {actual}, expected {digest}")
        if entry["kind"] == "plugin":
            exact_string_list(entry["arch"], f"{label}.arch", allowed=ARCHITECTURES)
        else:
            slug = str(entry["id"]).removeprefix("dev.termite.")
            self.check_channel_metadata(entry, label, slug)
        self.check_archive(entry, path, label)
        self.archive_count += 1
        self.archive_bytes += path.stat().st_size

    def check_channel_metadata(
        self, entry: dict[str, object], label: str, slug: str
    ) -> None:
        expected_homepage = f"{CANONICAL_REPOSITORY}/tree/main/plugins/{slug}"
        if entry["homepage"] != expected_homepage:
            raise RegistryError(f"{label}.homepage must be {expected_homepage}")
        capabilities = exact_string_list(entry["capabilities"], f"{label}.capabilities")
        if any(not CAPABILITY_RE.fullmatch(value) for value in capabilities):
            raise RegistryError(f"{label}.capabilities contains an invalid capability")
        if "channels" not in capabilities:
            raise RegistryError(f"{label} must request channels")
        mode = entry["mode"]
        if mode not in CHANNEL_MODES:
            raise RegistryError(f"{label}.mode is unsupported")
        if mode == "two-way" and "events.read" not in capabilities:
            raise RegistryError(f"{label} two-way Channel needs events.read")
        if mode != "two-way" and "events.read" in capabilities:
            raise RegistryError(f"{label} non-replying Channel may not request events.read")
        nonempty_text(entry["setup"], f"{label}.setup", 512)
        configuration = entry["configuration"]
        if not isinstance(configuration, dict):
            raise RegistryError(f"{label}.configuration must be an object")
        exact_keys(configuration, {"fields", "version"}, f"{label}.configuration")
        if configuration["version"] != 1 or isinstance(configuration["version"], bool):
            raise RegistryError(f"{label}.configuration.version must be integer 1")
        fields = configuration["fields"]
        if not isinstance(fields, list) or len(fields) > 32:
            raise RegistryError(f"{label}.configuration.fields must contain at most 32 fields")
        seen: set[str] = set()
        for index, field in enumerate(fields):
            field_label = f"{label}.configuration.fields[{index}]"
            if not isinstance(field, dict):
                raise RegistryError(f"{field_label} must be an object")
            if not REQUIRED_FIELD_KEYS.issubset(field) or not set(field).issubset(FIELD_KEYS):
                raise RegistryError(f"{field_label} has missing or unsupported fields")
            key = field["key"]
            if not isinstance(key, str) or not FIELD_KEY_RE.fullmatch(key):
                raise RegistryError(f"{field_label}.key is invalid")
            if key in seen:
                raise RegistryError(f"{label} duplicates configuration key {key}")
            seen.add(key)
            nonempty_text(field["label"], f"{field_label}.label", 256)
            if field["type"] not in CHANNEL_FIELD_TYPES:
                raise RegistryError(f"{field_label}.type is unsupported")
            if not isinstance(field["required"], bool):
                raise RegistryError(f"{field_label}.required must be boolean")
            for optional in ("help", "placeholder"):
                if optional in field:
                    nonempty_text(field[optional], f"{field_label}.{optional}", 512)
            service = field.get("keychainService")
            if field["type"] == "secret":
                if not isinstance(service, str) or not re.fullmatch(
                    r"(?:cmdy|termite)\.[a-z0-9][a-z0-9.-]*", service
                ):
                    raise RegistryError(f"{field_label} secret needs a stable Keychain service")
            elif service is not None:
                raise RegistryError(f"{field_label} non-secret may not declare Keychain storage")
            check_default_type(field, field_label)

    def check_archive(
        self, entry: dict[str, object], path: Path, label: str
    ) -> None:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_FILES:
                raise RegistryError(f"{label} archive file count is invalid")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise RegistryError(f"{label} archive contains duplicate paths")
            total = 0
            roots: set[str] = set()
            for info in infos:
                name = info.filename
                if "\\" in name or "\x00" in name:
                    raise RegistryError(f"{label} archive has a non-POSIX path")
                member = PurePosixPath(name)
                if member.is_absolute() or any(part in ("", ".", "..") for part in member.parts):
                    raise RegistryError(f"{label} archive has unsafe path {name!r}")
                canonical_name = member.as_posix() + ("/" if info.is_dir() else "")
                if name != canonical_name:
                    raise RegistryError(f"{label} archive has noncanonical path {name!r}")
                roots.add(member.parts[0])
                if info.flag_bits & 0x1:
                    raise RegistryError(f"{label} archive contains encrypted member {name!r}")
                if info.file_size > MAX_ARCHIVE_FILE_BYTES:
                    raise RegistryError(f"{label} archive member is too large: {name!r}")
                if (
                    info.file_size > 1024 * 1024
                    and info.compress_size * 1000 < info.file_size
                ):
                    raise RegistryError(f"{label} archive member has an unsafe compression ratio: {name!r}")
                total += info.file_size
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise RegistryError(f"{label} archive contains symlink {name!r}")
                if mode and not (
                    stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_IFMT(mode) == 0
                ):
                    raise RegistryError(f"{label} archive contains special file {name!r}")
                if member.name in {"config.json", ".env", ".env.local", ".DS_Store"}:
                    raise RegistryError(f"{label} archive contains forbidden file {name!r}")
            if total > MAX_ARCHIVE_TOTAL_BYTES or len(roots) != 1:
                raise RegistryError(f"{label} archive size/root layout is invalid")
            corrupt = archive.testzip()
            if corrupt is not None:
                raise RegistryError(f"{label} archive member fails CRC: {corrupt!r}")
            root = next(iter(roots))
            manifest_name = f"{root}/manifest.json"
            if manifest_name not in names:
                raise RegistryError(f"{label} archive has no root manifest.json")
            try:
                manifest = json.loads(
                    archive.read(manifest_name).decode("utf-8"),
                    object_pairs_hook=object_without_duplicates,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, RegistryError) as error:
                raise RegistryError(f"{label} archive manifest is invalid: {error}") from error
            if not isinstance(manifest, dict) or manifest.get("id") != entry["id"]:
                raise RegistryError(f"{label} archive manifest ID differs")
            if entry["kind"] == "channel":
                if manifest.get("manifestVersion") != 1 or manifest.get("version") != entry["version"]:
                    raise RegistryError(f"{label} Channel archive manifest version differs")
                capabilities = manifest.get("capabilities")
                if not isinstance(capabilities, list) or set(capabilities) != set(entry["capabilities"]):
                    raise RegistryError(f"{label} Channel archive capabilities differ")
                executable = manifest.get("entrypoint")
            else:
                executable = manifest.get("exec")
            if not isinstance(executable, str) or not executable:
                raise RegistryError(f"{label} archive manifest has no executable")
            safe_relative(executable, f"{label} archive executable")
            executable_path = f"{root}/{executable}"
            if executable_path not in names:
                raise RegistryError(f"{label} archive executable is missing")
            executable_info = archive.getinfo(executable_path)
            executable_mode = executable_info.external_attr >> 16
            if stat.S_ISLNK(executable_mode) or not executable_mode & 0o111:
                raise RegistryError(f"{label} archive executable is not a real executable file")

    def check_governed_file_set(self) -> None:
        actual: set[str] = set()
        for directory in ("dist", "shaders", "themes", "rigs"):
            root = self.root / directory
            if not root.is_dir() or root.is_symlink():
                raise RegistryError(f"governed directory is missing or symlinked: {directory}")
            for path in root.rglob("*"):
                if path.is_file():
                    actual.add(path.relative_to(self.root).as_posix())
        if actual != self.expected_files:
            raise RegistryError(
                "governed file set differs "
                f"(missing={sorted(self.expected_files - actual)}, "
                f"extra={sorted(actual - self.expected_files)})"
            )
        for retired in ("shaders/termite", "themes/termite", "rigs/termite"):
            if (self.root / retired).exists():
                raise RegistryError(f"retired data-package directory remains: {retired}")

    def check_source_channels(self) -> None:
        channel_entries = {
            str(entry["id"]): entry for entry in self.entries
            if entry.get("kind") == "channel"
        }
        plugins_root = self.root / "plugins"
        if not plugins_root.is_dir() or plugins_root.is_symlink():
            raise RegistryError("plugins source directory is missing or symlinked")
        actual_directories = {
            path.name for path in plugins_root.iterdir() if path.is_dir()
        }
        expected_directories = {
            identifier.removeprefix("dev.termite.") for identifier in channel_entries
        }
        if actual_directories != expected_directories:
            raise RegistryError(
                "Channel source directories differ "
                f"(missing={sorted(expected_directories - actual_directories)}, "
                f"extra={sorted(actual_directories - expected_directories)})"
            )
        for identifier, entry in sorted(channel_entries.items()):
            slug = identifier.removeprefix("dev.termite.")
            source = plugins_root / slug
            manifest = load_json(source / "manifest.json", f"{identifier} source manifest")
            if manifest.get("id") != identifier or manifest.get("version") != entry["version"]:
                raise RegistryError(f"{identifier} source manifest identity/version differs")
            if manifest.get("manifestVersion") != 1:
                raise RegistryError(f"{identifier} source manifest must use v1")
            if set(manifest.get("capabilities", [])) != set(entry["capabilities"]):
                raise RegistryError(f"{identifier} source capabilities differ")
            expected_homepage = f"{CANONICAL_REPOSITORY}/tree/main/plugins/{slug}"
            if manifest.get("homepage") != expected_homepage:
                raise RegistryError(f"{identifier} source homepage differs")
            example = source / "config.example.json"
            if example.exists():
                config = load_json(example, f"{identifier} example configuration")
                check_example_secrets(config, f"{identifier} example configuration")

    def check_lock(self, registry: dict[str, object]) -> None:
        lock = load_json(self.root / "registry-lock.json", "registry lock")
        exact_keys(lock, {
            "contentSha256", "entryCount", "excludedIDs", "kindCounts",
            "legacySourceCommit", "preambleSha256", "registrySha256", "schemaVersion",
            "schemaSha256", "sourceSnapshotSha256",
        }, "registry lock")
        if lock["schemaVersion"] != 1 or isinstance(lock["schemaVersion"], bool):
            raise RegistryError("registry lock schemaVersion must be integer 1")
        if lock["registrySha256"] != sha256_file(self.root / "registry.json"):
            raise RegistryError("registry lock does not bind registry.json")
        if lock["preambleSha256"] != sha256_file(self.root / "tools/preamble.metal"):
            raise RegistryError("registry lock does not bind the shader preamble")
        if lock["schemaSha256"] != sha256_file(self.root / "schema/registry-v1.schema.json"):
            raise RegistryError("registry lock does not bind the registry schema")
        content_paths = {
            str(entry["file"]) for entry in self.entries
            if entry.get("kind") in CONTENT_KINDS
        }
        content_hashes = lock["contentSha256"]
        if not isinstance(content_hashes, dict) or set(content_hashes) != content_paths:
            raise RegistryError("registry lock content file set differs")
        for relative in sorted(content_paths):
            digest = content_hashes[relative]
            if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                raise RegistryError(f"registry lock content hash is invalid: {relative}")
            if digest != sha256_file(self.root / relative):
                raise RegistryError(f"registry lock content hash differs: {relative}")
        for field in (
            "registrySha256", "preambleSha256", "schemaSha256",
            "sourceSnapshotSha256",
        ):
            if not isinstance(lock[field], str) or not SHA256_RE.fullmatch(lock[field]):
                raise RegistryError(f"registry lock {field} is not SHA-256")
        if lock["legacySourceCommit"] != LEGACY_SOURCE_COMMIT:
            raise RegistryError("registry lock legacySourceCommit differs from audited input")
        if lock["sourceSnapshotSha256"] != SOURCE_SNAPSHOT_SHA256:
            raise RegistryError("registry lock sourceSnapshotSha256 differs from audited input")
        if lock["entryCount"] != len(self.entries) or isinstance(lock["entryCount"], bool):
            raise RegistryError("registry lock entryCount differs")
        counts = dict(sorted(collections.Counter(str(entry["kind"]) for entry in self.entries).items()))
        if lock["kindCounts"] != counts:
            raise RegistryError("registry lock kindCounts differ")
        excluded = lock["excludedIDs"]
        if excluded != []:
            raise RegistryError("registry lock excludedIDs must be empty")
        if any(value in self.ids for value in excluded):
            raise RegistryError("an explicitly excluded registry ID is present")

    def check_public_text(self) -> None:
        for path in self.root.rglob("*"):
            relative = path.relative_to(self.root)
            if relative.parts and relative.parts[0] == ".git":
                continue
            if not path.is_file() or path.suffix.lower() not in SOURCE_TEXT_SUFFIXES:
                continue
            if path.stat().st_size > 4 * 1024 * 1024:
                raise RegistryError(f"public text file is unexpectedly large: {relative}")
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise RegistryError(f"public text file is not UTF-8: {relative}") from error
            for forbidden in FORBIDDEN_PUBLIC_TEXT:
                if forbidden in text:
                    raise RegistryError(
                        f"legacy public repository URL remains in {relative}: {forbidden}"
                    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent,
        help="registry repository root",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    validator = Validator(arguments.root)
    try:
        validator.validate()
    except RegistryError as error:
        print(str(error), file=sys.stderr)
        return 1
    counts = collections.Counter(str(entry["kind"]) for entry in validator.entries)
    rendered_counts = " ".join(f"{kind}s={counts[kind]}" for kind in KINDS)
    print(
        "REGISTRY_VALID "
        f"entries={len(validator.entries)} {rendered_counts} "
        f"archives={validator.archive_count} archiveBytes={validator.archive_bytes} "
        f"compiledShaders={validator.compiled_shaders} "
        f"registrySha256={sha256_file(validator.root / 'registry.json')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
