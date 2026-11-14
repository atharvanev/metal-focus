import AppKit
import MetalFocusCore
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    @MainActor static let model = AppModel()
    private var sigterm: DispatchSourceSignal?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // SIGTERM (pkill, logout, build-app.sh --install) skips applicationWillTerminate; route it
        // through a normal terminate so logger_stop is still written.
        signal(SIGTERM, SIG_IGN)
        let src = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        src.setEventHandler { NSApp.terminate(nil) }
        src.resume()
        sigterm = src
        MainActor.assumeIsolated { Self.model.start() }
    }

    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { Self.model.shutdown() }
    }
}

@main
struct MetalFocusBarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        MenuBarExtra {
            PopoverView(model: AppDelegate.model)
        } label: {
            if AppDelegate.model.startupError == nil {
                Image(nsImage: MenuBarIcon.image)
            } else {
                Image(systemName: "exclamationmark.triangle")
            }
        }
        .menuBarExtraStyle(.window)
    }
}

/// The mono rabbit logo as a template image, so the menu bar tints it for light/dark mode.
/// Falls back to an SF Symbol when run outside the bundle (e.g. `swift run`).
enum MenuBarIcon {
    @MainActor static let image: NSImage = {
        let img = Bundle.main.url(forResource: "MenuBarIcon", withExtension: "svg").flatMap(NSImage.init(contentsOf:))
            ?? NSImage(systemSymbolName: "hare", accessibilityDescription: "Metal Rabbit")!
        img.size = NSSize(width: 18, height: 18)
        img.isTemplate = true
        img.accessibilityDescription = "Metal Rabbit"
        return img
    }()
}
