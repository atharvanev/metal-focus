import MetalFocusCore
import SwiftUI

/// Data behind the viewer window. Reads only; window titles are shown, never logged.
@MainActor @Observable final class ViewerModel {
    @ObservationIgnored private let store: Store
    private(set) var day: Date = Calendar.current.startOfDay(for: Date())
    private(set) var segments: [Segment] = []
    private(set) var samples: [SampleRow] = []
    private(set) var events: [EventRow] = []
    private(set) var checkins: [CheckinRow] = []
    private(set) var error: String?
    private(set) var loaded = false

    init(store: Store) { self.store = store }

    var dayEnd: Date { Calendar.current.date(byAdding: .day, value: 1, to: day)! }
    var isToday: Bool { Calendar.current.isDateInToday(day) }

    func refresh() async {
        do {
            segments = try await store.timeline(day: day)
            samples = try await store.samples(since: nil, limit: 200)
            events = try await store.events(since: nil, limit: 200)
            checkins = try await store.checkins(limit: 200)
            error = nil
        } catch {
            self.error = "Database not available (\(error.localizedDescription))"
        }
        loaded = true
    }

    func shiftDay(by days: Int) async {
        guard let d = Calendar.current.date(byAdding: .day, value: days, to: day) else { return }
        day = min(Calendar.current.startOfDay(for: d), Calendar.current.startOfDay(for: Date()))
        segments = []
        await refresh()
    }
}

/// Wraps rows that have no natural id so they can feed a `Table`.
private struct Indexed<T>: Identifiable {
    let id: Int
    let row: T
    static func wrap(_ rows: [T]) -> [Indexed<T>] { rows.enumerated().map { Indexed(id: $0.offset, row: $0.element) } }
}

private func time(_ ts: Double) -> String { Date(timeIntervalSince1970: ts).formatted(date: .omitted, time: .standard) }
private func dateTime(_ ts: Double) -> String { Date(timeIntervalSince1970: ts).formatted(date: .abbreviated, time: .shortened) }
private func duration(_ s: Double) -> String {
    Duration.seconds(s.rounded()).formatted(.units(allowed: [.hours, .minutes, .seconds], width: .abbreviated, maximumUnitCount: 2))
}

private let palette: [Color] = [.blue, .green, .orange, .purple, .pink, .teal, .indigo, .brown, .mint, .cyan]
private func color(for label: String) -> Color {
    TimelineSummary.neutralLabels.contains(label)
        ? Color.gray.opacity(0.45) : palette[TimelineSummary.paletteIndex(for: label, count: palette.count)]
}

struct ViewerView: View {
    let model: ViewerModel

    var body: some View {
        VStack(spacing: 0) {
            if let error = model.error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.red).padding(8).frame(maxWidth: .infinity, alignment: .leading)
            }
            TabView {
                TodayTab(model: model).tabItem { Text("Today") }
                SamplesTab(rows: model.samples, loaded: model.loaded).tabItem { Text("Samples") }
                EventsTab(rows: model.events, loaded: model.loaded).tabItem { Text("Events") }
                CheckinsTab(rows: model.checkins, loaded: model.loaded).tabItem { Text("Check-ins") }
            }
            .padding(8)
        }
        .frame(minWidth: 600, minHeight: 400)
        .toolbar {
            ToolbarItem {
                Button { Task { await model.refresh() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                    .help("Refresh")
            }
        }
        .task {
            // Lives as long as the hosting view; ViewerWindow drops it on close, which cancels this loop.
            while !Task.isCancelled {
                await model.refresh()
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }
}

private struct EmptyState: View {
    let loaded: Bool
    let title: String
    var body: some View {
        if loaded { ContentUnavailableView(title, systemImage: "tray") } else { ProgressView() }
    }
}

// MARK: - Today

private struct TodayTab: View {
    let model: ViewerModel
    @State private var hovered: Segment?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Button { Task { await model.shiftDay(by: -1) } } label: { Image(systemName: "chevron.left") }
                    .help("Previous day").accessibilityLabel("Previous day")
                Text(model.day.formatted(date: .complete, time: .omitted)).font(.headline)
                Button { Task { await model.shiftDay(by: 1) } } label: { Image(systemName: "chevron.right") }
                    .help("Next day").accessibilityLabel("Next day").disabled(model.isToday)
                Spacer()
                if !model.isToday { Button("Today") { Task { await model.shiftDay(by: 3650) } } }
            }
            if model.segments.isEmpty {
                EmptyState(loaded: model.loaded, title: "No activity on this day")
            } else {
                Text(hovered.map { "\($0.label)  \(time($0.start))–\(time($0.end))  (\(duration($0.end - $0.start)))" }
                     ?? "Hover the timeline for details")
                    .font(.callout).foregroundStyle(hovered == nil ? .secondary : .primary)
                TimelineBar(segments: model.segments, start: model.day.timeIntervalSince1970,
                            end: model.dayEnd.timeIntervalSince1970, hovered: $hovered)
                    .frame(height: 56)
                List(TimelineSummary.totals(model.segments), id: \.label) { t in
                    HStack {
                        RoundedRectangle(cornerRadius: 2).fill(color(for: t.label)).frame(width: 12, height: 12)
                        Text(t.label)
                        Spacer()
                        Text(duration(t.seconds)).monospacedDigit().foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(8)
    }
}

private struct TimelineBar: View {
    let segments: [Segment]
    let start: Double
    let end: Double
    @Binding var hovered: Segment?

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let span = max(end - start, 1)
            let x = { (t: Double) in CGFloat((t - start) / span) * w }
            VStack(alignment: .leading, spacing: 4) {
                // ponytail: Canvas + one hover readout instead of a view per segment; fine for thousands of segments.
                Canvas { ctx, size in
                    ctx.fill(Path(CGRect(origin: .zero, size: size)), with: .color(.secondary.opacity(0.1)))
                    for s in segments {
                        let r = CGRect(x: x(s.start), y: 0, width: max(x(s.end) - x(s.start), 1), height: size.height)
                        ctx.fill(Path(r), with: .color(color(for: s.label)))
                    }
                }
                .frame(height: 36)
                .clipShape(RoundedRectangle(cornerRadius: 4))
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let p):
                        let t = start + Double(p.x / max(w, 1)) * span
                        hovered = segments.first { $0.start <= t && t < $0.end }
                    case .ended:
                        hovered = nil
                    }
                }
                .accessibilityElement()
                .accessibilityLabel("Activity timeline, \(segments.count) segments")
                ZStack(alignment: .topLeading) {
                    ForEach(Array(stride(from: 0, through: 24, by: 3)), id: \.self) { h in
                        Text(String(format: "%02d", h % 24)).font(.caption2).foregroundStyle(.secondary)
                            .fixedSize()
                            .offset(x: min(max(x(start + Double(h) * 3600) - 6, 0), w - 14))
                    }
                }
            }
        }
    }
}

