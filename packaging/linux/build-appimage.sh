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
  if command -v linuxdeploy >/dev/null 2>&1; then
    LINUXDEPLOY="$(command -v linuxdeploy)"
  elif [[ -f "linuxdeploy-${ARCH}.AppImage" ]]; then
    LINUXDEPLOY="$(pwd)/linuxdeploy-${ARCH}.AppImage"
  fi
fi
if [[ -z "$LINUXDEPLOY" ]]; then
  echo "linuxdeploy not found — set LINUXDEPLOY=/path/to/linuxdeploy-x86_64.AppImage" >&2
  exit 1
fi

# ImageMagick 7 renamed `convert` to `magick` (convert is a deprecated alias).
RENDER="$(command -v magick >/dev/null 2>&1 && echo magick || echo convert)"

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
"$RENDER" 'OctoUpdater.ico[8]' -resize 256x256 \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png"

install -m 0755 packaging/linux/AppRun "$APPDIR/AppRun"
install -m 0644 packaging/linux/OctoUpdater.desktop "$APPDIR/OctoUpdater.desktop"

# 3. Run linuxdeploy (bundles missing Qt/system libs, validates the desktop
#    entry, and builds the final AppImage)
echo "==> Running linuxdeploy"
[[ -x "$LINUXDEPLOY" ]] || chmod +x "$LINUXDEPLOY"
"$LINUXDEPLOY" --appdir "$APPDIR" \
  --desktop-file "$APPDIR/OctoUpdater.desktop" \
  --icon-file "$APPDIR/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png" \
  --output appimage

# linuxdeploy names the AppImage after the desktop entry's Name with spaces
# replaced by underscores and leaves it in the current directory — relocate it
# to the canonical dist/OctoUpdater-<arch>.AppImage path.
produced="$(ls -t ./*.AppImage 2>/dev/null | head -n 1 || true)"
if [[ -z "$produced" ]]; then
  echo "linuxdeploy did not produce an AppImage" >&2
  exit 1
fi
mv -f "$produced" "$OUT"

echo "==> Done: ${OUT}"
