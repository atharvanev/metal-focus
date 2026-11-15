#!/usr/bin/env bash
# Build, bundle and sign MetalFocusBar.app. Usage: app/build-app.sh [--install]
set -euo pipefail
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
PKG="$REPO/app/MetalFocusBar"
OUT="$REPO/app/build/MetalFocusBar.app"
# The Apple Development cert (team TLUW48C678), expires 2027-11-14. Selected by SHA-1
# because an expired cert with the same name is still in the keychain. Override with
# METAL_FOCUS_SIGN_ID (a hash or name, e.g. "Metal Focus Dev" from app/make-cert.sh).
IDENTITY="${METAL_FOCUS_SIGN_ID:-959415F6F9C8607CEC68AE73D5283A8B4A43E96C}"

if ! security find-identity -v -p codesigning | grep -q "$IDENTITY"; then
  echo "Signing identity \"$IDENTITY\" not found or not valid (expired?). Check: security find-identity -v -p codesigning" >&2; exit 1
fi

swift build -c release --disable-keychain --package-path "$PKG"
BIN=$(swift build -c release --disable-keychain --package-path "$PKG" --show-bin-path)/MetalFocusBar

rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources" "$OUT/Contents/Frameworks"
cp "$BIN" "$OUT/Contents/MacOS/MetalFocusBar"
# SQLCipher ships as a dynamic framework: embed it and point the rpath at it.
ditto "$(dirname "$BIN")/SQLCipher.framework" "$OUT/Contents/Frameworks/SQLCipher.framework"
install_name_tool -add_rpath "@executable_path/../Frameworks" "$OUT/Contents/MacOS/MetalFocusBar"
cp "$REPO/app/Info.plist.in" "$OUT/Contents/Info.plist"
# App icon (render with app/make-icon.sh) and the menu bar template icon.
cp "$REPO/app/Assets/AppIcon.icns" "$OUT/Contents/Resources/AppIcon.icns"
cp "$REPO/app/Assets/rabbit-logo-mono.svg" "$OUT/Contents/Resources/MenuBarIcon.svg"
# Bundle the config and schema so the app never reads the repo at runtime (the repo is under
# ~/Documents, which would need a Documents-folder permission). Rebuild after changing them.
CFG="$OUT/Contents/Resources/repo"
mkdir -p "$CFG/logger" "$CFG/data" "$CFG/nudge"
cp "$REPO/logger/config.yaml" "$REPO/logger/financial.yaml" "$CFG/logger/"
[[ -f "$REPO/logger/config.local.yaml" ]] && cp "$REPO/logger/config.local.yaml" "$CFG/logger/"
cp "$REPO/data/schema.sql" "$CFG/data/"
cp "$REPO/nudge/config.yaml" "$CFG/nudge/"  # risk threshold for the popover meter
plutil -lint "$OUT/Contents/Info.plist" >/dev/null
# Sign inside-out: the embedded framework, then the app.
codesign --force --timestamp=none -s "$IDENTITY" "$OUT/Contents/Frameworks/SQLCipher.framework"
codesign --force --timestamp=none -s "$IDENTITY" "$OUT"
codesign -v --deep --strict "$OUT"
echo "built $OUT"

if [[ "${1:-}" == "--install" ]]; then
  mkdir -p "$HOME/Applications"
  pkill -x MetalFocusBar 2>/dev/null && sleep 1 || true
  rm -rf "$HOME/Applications/MetalFocusBar.app"
  ditto "$OUT" "$HOME/Applications/MetalFocusBar.app"
  echo "installed ~/Applications/MetalFocusBar.app"
fi