// MARK: - Tables

private struct Badges: View {
    let names: [String]
    var body: some View {
        HStack(spacing: 3) {
            ForEach(names, id: \.self) { n in
                Text(n).font(.caption2).padding(.horizontal, 4).padding(.vertical, 1)
                    .background(.quaternary, in: Capsule())
            }
        }
    }
}

private struct SamplesTab: View {
    let rows: [SampleRow]
    let loaded: Bool

    private static func flags(_ s: SampleRow) -> [String] {
        [("private", s.isPrivate), ("excluded", s.excluded), ("fullscreen", s.fullscreen), ("locked", s.locked),
         ("video", s.mediaPlaying), ("type", s.keyActive), ("click", s.clickActive), ("scroll", s.scrollActive)]
            .compactMap { $0.1 == 1 ? $0.0 : nil }
    }

    var body: some View {
        if rows.isEmpty {
            EmptyState(loaded: loaded, title: "No samples yet")
        } else {
            Table(Indexed.wrap(rows)) {
                TableColumn("Time") { Text(time($0.row.ts)).monospacedDigit() }.width(min: 70, ideal: 80)
                TableColumn("App") { Text($0.row.appName ?? "—") }.width(min: 80, ideal: 120)
                TableColumn("Window title") { r in
                    if let t = r.row.windowTitle { Text(t).help(t) }
                    else if r.row.excluded == 1 || r.row.isPrivate == 1 { Text("hidden").foregroundStyle(.secondary) }
                    else { Text("—").foregroundStyle(.secondary) }
                }.width(min: 150, ideal: 280)
                TableColumn("Domain") { Text($0.row.domain ?? "") }.width(min: 80, ideal: 120)
                TableColumn("Flags") { Badges(names: Self.flags($0.row)) }.width(min: 100, ideal: 200)
                TableColumn("Idle") { r in Text(r.row.idleS.map { String(format: "%.0f s", $0) } ?? "—").monospacedDigit() }
                    .width(min: 40, ideal: 50)
            }
        }
    }
}

private struct EventsTab: View {
    let rows: [EventRow]
    let loaded: Bool
    var body: some View {
        if rows.isEmpty {
            EmptyState(loaded: loaded, title: "No events yet")
        } else {
            Table(Indexed.wrap(rows)) {
                TableColumn("Time") { Text(dateTime($0.row.ts)).monospacedDigit() }.width(min: 120, ideal: 150)
                TableColumn("Kind") { Text($0.row.kind) }.width(min: 80, ideal: 140)
                TableColumn("Detail") { Text($0.row.detail ?? "").foregroundStyle(.secondary) }
            }
        }
    }
}

private struct CheckinsTab: View {
    let rows: [CheckinRow]
    let loaded: Bool
    var body: some View {
        if rows.isEmpty {
            EmptyState(loaded: loaded, title: "No check-ins yet")
        } else {
            Table(Indexed.wrap(rows)) {
                TableColumn("Asked") { Text(dateTime($0.row.askedAt)).monospacedDigit() }.width(min: 120, ideal: 150)
                TableColumn("Trigger") { Text($0.row.trigger) }
                TableColumn("Split") { Text($0.row.split) }.width(min: 40, ideal: 50)
                TableColumn("Model guess") { r in
                    Text(r.row.modelTopState.map { s in s + (r.row.modelTopProb.map { String(format: " %.0f%%", $0 * 100) } ?? "") } ?? "—")
                }
                TableColumn("Response") { Text($0.row.response ?? "—") }
                TableColumn("Responded") { r in Text(r.row.respondedAt.map(dateTime) ?? "—").monospacedDigit() }
                    .width(min: 120, ideal: 150)
            }
        }
    }
}
