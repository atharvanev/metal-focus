import AppKit
import MetalFocusCore
import SwiftUI

// Building blocks for the popover. Design rules (Apple HIG / WWDC "Designing Fluid Interfaces"):
// - The popover's own material is the only translucent layer; modules sit on a faint solid fill
//   (never translucent-on-translucent, which kills legibility).
// - Motion is a critically damped spring (no bounce): nothing here is flicked, so nothing overshoots.
// - Press feedback happens on press-down, instantly. Reduce Motion swaps movement for crossfades.

enum Motion {
    /// Default UI spring: critically damped, ~0.35 s response.
    static let spring = Animation.spring(duration: 0.35, bounce: 0)
    static func standard(_ reduceMotion: Bool) -> Animation { reduceMotion ? .easeOut(duration: 0.2) : spring }
}

/// A Control Center-style grouped section.
struct Module<Content: View>: View {
    var title: String?
    @ViewBuilder var content: Content
    @Environment(\.colorSchemeContrast) private var contrast

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let title {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            content
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.05), in: .rect(cornerRadius: 12, style: .continuous))
        .overlay {
            if contrast == .increased {
                RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.primary.opacity(0.35))
            }
        }
    }
}

/// Full-width row button: hover highlight, and an instant scale on press-down.
struct RowButtonStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var hovering = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect(cornerRadius: 8, style: .continuous))
            .background(Color.primary.opacity(configuration.isPressed ? 0.12 : hovering ? 0.07 : 0),
                        in: .rect(cornerRadius: 8, style: .continuous))
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.98 : 1)
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
            .onHover { hovering = $0 }
    }
}

/// Status dot + text in a capsule. Static on purpose: no pulsing (slow loops distract).
struct StatusChip: View {
    var text: String
    var color: Color

    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(text).font(.caption.weight(.medium)).foregroundStyle(.secondary)
                .contentTransition(.opacity)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(Color.primary.opacity(0.06), in: .capsule)
        .accessibilityElement(children: .combine)
    }
}

/// P(Drifting) + P(Rabbit hole) as a capsule meter with a tick at the nudge threshold.
struct RiskMeter: View {
    var risk: Double
    var threshold: Double
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static func color(risk: Double, threshold: Double) -> Color {
        risk >= threshold ? .red : risk >= threshold / 2 ? .orange : .green
    }

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.08))
                Capsule().fill(Self.color(risk: risk, threshold: threshold).gradient)
                    .frame(width: max(6, w * min(max(risk, 0), 1)))
                RoundedRectangle(cornerRadius: 1)
                    .fill(Color.primary.opacity(0.45))
                    .frame(width: 2, height: 12)
                    .offset(x: w * threshold - 1)
            }
            .frame(height: 6)
            .frame(maxHeight: .infinity)
        }
        .frame(height: 12)
        .animation(Motion.standard(reduceMotion), value: risk)
        .accessibilityElement()
        .accessibilityLabel("Rabbit-hole risk")
        .accessibilityValue("\(Int((risk * 100).rounded())) percent, nudge at \(Int((threshold * 100).rounded())) percent")
    }
}

/// Today's segments as one capsule strip. Away gaps show the empty track.
struct DayStrip: View {
    var segments: [Segment]
    var color: (Segment) -> Color?

    var body: some View {
        Canvas { ctx, size in
            ctx.fill(Path(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: size.height / 2),
                     with: .color(.primary.opacity(0.08)))
            guard let lo = segments.first?.start else { return }
            let hi = max(Date().timeIntervalSince1970, segments.last?.end ?? lo + 1)
            let span = max(hi - lo, 1)
            ctx.clip(to: Path(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: size.height / 2))
            for s in segments {
                guard let c = color(s) else { continue }
                let x0 = (s.start - lo) / span * size.width
                let x1 = (s.end - lo) / span * size.width
                ctx.fill(Path(CGRect(x: x0, y: 0, width: max(x1 - x0, 1), height: size.height)), with: .color(c))
            }
        }
        .frame(height: 10)
        .accessibilityHidden(true)  // the top-apps list below carries the same information
    }
}

/// The real app icon for a bundle id (cached), or an SF Symbol stand-in.
struct AppIcon: View {
    var bundleId: String?
    var symbol: String = "app.dashed"
    var size: CGFloat = 32

    @MainActor private static var cache: [String: NSImage] = [:]

    @MainActor static func icon(for bundleId: String) -> NSImage? {
        if let hit = cache[bundleId] { return hit }
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleId) else { return nil }
        let img = NSWorkspace.shared.icon(forFile: url.path)
        cache[bundleId] = img
        return img
    }

    var body: some View {
        if let bundleId, let img = Self.icon(for: bundleId) {
            Image(nsImage: img).resizable().interpolation(.high).frame(width: size, height: size)
        } else {
            Image(systemName: symbol)
                .font(.system(size: size * 0.45, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: size, height: size)
                .background(Color.primary.opacity(0.06), in: .rect(cornerRadius: size * 0.22, style: .continuous))
        }
    }
}

enum Fmt {
    static func duration(_ seconds: Double) -> String {
        let m = Int((seconds / 60).rounded())
        if m < 1 { return "<1m" }
        return m >= 60 ? "\(m / 60)h \(String(format: "%02d", m % 60))m" : "\(m)m"
    }

    static func clock(_ ts: Double) -> String {
        Date(timeIntervalSince1970: ts).formatted(date: .omitted, time: .shortened)
    }
}
