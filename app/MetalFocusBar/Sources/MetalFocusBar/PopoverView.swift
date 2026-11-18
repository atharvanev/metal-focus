import MetalFocusCore
import SwiftUI

/// The menu bar popover: what you're on now, the live model's read on it, today at a glance,
/// and (one level down) permissions, prompt tests and launch at login.
struct PopoverView: View {
    let model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Header(model: model)
            if let err = model.startupError {
                ErrorModule(message: err)
            } else {
                NowModule(sample: model.lastSample, accessibility: model.accessibility)
                FocusModule(readout: model.readout, threshold: model.nudge.riskThreshold,
                            notWorking: Binding(get: { model.notWorkingSince != nil }, set: { model.setNotWorking($0) }))
                TodayModule(segments: model.today, status: model.status)
            }
            if !model.accessibility { PermissionBanner() }
            if model.legacyLoggerAgent {
                LegacyAgentBanner(title: "Old logger still installed",
                                  detail: "The v1 LaunchAgent would write duplicate samples. Remove ~/Library/LaunchAgents/com.metalfocus.logger.plist.")
            }
            if let problem = model.jobProblem {
                LegacyAgentBanner(title: "Background jobs paused", detail: problem)
            }
            SettingsModule(model: model)
            Footer()
        }
        .padding(12)
        .frame(width: 340)
        .task { await model.refreshStatus() }
        .task { await model.refreshTodayWhileVisible() }
    }
}

// MARK: - Header

private struct Header: View {
    let model: AppModel

    private var chip: (String, Color) {
        if model.startupError != nil { return ("Error", .red) }
        guard let s = model.lastSample else { return ("Starting", .secondary) }
        if s.locked == 1 { return ("Screen locked", .secondary) }
        // lastSample is set even if the DB write failed; trust the DB's view once we have it.
        if let st = model.status, !st.loggerRunning { return ("Not saving", .red) }
        return ("Sampling", .green)
    }

    var body: some View {
        HStack(spacing: 8) {
            Image(nsImage: MenuBarIcon.image)
                .renderingMode(.template)
                .foregroundStyle(.primary)
            Text("Metal Rabbit")
                .font(.headline)
            Spacer()
            StatusChip(text: chip.0, color: chip.1)
        }
        .padding(.horizontal, 4)
        .padding(.bottom, 2)
    }
}

// MARK: - Now

private struct NowModule: View {
    let sample: Sample?
    let accessibility: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private struct Content: Equatable {
        var bundleId: String?, symbol: String, name: String, detail: String
        var video = false, fullscreen = false, isPrivate = false
    }

    private var content: Content {
        guard let s = sample else {
            return Content(bundleId: nil, symbol: "hourglass", name: "Waiting for the first sample", detail: "A few seconds after launch")
        }
        if s.locked == 1 { return Content(bundleId: nil, symbol: "lock.fill", name: "Screen locked", detail: "Counted as Away") }
        if s.excluded == 1 {
            return Content(bundleId: nil, symbol: "lock.shield.fill", name: "Private app or site",
                           detail: "Excluded: nothing about it is stored", video: s.mediaPlaying == 1)
        }
        let detail = s.domain ?? s.windowTitle
            ?? (s.isPrivate == 1 ? "Private window: title not stored"
                : accessibility ? "No window title" : "Window titles need Accessibility")
        return Content(bundleId: s.bundleId, symbol: "app.dashed", name: s.appName ?? "Unknown app", detail: detail,
                       video: s.mediaPlaying == 1, fullscreen: s.fullscreen == 1, isPrivate: s.isPrivate == 1)
    }

    var body: some View {
        let c = content
        Module(title: "Now") {
            HStack(spacing: 10) {
                AppIcon(bundleId: c.bundleId, symbol: c.symbol)
                VStack(alignment: .leading, spacing: 1) {
                    Text(c.name).font(.body.weight(.semibold)).lineLimit(1)
                    Text(c.detail).font(.callout).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 4)
                HStack(spacing: 6) {
                    if c.video { flag("play.rectangle.fill", "Video playing") }
                    if c.fullscreen { flag("arrow.up.left.and.arrow.down.right", "Full screen") }
                    if c.isPrivate { flag("eye.slash.fill", "Private window") }
                }
            }
            // Materialize in place when the frontmost app changes (same path in and out).
            .id(c.bundleId ?? c.name)
            .transition(reduceMotion ? .opacity : .opacity.combined(with: .scale(scale: 0.98)))
        }
        .animation(Motion.standard(reduceMotion), value: c)
    }

    private func flag(_ symbol: String, _ label: String) -> some View {
        Image(systemName: symbol)
            .font(.caption)
            .foregroundStyle(.secondary)
            .help(label)
            .accessibilityLabel(label)
    }
}

// MARK: - Focus (live HSMM readout)

private struct FocusModule: View {
    let readout: ReadoutInfo?
    let threshold: Double
    @Binding var notWorking: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static let states = ["Working", "Short break", "Drifting", "Rabbit hole"]  // hsmm/config.yaml order
    static let stateColors: [Color] = [.green, .teal, .orange, .red]

