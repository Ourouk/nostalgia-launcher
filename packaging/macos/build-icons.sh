#!/usr/bin/env bash
# Build the macOS .icns app icon from packaging/icons/VanillaWoWLauncher.png.
#
# Produces packaging/macos/VanillaWoWLauncher.icns (via iconutil, macOS-only).
# Uses macOS-native `sips` when available, falling back to ImageMagick
# (magick/convert) on other systems.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="$ROOT/packaging/macos"
ICONSET="$OUT_DIR/VanillaWoWLauncher.iconset"
SRC="$ROOT/packaging/icons/VanillaWoWLauncher.png"
ICNS="$OUT_DIR/VanillaWoWLauncher.icns"

if command -v sips >/dev/null 2>&1; then
  RENDER="sips"
else
  RENDER="$(command -v magick >/dev/null 2>&1 && echo magick || echo convert)"
fi

rm -rf "$ICONSET"
mkdir -p "$ICONSET"

# iconutil expects icon_<base>x<base>.png plus the @2x (retina) variants.
for s in 16 32 128 256 512; do
  retina=$((s * 2))
  if [[ "$RENDER" == "sips" ]]; then
    sips -z "$s" "$s" "$SRC" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z "$retina" "$retina" "$SRC" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  else
    "$RENDER" -background none "$SRC" -resize "${s}x${s}" \
      "$ICONSET/icon_${s}x${s}.png"
    "$RENDER" -background none "$SRC" -resize "${retina}x${retina}" \
      "$ICONSET/icon_${s}x${s}@2x.png"
  fi
done

iconutil -c icns "$ICONSET" -o "$ICNS"
rm -rf "$ICONSET"

echo "==> Done: ${ICNS}"
