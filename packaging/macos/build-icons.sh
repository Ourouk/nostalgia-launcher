#!/usr/bin/env bash
# Build the macOS .icns app icon from packaging/icons/VanillaWoWLauncher.png.
#
# Produces packaging/macos/VanillaWoWLauncher.icns (via iconutil, macOS-only).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="$ROOT/packaging/macos"
ICONSET="$OUT_DIR/VanillaWoWLauncher.iconset"
SRC="$ROOT/packaging/icons/VanillaWoWLauncher.png"
ICNS="$OUT_DIR/VanillaWoWLauncher.icns"

RENDER="$(command -v magick >/dev/null 2>&1 && echo magick || echo convert)"

rm -rf "$ICONSET"
mkdir -p "$ICONSET"

# iconutil expects icon_<base>x<base>.png plus the @2x (retina) variants.
for s in 16 32 128 256 512; do
  "$RENDER" -background none "$SRC" -resize "${s}x${s}" \
    "$ICONSET/icon_${s}x${s}.png"
  retina=$((s * 2))
  "$RENDER" -background none "$SRC" -resize "${retina}x${retina}" \
    "$ICONSET/icon_${s}x${s}@2x.png"
done

iconutil -c icns "$ICONSET" -o "$ICNS"
rm -rf "$ICONSET"

echo "==> Done: ${ICNS}"
