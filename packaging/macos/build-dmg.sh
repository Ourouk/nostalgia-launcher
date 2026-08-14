#!/usr/bin/env bash
# Build a universal2 (arm64 + x86_64) macOS .dmg for Vanilla WoW Launcher.
#
# Must run on macOS with a *universal* Python/PySide6 environment, e.g.:
#   uv sync --dev
#   ./packaging/macos/build-dmg.sh
#
# Produces: dist/VanillaWoWLauncher-universal2.dmg
#
# The result is unsigned by default (macOS will warn on first open). Optional
# hooks (all via environment variables, none required):
#   CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#       ad-hoc/Developer-ID signs the .app before the DMG is built.
#   NOTARY_APPLE_ID, NOTARY_TEAM_ID, NOTARY_PASSWORD
#       submit the app to Apple notarization and staple it (requires the
#       signed .app + an app-specific password).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

APP_NAME="VanillaWoWLauncher"
APP="dist/${APP_NAME}.app"
EXE="$APP/Contents/MacOS/${APP_NAME}"
DMG="dist/${APP_NAME}-universal2.dmg"
STAGE="dist/dmg-staging"

# 1. App icon (macOS-only tooling: magick/convert + iconutil)
"$ROOT/packaging/macos/build-icons.sh"

# 2. PyInstaller universal2 .app bundle
echo "==> Building universal2 .app bundle"
uv sync --dev
uv run pyinstaller --noconfirm --clean VanillaWoWLauncher-macos.spec

# 3. Verify the binary is truly universal
echo "==> Verifying architectures"
ARCHS="$(lipo -archs "$EXE")"
if ! grep -q "x86_64" <<<"$ARCHS"; then
  echo "ERROR: bundle is missing x86_64 (got: $ARCHS) — need a universal Python/PySide6" >&2
  exit 1
fi
if ! grep -q "arm64" <<<"$ARCHS"; then
  echo "ERROR: bundle is missing arm64 (got: $ARCHS) — need a universal Python/PySide6" >&2
  exit 1
fi
echo "    arches: $ARCHS"

# 4. Optional code signing (ad-hoc or Developer ID)
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  echo "==> Codesigning with: ${CODESIGN_IDENTITY}"
  codesign --force --options runtime --sign "$CODESIGN_IDENTITY" "$APP"
  codesign --verify --deep --strict "$APP"
fi

# 5. Optional notarization (requires a signed build + Apple credentials)
if [[ -n "${NOTARY_APPLE_ID:-}" && -n "${NOTARY_TEAM_ID:-}" && -n "${NOTARY_PASSWORD:-}" ]]; then
  echo "==> Notarizing"
  ZIP="$STAGE/${APP_NAME}.zip"
  mkdir -p "$STAGE"
  ditto -c -k --keepParent "$APP" "$ZIP"
  xcrun notarytool submit "$ZIP" \
    --apple-id "$NOTARY_APPLE_ID" \
    --team-id "$NOTARY_TEAM_ID" \
    --password "$NOTARY_PASSWORD" \
    --wait
  xcrun stapler staple "$APP"
  rm -rf "$STAGE"
fi

# 6. Assemble the DMG
echo "==> Creating DMG"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "Vanilla WoW Launcher" \
  -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

echo "==> Done: ${DMG}"
