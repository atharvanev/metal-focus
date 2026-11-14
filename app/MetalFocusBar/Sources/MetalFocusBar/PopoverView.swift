import MetalFocusCore
import SwiftUI

/// Minimal sampler status (Task 4). The full popover arrives in Task 7.
struct PopoverView: View {
    let model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Metal Focus").font(.headline)
            if let err = model.startupError {
                Label(err, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
            } else if let s = model.lastSample {
                Text("Sampling · last: \(s.appName ?? (s.excluded == 1 ? "Excluded" : "—"))")
            } else {
                Text("Starting…")
            }
            if !model.accessibility {
                HStack {
                    Text("Accessibility not granted (no window titles)")
                    Button("Grant") { Permissions.promptAccessibility() }
                }
            }
            Divider()
            Button("Quit Metal Focus") { NSApp.terminate(nil) }
        }
        .padding(12)
        .frame(width: 320)
    }
}