    var body: some View {
        Module(title: "Focus") {
            Toggle("Not working", isOn: $notWorking)
                .toggleStyle(.switch).controlSize(.small)
                .help("Leisure time: no nudges or check-ins, and the model doesn't learn from it")
            if notWorking {
                Text("Nudges and check-ins are paused, and this time isn't used for training.")
                    .font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if let r = readout {
                live(r)
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: "moon.zzz").foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Live model is off").font(.body.weight(.medium))
                        Text("Risk shows here once the HSMM runner is writing readouts.")
                            .font(.callout).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    @ViewBuilder private func live(_ r: ReadoutInfo) -> some View {
        let away = r.away == true
        let idx = r.topState.flatMap { Self.states.indices.contains($0) ? $0 : nil }
        let name = away ? "Away" : idx.map { Self.states[$0] } ?? "Unsure"
        let tint = away ? Color.secondary : idx.map { Self.stateColors[$0] } ?? .secondary

        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 8) {
                Circle().fill(tint).frame(width: 9, height: 9)
                Text(name)
                    .font(.title2.weight(.semibold))
                    .tracking(-0.3)  // tighter tracking at display sizes
                    .contentTransition(.opacity)
            }
            Text(subtitle(r, away: away))
                .font(.callout).foregroundStyle(.secondary)
                .contentTransition(.numericText())
        }
        .animation(Motion.standard(reduceMotion), value: name)
        .animation(Motion.standard(reduceMotion), value: r.minutesInState)
        .accessibilityElement(children: .combine)

        if !away {
            VStack(spacing: 6) {
                RiskMeter(risk: r.risk, threshold: threshold)
                HStack {
                    Text("Rabbit-hole risk").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Text("\(Int((r.risk * 100).rounded()))%")
                        .font(.caption.weight(.semibold)).monospacedDigit()
                        .foregroundStyle(r.risk >= threshold ? AnyShapeStyle(.red) : AnyShapeStyle(.primary))
                        .contentTransition(.numericText(value: r.risk))
                        .animation(Motion.standard(reduceMotion), value: r.risk)
                }
                .accessibilityHidden(true)  // RiskMeter speaks the value
            }
        }
    }

    private func subtitle(_ r: ReadoutInfo, away: Bool) -> String {
        if away { return "Idle or screen off" }
        var parts: [String] = []
        if let m = r.minutesInState { parts.append("\(Int(m.rounded())) min in this state") }
        if let p = r.topProb { parts.append("\(Int((p * 100).rounded()))% sure") }
        return parts.isEmpty ? "Updated \(Fmt.clock(r.writtenAt))" : parts.joined(separator: " · ")
    }
}

// MARK: - Today

private struct TodayModule: View {
    let segments: [Segment]
    let status: Status?

    static let nonApps: Set<String> = ["Away", "Excluded", "Unknown"]

    private var tracked: Double { segments.filter { $0.label != "Away" }.reduce(0) { $0 + $1.end - $1.start } }

    private var topApps: [(name: String, seconds: Double)] {
        var by: [String: Double] = [:]
        for s in segments where !Self.nonApps.contains(s.label) { by[s.label, default: 0] += s.end - s.start }
        return by.sorted { $0.value > $1.value }.prefix(3).map { ($0.key, $0.value) }
    }

    var body: some View {
        let top = topApps
        let colors = AppColors.byRank(top.map(\.name))

        Module(title: "Today") {
            if segments.isEmpty {
                Text("Nothing tracked yet today.").font(.callout).foregroundStyle(.secondary)
            } else {
                HStack(alignment: .firstTextBaseline) {
                    Text(Fmt.duration(tracked))
                        .font(.title2.weight(.semibold)).monospacedDigit().tracking(-0.3)
                        .contentTransition(.numericText())
                    Text("tracked").font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    if let st = status {
                        Text("\(st.samplesToday.formatted()) samples")
                            .font(.caption).foregroundStyle(.tertiary).monospacedDigit()
                    }
                }

                VStack(spacing: 4) {
                    DayStrip(segments: segments) { s in
                        if s.label == "Away" { return nil }
                        return colors[s.label] ?? Color.secondary.opacity(0.45)
                    }
                    HStack {
                        Text(Fmt.clock(segments[0].start))
                        Spacer()
                        Text("Now")
                    }
                    .font(.caption2).foregroundStyle(.tertiary)
                }

                VStack(spacing: 6) {
                    ForEach(top, id: \.name) { app in
                        HStack(spacing: 8) {
                            Circle().fill(colors[app.name] ?? .secondary).frame(width: 7, height: 7)
                            Text(app.name).font(.callout).lineLimit(1)
                            Spacer()
                            Text(Fmt.duration(app.seconds))
                                .font(.callout).monospacedDigit().foregroundStyle(.secondary)
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
            }
        }
    }
}

// MARK: - Banners and footer

private struct PermissionBanner: View {
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "hand.raised.fill")
                .foregroundStyle(.orange)
                .font(.title3)
            VStack(alignment: .leading, spacing: 2) {
                Text("Window titles are off").font(.body.weight(.semibold))
                Text("Allow Accessibility so Metal Rabbit can read window titles. It is used only for titles and full-screen state.")
                    .font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            Button("Allow…") { Permissions.promptAccessibility() }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
        }
        .padding(12)
        .background(Color.orange.opacity(0.12), in: .rect(cornerRadius: 12, style: .continuous))
    }
}

private struct LegacyAgentBanner: View {
    let title: String
    let detail: String
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange).font(.title3)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.body.weight(.semibold))
                Text(detail)
                    .font(.callout).foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.12), in: .rect(cornerRadius: 12, style: .continuous))
    }
}

