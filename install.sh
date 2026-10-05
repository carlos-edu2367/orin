#!/usr/bin/env bash
# One-line Orin installer for Linux: downloads the OrinSetup program from the
# release and runs it in text mode.
#
#   curl -fsSL https://github.com/carlos-edu2367/orin/releases/latest/download/install.sh | bash
#
# All installation logic (download, SHA-256 check, staged extraction, smoke
# tests, rollback, the `orin` command, the menu entry) lives in the Orin
# installer engine -- the same one behind `orin update`. This script only
# fetches and starts it. To remove Orin run `orin --uninstall`.
set -euo pipefail

REPOSITORY="${ORIN_RELEASE_REPOSITORY:-carlos-edu2367/orin}"
BASE_URL="${ORIN_RELEASE_BASE_URL:-https://github.com/$REPOSITORY/releases}"
ASSET="OrinSetup-linux-x64"

version="latest"
setup_args=(--silent)
uninstall=0

while [ $# -gt 0 ]; do
  case "$1" in
    --version) version="${2:?--version needs a value}"; shift 2 ;;
    --no-desktop-shortcut) setup_args+=(--no-shortcut); shift ;;
    --uninstall) uninstall=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ "$uninstall" = "1" ]; then
  orin="$HOME/.local/bin/orin"
  [ -x "$orin" ] || { echo "Orin does not appear to be installed ($orin was not found)." >&2; exit 1; }
  exec "$orin" --uninstall
fi

if [ "$(uname -m)" != "x86_64" ]; then
  echo "Orin is only published for Linux x86_64 (this machine is $(uname -m))." >&2
  exit 1
fi

if [ "$version" = "latest" ]; then
  url="$BASE_URL/latest/download/$ASSET"
else
  normalized="${version#v}"
  if ! [[ "$normalized" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
    echo "Version must use semantic version format, for example 0.5.0." >&2
    exit 1
  fi
  url="$BASE_URL/download/v$normalized/$ASSET"
  setup_args+=(--to "$normalized")
fi

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
curl -fsSL "$url" -o "$workdir/$ASSET" || { echo "Could not download the Orin installer from $url" >&2; exit 1; }
chmod +x "$workdir/$ASSET"
"$workdir/$ASSET" "${setup_args[@]}"
