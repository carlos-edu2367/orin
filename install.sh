#!/usr/bin/env bash
# Distribution installer for a packaged Orin release on Linux.
#
# Mirrors install.ps1's flow (fetch manifest, download, verify SHA-256,
# stage, promote, shim, offer a launcher entry) using the Linux-native
# equivalents: ~/.local/bin instead of a registry PATH edit, and a
# freedesktop.org .desktop file instead of a .lnk shortcut.
set -euo pipefail

REPOSITORY="${ORIN_RELEASE_REPOSITORY:-carlos-edu2367/orin}"
# Overridable purely for testing against a local server. Production installs
# never set this. The origin-pin check below still only trusts whatever this
# resolves to -- overriding it points the *whole* flow (fetch and origin pin
# together) at a different base; it is not a way to accept an archive from an
# untrusted host while still claiming to trust the real one.
BASE_URL="${ORIN_RELEASE_BASE_URL:-https://github.com/$REPOSITORY/releases}"
PROGRAMS_ROOT="$HOME/.local/share/Orin/versions"
BIN_ROOT="$HOME/.local/bin"
SHIM="$BIN_ROOT/orin"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPS_DIR/orin-desktop.desktop"

VERSION="latest"
FORCE=0
UNINSTALL=0
WAIT_FOR_PID=0
NO_DESKTOP_SHORTCUT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --wait-for-pid) WAIT_FOR_PID="$2"; shift 2 ;;
    --no-desktop-shortcut) NO_DESKTOP_SHORTCUT=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

fetch_manifest() {
  local asset
  if [ "$VERSION" = "latest" ]; then
    asset="latest/download/release.json"
  else
    local normalized="${VERSION#v}"
    if ! [[ "$normalized" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
      echo "Version must use semantic version format, for example 0.1.0." >&2
      exit 1
    fi
    asset="download/v$normalized/release.json"
  fi
  curl -fsSL "$BASE_URL/$asset"
}

if [ "$UNINSTALL" = "1" ]; then
  if [ "$FORCE" != "1" ]; then
    read -r -p "Completely remove Orin, including all local data and configuration? [y/N] " answer
    case "$answer" in
      y|Y|yes|YES) ;;
      *) exit 0 ;;
    esac
  fi
  rm -f "$SHIM" "$DESKTOP_FILE"
  # Deferred removal: wait for the running instance's pid to exit (it may be
  # the process that invoked this script), then remove the versioned install
  # and its state directory. Backgrounded so this script returns immediately,
  # mirroring install.ps1's Start-DeferredRemoval.
  (
    if [ "$WAIT_FOR_PID" -gt 0 ] 2>/dev/null; then
      while kill -0 "$WAIT_FOR_PID" 2>/dev/null; do sleep 1; done
    fi
    for _ in $(seq 1 180); do
      rm -rf "$PROGRAMS_ROOT" "$HOME/.local/share/orin"
      [ -d "$PROGRAMS_ROOT" ] || [ -d "$HOME/.local/share/orin" ] || exit 0
      sleep 1
    done
  ) >/dev/null 2>&1 &
  disown
  echo "Orin removal was scheduled. The runtime, local data and configuration will be removed after Orin exits."
  exit 0
fi

manifest_json="$(fetch_manifest)"
install_version="$(echo "$manifest_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
if ! [[ "$install_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
  echo "Release manifest contains an invalid version." >&2
  exit 1
fi
archive_url="$(echo "$manifest_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("platforms",{}).get("linux-x64",{}).get("archive_url",""))')"
archive_sha256="$(echo "$manifest_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("platforms",{}).get("linux-x64",{}).get("archive_sha256",""))')"
if [ -z "$archive_url" ] || [ -z "$archive_sha256" ]; then
  echo "Release manifest is missing a linux-x64 build for this version." >&2
  exit 1
fi
if [[ "$archive_url" != "$BASE_URL/download/"* ]]; then
  echo "Release archive URL must belong to the official Orin release." >&2
  exit 1
fi
if ! [[ "$archive_sha256" =~ ^[A-Fa-f0-9]{64}$ ]]; then
  echo "Release manifest contains an invalid SHA-256." >&2
  exit 1
fi

target="$PROGRAMS_ROOT/$install_version"
staging="$target.staging"
download="$(mktemp -t "orin-$install_version-XXXXXX.tar.gz")"

mkdir -p "$PROGRAMS_ROOT" "$BIN_ROOT"
rm -rf "$staging"
cleanup() { rm -f "$download"; rm -rf "$staging"; }
trap cleanup EXIT

curl -fsSL "$archive_url" -o "$download"
actual_sha256="$(sha256sum "$download" | cut -d' ' -f1)"
expected_sha256="$(echo "$archive_sha256" | tr '[:upper:]' '[:lower:]')"
if [ "$actual_sha256" != "$expected_sha256" ]; then
  echo "Downloaded release hash does not match release.json." >&2
  exit 1
fi
mkdir -p "$staging"
tar -xzf "$download" -C "$staging"
runtime="$staging/resources/runtime/orin"
desktop="$staging/Orin Desktop"
if [ ! -f "$runtime" ] || [ ! -f "$desktop" ]; then
  echo "Release archive does not contain the required Orin runtime." >&2
  exit 1
fi
chmod +x "$runtime" "$desktop"
"$runtime" --version >/dev/null

if [ -d "$target" ]; then
  if [ "$FORCE" != "1" ]; then
    echo "Orin $install_version is already installed. Use --force to reinstall it." >&2
    exit 1
  fi
  rm -rf "$target"
fi
mv "$staging" "$target"
trap - EXIT
rm -f "$download"

current="$PROGRAMS_ROOT/current"
rm -f "$current"
ln -s "$target" "$current"

cat > "$SHIM" <<SHIM_EOF
#!/usr/bin/env bash
exec "$PROGRAMS_ROOT/current/resources/runtime/orin" "\$@"
SHIM_EOF
chmod +x "$SHIM"

case ":$PATH:" in
  *":$BIN_ROOT:"*) ;;
  *) echo "Add this to your shell config to use the 'orin' command: export PATH=\"$BIN_ROOT:\$PATH\"" ;;
esac

update_desktop_entry=0
if [ ! -f "$DESKTOP_FILE" ] && [ "$NO_DESKTOP_SHORTCUT" != "1" ]; then
  read -r -p "Do you want to add Orin Desktop to your application menu? [Y/n] " answer
  case "$answer" in
    n|N|no|NO) ;;
    *) update_desktop_entry=1 ;;
  esac
elif [ -f "$DESKTOP_FILE" ]; then
  update_desktop_entry=1
fi
if [ "$update_desktop_entry" = "1" ]; then
  mkdir -p "$APPS_DIR"
  cat > "$DESKTOP_FILE" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=Orin Desktop
Exec="$PROGRAMS_ROOT/current/resources/runtime/orin" --desktop
Icon=$PROGRAMS_ROOT/current/resources/runtime/_internal/web/orin-logo.png
Terminal=false
Categories=Development;
DESKTOP_EOF
fi

echo "Orin $install_version is installed. Run 'orin' or find Orin Desktop in your application menu."
