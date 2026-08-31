#!/bin/bash
# Create a deterministic-clean Marketplace archive from one Extension folder.
#
#   tools/pack-plugin.sh /path/to/extension dist/extension-1.0.0.zip
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <extension-directory> <output.zip>" >&2
  exit 64
fi

SOURCE_INPUT=$1
OUTPUT_INPUT=$2

if [ ! -d "$SOURCE_INPUT" ] || [ -L "$SOURCE_INPUT" ]; then
  echo "extension source must be a real directory: $SOURCE_INPUT" >&2
  exit 1
fi
if [ ! -f "$SOURCE_INPUT/manifest.json" ] || [ -L "$SOURCE_INPUT/manifest.json" ]; then
  echo "extension source has no real manifest.json: $SOURCE_INPUT" >&2
  exit 1
fi

SOURCE_DIRECTORY=$(cd "$(dirname "$SOURCE_INPUT")" && pwd -P)
SOURCE="$SOURCE_DIRECTORY/$(basename "$SOURCE_INPUT")"
mkdir -p "$(dirname "$OUTPUT_INPUT")"
OUTPUT_DIRECTORY=$(cd "$(dirname "$OUTPUT_INPUT")" && pwd -P)
OUTPUT="$OUTPUT_DIRECTORY/$(basename "$OUTPUT_INPUT")"

case "$OUTPUT" in
  *.zip) ;;
  *) echo "output must end in .zip: $OUTPUT" >&2; exit 1 ;;
esac

STAGING_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/cmdy-registry-pack.XXXXXX")
cleanup() {
  rm -rf -- "$STAGING_ROOT"
}
trap cleanup EXIT HUP INT TERM

PACKAGE="$STAGING_ROOT/$(basename "$SOURCE")"
ditto --norsrc --noextattr --noqtn --noacl "$SOURCE" "$PACKAGE"

# Framework payloads are distributed and reviewed separately. Never publish
# local configuration, caches, logs, environment files, or Finder metadata.
rm -rf -- "$PACKAGE/Frameworks"
find "$PACKAGE" -type d \( -name __pycache__ -o -name .pytest_cache \) \
  -prune -exec rm -rf -- {} +
find "$PACKAGE" -type f \( -name '*.pyc' -o -name '*.log' -o -name .DS_Store \
  -o -name config.json -o -name .env -o -name .env.local \) -delete

TEMPORARY_ARCHIVE="$STAGING_ROOT/archive.zip"
(
  cd "$STAGING_ROOT"
  ditto -c -k --keepParent --norsrc --noextattr --noqtn --noacl \
    "$(basename "$SOURCE")" "$TEMPORARY_ARCHIVE"
)
mv -f -- "$TEMPORARY_ARCHIVE" "$OUTPUT"
shasum -a 256 "$OUTPUT" | awk '{print $1}'
