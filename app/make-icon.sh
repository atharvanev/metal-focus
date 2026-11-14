#!/usr/bin/env bash
# Render app/Assets/rabbit-logo.svg into app/Assets/AppIcon.icns (all macOS icon sizes).
# Run after editing the SVG and commit the .icns, so build-app.sh needs no extra tools.
set -euo pipefail
ASSETS="$(cd "$(dirname "$0")" && pwd)/Assets"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
SET="$TMP/AppIcon.iconset"; mkdir -p "$SET"

# NSImage renders SVG natively (macOS 13+). Draw into an exact-pixel bitmap per size.
cat > "$TMP/render.swift" <<'SWIFT'
import AppKit
let args = CommandLine.arguments
guard let img = NSImage(contentsOfFile: args[1]) else { fatalError("cannot load \(args[1])") }
let px = Int(args[2])!
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                           colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
rep.size = NSSize(width: px, height: px)
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSGraphicsContext.current?.imageInterpolation = .high
img.draw(in: NSRect(x: 0, y: 0, width: px, height: px))
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: args[3]))
SWIFT
swiftc -O -o "$TMP/render" "$TMP/render.swift"

for s in 16 32 128 256 512; do
  "$TMP/render" "$ASSETS/rabbit-logo.svg" "$s" "$SET/icon_${s}x${s}.png"
  "$TMP/render" "$ASSETS/rabbit-logo.svg" "$((s * 2))" "$SET/icon_${s}x${s}@2x.png"
done
iconutil -c icns -o "$ASSETS/AppIcon.icns" "$SET"
"$TMP/render" "$ASSETS/rabbit-logo.svg" 512 "$ASSETS/rabbit-logo-512.png"
echo "wrote $ASSETS/AppIcon.icns and rabbit-logo-512.png"
