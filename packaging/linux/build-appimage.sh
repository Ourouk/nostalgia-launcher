#!/usr/bin/env bash
# Build an Octo Updater AppImage for Linux.
#
# Prerequisites:
#   - uv (or a Python env with pyinstaller) and ImageMagick (`convert`)
#   - a `linuxdeploy` AppImage matching your architecture on PATH or in
#     ./linuxdeploy-x86_64.AppImage (https://github.com/linuxdeploy/linuxdeploy)
#
# Produces: dist/OctoUpdater-x86_64.AppImage
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

APP_NAME="OctoUpdater"
ARCH="$(uname -m)"
OUT="dist/${APP_NAME}-${ARCH}.AppImage"
APPDIR="dist/${APP_NAME}.AppDir"

LINUXDEPLOY="${LINUXDEPLOY:-}"
if [[ -z "$LINUXDEPLOY" ]]; then
  for cand in linuxdeploy-${ARCH}.AppImage linuxdeploy; do
    if command -v "$cand" >/dev/null 2>&1; then LINUXDEPLOY="$cand"; break; fi
    if [[ -f "$cand" ]]; then LINUXDEPLOY="$cand"; break; fi
  done
fi
if [[ -z "$LINUXDEPLOY" ]]; then
  echo "linuxdeploy not found — set LINUXDEPLOY=/path/to/linuxdeploy-x86_64.AppImage" >&2
  exit 1
fi

# 1. PyInstaller onedir bundle
echo "==> Building PyInstaller onedir bundle"
uv sync --dev
uv run pyinstaller --noconfirm --clean OctoUpdater-linux.spec

# 2. Assemble the AppDir
echo "==> Assembling ${APPDIR}"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -a "dist/${APP_NAME}/." "$APPDIR/usr/bin/"

mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
convert OctoUpdater.ico -resize 256x256 \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png"

install -m 0755 packaging/linux/AppRun "$APPDIR/AppRun"
install -m 0644 packaging/linux/OctoUpdater.desktop "$APPDIR/OctoUpdater.desktop"

# 3. Run linuxdeploy (bundles missing Qt/system libs, validates the desktop
#    entry, and builds the final AppImage)
echo "==> Running linuxdeploy"
chmod +x "$LINUXDEPLOY"
"$LINUXDEPLOY" --appdir "$APPDIR" \
  --desktop-file "$APPDIR/OctoUpdater.desktop" \
  --icon-file "$APPDIR/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png" \
  --output appimage

echo "==> Done: ${OUT}"