// MARK: - Settings (secondary: collapsed unless something needs attention)

private struct SettingsModule: View {
    let model: AppModel
    @State private var expanded: Bool?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Anything here the user should act on.
    private var needsAttention: Bool {
        !model.accessibility || model.promptError != nil
            || model.browsers.contains { $0.automation == .denied }
    }

    private var isOpen: Bool { expanded ?? needsAttention }

    var body: some View {
        Module {
            Button {
                withAnimation(Motion.standard(reduceMotion)) { expanded = !isOpen }
            } label: {
                HStack(spacing: 6) {
                    Text("Settings").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
                    if needsAttention && !isOpen {
                        Circle().fill(.orange).frame(width: 6, height: 6).accessibilityLabel("Needs attention")
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                        .rotationEffect(.degrees(isOpen ? 90 : 0))
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityValue(isOpen ? "Expanded" : "Collapsed")

            if isOpen {
                VStack(alignment: .leading, spacing: 8) {
                    permissions
                    Divider().padding(.vertical, 2)
                    HStack {
                        Text("Launch at login").font(.callout)
                        Spacer()
                        Toggle("Launch at login", isOn: Binding(get: { model.launchAtLogin },
                                                                set: { model.toggleLaunchAtLogin($0) }))
                            .toggleStyle(.switch).controlSize(.mini).labelsHidden()
                    }
                    Divider().padding(.vertical, 2)
                    VStack(spacing: 0) {
                        action("Open viewer", "rectangle.split.3x1") { model.openViewer() }
                        action("Test check-in", "questionmark.bubble") { model.testPrompt(.checkin) }
                        action("Test nudge", "bell.badge") { model.testPrompt(.nudge) }
                    }
                    .padding(.horizontal, -10)  // row highlights reach the module's inner edge
                    .disabled(model.store == nil)
                    if let err = model.promptError {
                        Text(err).font(.caption).foregroundStyle(.red).lineLimit(3).textSelection(.enabled)
                    }
                }
                .transition(.opacity)
            }
        }
    }

    @ViewBuilder private var permissions: some View {
        row("Accessibility", detail: "Window titles", ok: model.accessibility, fix: Permissions.promptAccessibility)
        row("Notifications", detail: model.notificationsAllowed ? "Nudges as notifications" : "Off: nudges use a panel",
            ok: model.notificationsAllowed, okText: "On", fixText: "Turn on…", fix: SystemStatus.openNotificationSettings)
        ForEach(model.browsers, id: \.id) { b in
            switch b.automation {
            case .granted: row(b.name, detail: "Automation for site names", ok: true) {}
            case .denied: row(b.name, detail: "Automation denied: no site names", ok: false, fix: SystemStatus.openAutomationSettings)
            case .notAsked: row(b.name, detail: "Asks the first time it's used", ok: true, okText: "Pending") {}
            case .notRunning: EmptyView()
            }
        }
    }

    private func row(_ title: String, detail: String, ok: Bool, okText: String = "On", fixText: String = "Fix…",
                     fix: @escaping () -> Void) -> some View {
        HStack(spacing: 8) {
            Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .foregroundStyle(ok ? Color.green : Color.orange)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 0) {
                Text(title).font(.callout)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if ok {
                Text(okText).font(.caption).foregroundStyle(.tertiary)
            } else {
                Button(fixText, action: fix).controlSize(.small)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func action(_ title: String, _ symbol: String, _ run: @escaping () -> Void) -> some View {
        Button(action: run) {
            HStack(spacing: 8) {
                Image(systemName: symbol).foregroundStyle(.secondary).frame(width: 18)  // fixed width: titles align
                Text(title)
            }
            .font(.callout)
        }
        .buttonStyle(RowButtonStyle())
    }
}

private struct ErrorModule: View {
    let message: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red).font(.title3)
            VStack(alignment: .leading, spacing: 2) {
                Text("Sampling stopped").font(.body.weight(.semibold))
                Text(message).font(.callout).foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.10), in: .rect(cornerRadius: 12, style: .continuous))
    }
}

private struct Footer: View {
    var body: some View {
        Button {
            NSApp.terminate(nil)
        } label: {
            HStack {
                Text("Quit Metal Rabbit")
                Spacer()
                Text("⌘Q").foregroundStyle(.tertiary)
            }
            .font(.callout)
        }
        .buttonStyle(RowButtonStyle())
        .keyboardShortcut("q")
    }
}
