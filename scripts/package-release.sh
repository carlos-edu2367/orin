#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(python3 -c 'import json; print(json.load(open("desktop/package.json"))["version"])')"
fi
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
  echo "Version must use semantic version format, for example 0.1.0." >&2
  exit 1
fi

source="desktop/dist/linux-unpacked"
runtime="$source/resources/runtime/orin"
desktop="$source/Orin Desktop"
if [ ! -f "$runtime" ] || [ ! -f "$desktop" ]; then
  echo "The Electron directory package is incomplete. Run scripts/build-linux.sh first." >&2
  exit 1
fi

output="dist"
archive_name="Orin-$VERSION-linux-x64.tar.gz"
archive="$output/$archive_name"
mkdir -p "$output"
rm -f "$archive"
tar -czf "$archive" -C "$source" .
sha256="$(sha256sum "$archive" | cut -d' ' -f1)"

cat > "$output/linux-release.json" <<EOF
{
  "archive_name": "$archive_name",
  "archive_sha256": "$sha256"
}
EOF

echo "Release archive: $archive"
echo "Manifest fragment: $output/linux-release.json"
