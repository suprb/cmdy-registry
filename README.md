# cmdy registry

The public catalog for the cmdy Marketplace: shaders, themes, rigs, Channel
connectors, and native Extensions.

`registry.json` is the signed-off catalog input. Every local content file and
native archive is named there, native archives are pinned by SHA-256, and
`registry-lock.json` binds the audited launch snapshot and shader contract.
Validation is fail-closed: unlisted governed files, unsafe archives, retired
shader symbols, symlinks, secret-like example values, and unpinned URLs all fail.

## Catalog

The catalog contains 60 entries:

| Kind | Count | Location |
|---|---:|---|
| Shader | 30 | `shaders/cmdy/*.metal` |
| Theme | 4 | `themes/cmdy/*.json` |
| Rig | 2 | `rigs/cmdy/*.conf` |
| Channel | 19 | `plugins/*` source and `dist/*.cmdyext` packages |
| Extension | 5 | `dist/*.cmdyext` packages or pinned release assets |

Every native package uses the same installable `.cmdyext` format. Browser is a
normal removable Extension whose small activation package is pinned to the cmdy
GitHub Release by URL and SHA-256; Chromium itself stays sealed in the notarized
cmdy app.

## Validate

Validation requires macOS with Xcode command-line tools because every shader is
compiled against the current cmdy Metal ABI.

```sh
python3 -B tools/test-validator.py
python3 -B tools/validate.py
python3 -B tools/test-channels.py
python3 -B tools/test-channel-integrations.py
```

The integration suite uses loopback-only cmdy and provider probes. It does not
contact external services or need provider credentials.

## Add content

A user shader exports exactly one function:

```metal
float4 cmdy_main(float2 uv, float4 sceneColor,
                 constant CmdyUniforms &u,
                 texture2d<float> scene, sampler smp)
```

The preamble supplies `cmdy_hash`, `cmdy_palette`, and `cmdy_textMask`. Start
from a file in `shaders/cmdy`, add one `cmdy/<slug>` entry to `registry.json`,
then run the full validation commands above. Themes contain a name, background,
foreground, optional cursor and border, plus exactly 16 ANSI colors. Rigs use
the validator's bounded `key = value` vocabulary.

Native code receives stricter review. A Channel is a v1 Extension whose
manifest requests `channels`; two-way Channels also request `events.read`.
Build archives with `tools/pack-plugin.sh`, audit their contents, pin the exact
SHA-256 in `registry.json`, and update `registry-lock.json` only as part of an
explicit catalog review.

Provider credentials never belong in this repository or an archive. Copy a
connector's `config.example.json` to `config.json` only in the installed local
connector, and use the documented macOS Keychain service for secrets.

## Compatibility identifiers

The launch snapshot intentionally preserves native package IDs in the
`dev.termite.*` namespace and several `termite.*` Keychain or environment names.
Existing installations and pinned archive hashes depend on those identifiers.
They are compatibility interfaces, not the repository or product name. See
[COMPATIBILITY.md](COMPATIBILITY.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change. Security
issues follow [SECURITY.md](SECURITY.md). Code and seeded content are available
under the [MIT License](LICENSE).
