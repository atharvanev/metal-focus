import AppKit
import MetalFocusCore
import SwiftUI

/// The single viewer window. Shown without activating the app: Metal Rabbit never steals focus.
@MainActor enum ViewerWindow {
    private static var window: NSWindow?
    private static var model: ViewerModel?

    static func show(store: Store, riskThreshold: Double) {
        let win = window ?? makeWindow()
        if win.contentViewController == nil {
            // Fresh hosting controller per opening; it is dropped on close so the auto-refresh task ends.
            let m = model ?? ViewerModel(store: store, riskThreshold: riskThreshold)
            model = m
            let host = NSHostingController(rootView: ViewerView(model: m))
            host.sceneBridgingOptions = [.toolbars]
            host.sizingOptions = []
            let frame = win.frame // keep the user's size across reopenings
            win.contentViewController = host
            win.setFrame(frame, display: false)
        }
        win.makeKeyAndOrderFront(nil)
        win.orderFrontRegardless()
    }

    private static func makeWindow() -> NSWindow {
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 920, height: 720),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        win.title = "Metal Rabbit"
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 640, height: 480)
        win.center()
        NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: win, queue: .main) { _ in
            MainActor.assumeIsolated { window?.contentViewController = nil }
        }
        window = win
        return win
    }
}
