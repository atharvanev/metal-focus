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
            Image(systemName: AppDelegate.model.startupError == nil ? "scope" : "exclamationmark.triangle")
        }
        .menuBarExtraStyle(.window)
    }
}
