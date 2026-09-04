# Contributing

Thank you for helping improve the cmdy Marketplace. Keep each proposal narrow,
auditable, and reproducible.

## Before opening a change

1. Do not include credentials, private URLs, generated caches, symlinks, or
   local `config.json` files.
2. Keep packages larger than GitHub's normal file limit in an immutable HTTPS
   release asset and pin that `.cmdyext` URL and its exact SHA-256.
3. Preserve published `dev.termite.*` package IDs and compatibility-facing
   Keychain/environment identifiers unless a separately reviewed migration is
   available.
4. For native code, include readable source, a minimal capability grant, an
   executable archive with one root directory, and the exact SHA-256.
5. Use MIT-compatible material and retain required attribution.

## Required checks

Run on macOS with Xcode command-line tools installed:

```sh
python3 -B tools/test-validator.py
python3 -B tools/validate.py
python3 -B tools/test-channels.py
python3 -B tools/test-channel-integrations.py
```

Also inspect the staged diff and confirm that every archive change is expected:

```sh
git diff --check
git diff --stat
shasum -a 256 dist/*.cmdyext
```

The validator requires the governed file set (`dist`, `shaders`, `themes`, and
`rigs`) to match `registry.json` exactly. It checks native archive layout,
identity, version, capabilities, executable mode, unsafe ZIP members, hashes,
source manifests, configuration examples, registry lock facts, and every shader
compile. A passing check is necessary but does not replace human review of
native code or visual review of shaders and themes.

## Review expectations

- Explain the user-visible behavior and why each capability is needed.
- Include focused tests for source changes.
- Avoid unrelated formatting or catalog churn.
- Never rewrite a published archive in place. Bump its semantic version and
  record a new hash.
- Treat `registry-lock.json` as a review record, not a generated convenience.
