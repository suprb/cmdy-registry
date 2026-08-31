# Compatibility contract

The public product and repository name is cmdy. Mutable catalog content uses
`cmdy/*` IDs, `cmdy` authorship, cmdy repository URLs, cmdy data directories,
and the `cmdy_main` / `CmdyUniforms` Metal ABI.

The audited launch snapshot also contains native packages previously published
with IDs in the `dev.termite.*` namespace. Those package IDs remain stable so
existing installations, update checks, permissions, and archive hashes do not
break. Several first-party connectors likewise retain `termite.*` macOS
Keychain services, `TERMITE_*` process environment names, and protocol markers.
The cmdy runtime supplies those compatibility interfaces.

The pinned ZIP archives are immutable legacy release artifacts. Public source
and documentation may use cmdy terminology while preserving code-level
compatibility identifiers needed by those archives. A future namespace
migration must use new package versions, explicit data/keychain migration, and
an independently reviewed registry update; it must not overwrite existing
archives.

`dev.termite.chromium` is not part of the launch catalog and is explicitly
rejected by the validator and lock file.
