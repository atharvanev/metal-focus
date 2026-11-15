import AppKit
import MetalFocusCore
import SwiftUI

/// A floating panel that never becomes key or main and never activates the app, so showing it
/// can't steal focus. Clicks still reach its buttons (non-activating panels accept them).
class PromptPanel: NSPanel {
    init<Content: View>(_ content: Content) {
        super.init(contentRect: .zero, styleMask: [.nonactivatingPanel, .titled, .fullSizeContentView],
                   backing: .buffered, defer: true)
        level = .floating
        isFloatingPanel = true
        hidesOnDeactivate = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titleVisibility = .hidden
        titlebarAppearsTransparent = true
        isMovableByWindowBackground = true
        isReleasedWhenClosed = false
        for b in [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton] { standardWindowButton(b)?.isHidden = true }
        let host = FirstMouseHostingView(rootView: content)
        contentView = host
        setContentSize(host.fittingSize)
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    /// Top-right of the screen with the mouse, just under the menu bar (12 pt inset), stacked
    /// 8 pt below the lowest of `avoiding` (other open prompt panels) on that screen.
    func showTopRight(avoiding: [NSWindow] = []) {
        let mouse = NSEvent.mouseLocation
        guard let screen = NSScreen.screens.first(where: { NSMouseInRect(mouse, $0.frame, false) }) ?? NSScreen.main
        else { return }
        let vf = screen.visibleFrame, inset: CGFloat = 12
        let top = avoiding.filter { $0.isVisible && $0.screen == screen }.map { $0.frame.minY - 8 }.min() ?? vf.maxY - inset
        setFrameOrigin(NSPoint(x: vf.maxX - frame.width - inset, y: top - frame.height))
        orderFrontRegardless()
    }
}

/// Lets the first click on an inactive panel hit the button instead of being swallowed.
final class FirstMouseHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

final class CheckinPanel: PromptPanel {
    init(item: OutboxItem, deadline: Date, onAnswer: @escaping @MainActor (CheckinAnswer) -> Void) {
        super.init(CheckinView(item: item, deadline: deadline, onAnswer: onAnswer))
    }
}

struct CheckinView: View {
    let item: OutboxItem, deadline: Date, onAnswer: @MainActor (CheckinAnswer) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(item.question ?? "What were you doing the last 10 min?").font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if item.test { TestBadge() }
            }
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 6) {
                ForEach(CheckinAnswer.buttons, id: \.self) { a in
                    Button { onAnswer(a) } label: { Text(a.title).frame(maxWidth: .infinity) }
                }
            }
            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                Text("closes in \(max(0, Int(deadline.timeIntervalSince(ctx.date).rounded(.up)))) s")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
        }
        .padding(14)
        .frame(width: 340)
    }
}

struct TestBadge: View {
    var body: some View {
        Text("Test").font(.caption2.bold()).padding(.horizontal, 6).padding(.vertical, 2)
            .background(.orange.opacity(0.25), in: Capsule())
            .accessibilityLabel("Test prompt")
    }
}
