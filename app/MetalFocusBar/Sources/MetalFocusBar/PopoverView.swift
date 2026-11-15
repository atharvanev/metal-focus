import MetalFocusCore
import SwiftUI

/// Menu bar popover: sampler health, permissions, today's counts, the model line and actions.
struct PopoverView: View {
    let model: AppModel
    static let stateNames = ["Working", "Short break", "Drifting", "Rabbit hole"]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Metal Rabbit").font(.headline)
            if let err = model.startupError {
                Label(err, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
            } else {
                sampler
                modelLine
            }
            if model.legacyLoggerAgent {
                Label("The old v1 logger LaunchAgent is installed and would write duplicate samples. "
                      + "Remove ~/Library/LaunchAgents/com.metalfocus.logger.plist.", systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange).font(.caption)
            }
            Divider()
            permissions
            Divider()
            actions
            if let err = model.promptError {
                Text(err).font(.caption).foregroundStyle(.red).lineLimit(3)
            }
        }
        .padding(12)
        .frame(width: 340)
        .task { await model.refreshStatus() }
    }

    @ViewBuilder private var sampler: some View {
        if let st = model.status {
            HStack(spacing: 6) {
                Circle().fill(st.loggerRunning ? Color.green : Color.red).frame(width: 8, height: 8)
                    .accessibilityHidden(true)
                Text(st.loggerRunning ? "Sampling" : "Not sampling")
                if let last = st.lastSampleTs {
                    Text("· last sample \(Self.ago(last))").foregroundStyle(.secondary)
                }
            }
            Text("Today: \(st.samplesToday) samples (\(st.excludedToday) excluded)")
                .font(.callout).foregroundStyle(.secondary)
            if let app = model.lastSample.flatMap({ $0.excluded == 1 ? "Excluded" : $0.appName }) {
                Text("Now: \(app)").font(.callout).foregroundStyle(.secondary).lineLimit(1)
            }
        } else {
            Text("Starting…").foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var modelLine: some View {
        if let r = model.readout {
            if r.away == true {
                Label("Model: Away / paused", systemImage: "pause.circle").font(.callout)
            } else {
                let state = r.topState.flatMap { Self.stateNames.indices.contains($0) ? Self.stateNames[$0] : nil } ?? "—"
                Label("Model: \(state) · rabbit-hole risk \(Int((r.risk * 100).rounded()))%", systemImage: "waveform.path.ecg")
                    .font(.callout)
            }
        } else {
            Label("Model not running", systemImage: "waveform.path.ecg").font(.callout).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var permissions: some View {
        permissionRow("Accessibility (window titles)", ok: model.accessibility) { Permissions.promptAccessibility() }
        permissionRow("Notifications (nudges)", ok: model.notificationsAllowed, okText: "On",
                      missingText: "Off · using a panel") { SystemStatus.openNotificationSettings() }
        ForEach(model.browsers, id: \.id) { b in
            switch b.automation {
            case .granted: permissionRow("Automation: \(b.name)", ok: true) {}
            case .denied: permissionRow("Automation: \(b.name)", ok: false, missingText: "Denied") { SystemStatus.openAutomationSettings() }
            case .notAsked: permissionRow("Automation: \(b.name)", ok: false, missingText: "Asks on first use") {}
            case .notRunning: EmptyView()
            }
        }
    }

    private func permissionRow(_ title: String, ok: Bool, okText: String = "Granted", missingText: String = "Not granted",
                               fix: @escaping () -> Void) -> some View {
        HStack {
            Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.circle")
                .foregroundStyle(ok ? .green : .orange).accessibilityHidden(true)
            Text(title).font(.callout)
            Spacer()
            if ok {
                Text(okText).font(.caption).foregroundStyle(.secondary)
            } else {
                Button(missingText == "Asks on first use" ? missingText : "Fix…", action: fix)
                    .controlSize(.small).disabled(missingText == "Asks on first use")
                    .help(missingText)
            }
        }
    }

    @ViewBuilder private var actions: some View {
        HStack {
            Button("Open viewer") { model.openViewer() }
            Button("Test check-in") { model.testPrompt(.checkin) }
            Button("Test nudge") { model.testPrompt(.nudge) }
        }
        .controlSize(.small)
        .disabled(model.store == nil)
        Toggle("Launch at login", isOn: Binding(get: { model.launchAtLogin }, set: { model.toggleLaunchAtLogin($0) }))
            .toggleStyle(.checkbox)
        Button("Quit Metal Rabbit") { NSApp.terminate(nil) }
    }

    static func ago(_ ts: Double) -> String {
        let s = max(0, Int(Date().timeIntervalSince1970 - ts))
        return s < 60 ? "\(s) s ago" : s < 3600 ? "\(s / 60) min ago" : "\(s / 3600) h ago"
    }
}
