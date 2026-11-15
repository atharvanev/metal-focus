import MetalFocusCore
import SwiftUI

/// Data behind the viewer window. Reads only; window titles are shown, never logged.
@MainActor @Observable final class ViewerModel {
    @ObservationIgnored private let store: Store
    let riskThreshold: Double
    private(set) var day: Date = Calendar.current.startOfDay(for: Date())
    private(set) var segments: [Segment] = []
    private(set) var activity = DayActivity.empty
    private(set) var risk: [RiskPoint] = []
    private(set) var error: String?
    private(set) var loaded = false

    init(store: Store, riskThreshold: Double = NudgeSettings.fallback.riskThreshold) {
        self.store = store
        self.riskThreshold = riskThreshold
    }

    var dayEnd: Date { Calendar.current.date(byAdding: .day, value: 1, to: day)! }
    var isToday: Bool { Calendar.current.isDateInToday(day) }

    func refresh() async {
        do {
            let (s, a, r) = (try await store.timeline(day: day), try await store.dayActivity(day: day),
                             try await store.riskSeries(day: day))
            // Assign only on change: the 10 s refresh must not re-render an unchanged day.
            if s != segments { segments = s }
            if a != activity { activity = a }
            if r != risk { risk = r }
            error = nil
        } catch {
            self.error = "Database not available (\(error.localizedDescription))"
        }
        loaded = true
    }

    func shiftDay(by days: Int) async {
        guard let d = Calendar.current.date(byAdding: .day, value: days, to: day) else { return }
        await show(d)
    }

    func goToToday() async { await show(Date()) }

    private func show(_ d: Date) async {
        day = min(Calendar.current.startOfDay(for: d), Calendar.current.startOfDay(for: Date()))
        segments = []; activity = .empty; risk = []; loaded = false
        await refresh()
    }
}

struct ViewerView: View {
    let model: ViewerModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                DayTitle(day: model.day, isToday: model.isToday)
                if let error = model.error {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red).font(.callout)
                }
                content
                    .id(model.day)  // crossfade on day change only; the periodic refresh doesn't animate
                    .transition(.opacity)
            }
            .padding(24)
            .frame(maxWidth: 1000)
            .frame(maxWidth: .infinity)
        }
        .animation(Motion.standard(reduceMotion), value: model.day)
        .frame(minWidth: 640, minHeight: 480)
        .toolbar { toolbar }
        .task {
            // Lives as long as the hosting view; ViewerWindow drops it on close, which cancels this loop.
            while !Task.isCancelled {
                await model.refresh()
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    @ViewBuilder private var content: some View {
        if !model.loaded {
            ProgressView().frame(maxWidth: .infinity, minHeight: 240)
        } else if model.activity.sampleCount == 0 {
            ContentUnavailableView("No activity", systemImage: "moon.zzz",
                                   description: Text(model.isToday ? "Samples appear here as Metal Rabbit records them."
                                                                   : "Nothing was recorded on this day."))
                .frame(minHeight: 320)
        } else {
            VStack(alignment: .leading, spacing: 16) {
                SummaryTiles(model: model)
                TimelineModule(model: model)
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 16) { appsModule; sitesModule }
                    VStack(spacing: 16) { appsModule; sitesModule }
                }
                if !model.activity.titles.isEmpty {
                    Module(title: "Window titles") { RankedList(rows: model.activity.titles) }
                }
                PromptsModule(checkins: model.activity.checkins, nudges: model.activity.nudges)
                RawDataModule(samples: model.activity.samples, events: model.activity.events)
            }
        }
    }

    private var appColors: [String: Color] {
        AppColors.byRank(model.activity.apps.map(\.name).filter { !TimelineSummary.neutralLabels.contains($0) && $0 != "Unknown" })
    }

    private var appsModule: some View {
        Module(title: "Apps") { RankedList(rows: model.activity.apps, colors: appColors) }
            .frame(minWidth: 280)
    }

    private var sitesModule: some View {
        Module(title: "Sites") {
            if model.activity.sites.isEmpty {
                Text("No sites recorded.").font(.callout).foregroundStyle(.secondary)
            } else {
                RankedList(rows: model.activity.sites)
            }
        }
        .frame(minWidth: 280)
    }

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .navigation) {
            Button { Task { await model.shiftDay(by: -1) } } label: { Label("Previous day", systemImage: "chevron.left") }
                .help("Previous day")
                .keyboardShortcut("[", modifiers: .command)
            Button { Task { await model.shiftDay(by: 1) } } label: { Label("Next day", systemImage: "chevron.right") }
                .help("Next day")
                .keyboardShortcut("]", modifiers: .command)
                .disabled(model.isToday)
        }
        ToolbarItemGroup {
            Button("Today") { Task { await model.goToToday() } }
                .disabled(model.isToday)
            Button { Task { await model.refresh() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                .help("Refresh")
                .keyboardShortcut("r", modifiers: .command)
        }
    }
}

// MARK: - Header and tiles

private struct DayTitle: View {
    let day: Date
    let isToday: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(isToday ? "Today" : day.formatted(.dateTime.weekday(.wide)))
                .font(.largeTitle.weight(.bold))
                .tracking(-0.5)  // tighter at display sizes
            Text(day.formatted(date: .long, time: .omitted))
                .font(.title3).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

private struct SummaryTiles: View {
    let model: ViewerModel

    var body: some View {
        let a = model.activity
        let answered = a.checkins.filter { !["skip", "dismissed", nil].contains($0.response) }.count
        let tracked = model.segments.filter { $0.label != "Away" }.reduce(0) { $0 + $1.end - $1.start }
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: 4), spacing: 12) {
            tile("Tracked", Fmt.duration(tracked), "excluding Away")
            tile("Samples", a.sampleCount.formatted(), "\(Fmt.duration(a.loggedSeconds)) logged")
            tile("Check-ins", "\(answered) of \(a.checkins.count)", "answered")
            tile("Nudges", "\(a.nudges.filter { !$0.holdout }.count)",
                 a.nudges.contains(where: \.holdout) ? "\(a.nudges.filter(\.holdout).count) held out" : "shown")
        }
    }

    private func tile(_ title: String, _ value: String, _ sub: String) -> some View {
        Module(title: title) {
            VStack(alignment: .leading, spacing: 2) {
                Text(value).font(.title.weight(.semibold)).monospacedDigit().tracking(-0.4)
                    .contentTransition(.numericText())
                Text(sub).font(.caption).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Timeline (activity bar + risk line on one axis, one shared hover)

private struct TimelineModule: View {
    let model: ViewerModel
    @State private var hoverT: Double?

    private var start: Double { model.day.timeIntervalSince1970 }
    private var end: Double { model.dayEnd.timeIntervalSince1970 }

    private var colors: [String: Color] {
        AppColors.byRank(TimelineSummary.totals(model.segments).map(\.label)
            .filter { !TimelineSummary.neutralLabels.contains($0) && $0 != "Unknown" })
    }

    private var readout: String {
        guard let t = hoverT else { return "Hover the timeline for details" }
        var parts: [String] = []
        if let s = model.segments.first(where: { $0.start <= t && t < $0.end }) {
            parts.append("\(s.label) · \(Fmt.clock(s.start))–\(Fmt.clock(s.end)) (\(Fmt.duration(s.end - s.start)))")
        } else {
            parts.append(Fmt.clock(t))
        }
        if let p = model.risk.min(by: { abs($0.windowStart + 60 - t) < abs($1.windowStart + 60 - t) }),
           abs(p.windowStart + 60 - t) <= 120, let r = p.risk {
            parts.append("risk \(Int((r * 100).rounded()))%")
        }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        let colors = colors
        Module(title: "Timeline") {
            Text(readout)
                .font(.callout).monospacedDigit()
                .foregroundStyle(hoverT == nil ? .secondary : .primary)
                .lineLimit(1)
            VStack(spacing: 6) {
                TimelineBar(segments: model.segments, start: start, end: end, hoverT: $hoverT) { label in
                    TimelineSummary.neutralLabels.contains(label) ? (label == "Away" ? nil : AppColors.other)
                        : colors[label] ?? AppColors.other
                }
                .frame(height: 28)
                RiskLine(points: model.risk, start: start, end: end, threshold: model.riskThreshold, hoverT: $hoverT)
                    .frame(height: 72)
                HourAxis(start: start, end: end)
            }
        }
    }
}

/// Maps a hover location to a time and draws the shared crosshair.
private struct HoverTrack: ViewModifier {
    let start: Double, end: Double
    @Binding var hoverT: Double?

    func body(content: Content) -> some View {
        GeometryReader { geo in
            let w = max(geo.size.width, 1)
            content
                .overlay(alignment: .leading) {
                    if let t = hoverT {
                        Rectangle().fill(Color.primary.opacity(0.5)).frame(width: 1)
                            .offset(x: CGFloat((t - start) / (end - start)) * w)
                            .allowsHitTesting(false)
                    }
                }
                .contentShape(.rect)
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let p): hoverT = start + Double(p.x / w) * (end - start)
                    case .ended: hoverT = nil
                    }
                }
        }
    }
}

private struct TimelineBar: View {
    let segments: [Segment]
    let start: Double, end: Double
    @Binding var hoverT: Double?
    let color: (String) -> Color?

    var body: some View {
        // ponytail: Canvas + one hover readout instead of a view per segment; fine for thousands of segments.
        Canvas { ctx, size in
            let shape = Path(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: 7, style: .continuous)
            ctx.fill(shape, with: .color(.primary.opacity(0.07)))
            ctx.clip(to: shape)
            let span = max(end - start, 1)
            for s in segments {
                guard let c = color(s.label) else { continue }
                let x0 = (s.start - start) / span * size.width, x1 = (s.end - start) / span * size.width
                ctx.fill(Path(CGRect(x: x0, y: 0, width: max(x1 - x0, 1), height: size.height)), with: .color(c))
            }
        }
        .modifier(HoverTrack(start: start, end: end, hoverT: $hoverT))
        .accessibilityElement()
        .accessibilityLabel("Activity timeline")
        .accessibilityValue("\(segments.count) stretches; the Apps list has the totals")
    }
}

/// Day-long HSMM risk on the activity bar's axis. A nil risk (Away) lifts the pen.
private struct RiskLine: View {
    let points: [RiskPoint]
    let start: Double, end: Double
    let threshold: Double
    @Binding var hoverT: Double?

    private var hasRisk: Bool { points.contains { $0.risk != nil } }

    var body: some View {
        ZStack(alignment: .topLeading) {
            Canvas { ctx, size in
                let span = max(end - start, 1)
                let x = { (t: Double) in CGFloat((t - start) / span) * size.width }
                let y = { (r: Double) in size.height - CGFloat(min(max(r, 0), 1)) * (size.height - 2) - 1 }
                var thr = Path()
                thr.move(to: CGPoint(x: 0, y: y(threshold))); thr.addLine(to: CGPoint(x: size.width, y: y(threshold)))
                ctx.stroke(thr, with: .color(.red.opacity(0.5)), style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
                var base = Path()
                base.move(to: CGPoint(x: 0, y: size.height - 0.5)); base.addLine(to: CGPoint(x: size.width, y: size.height - 0.5))
                ctx.stroke(base, with: .color(.primary.opacity(0.12)), lineWidth: 1)
                // One run per unbroken stretch: a soft fill under the line, then the line itself.
                var runs: [[CGPoint]] = [], cur: [CGPoint] = []
                for p in points {
                    guard let r = p.risk else { if !cur.isEmpty { runs.append(cur); cur = [] }; continue }
                    cur.append(CGPoint(x: x(p.windowStart + 60), y: y(r)))
                }
                if !cur.isEmpty { runs.append(cur) }
                for run in runs {
                    var line = Path(); line.addLines(run)
                    var area = line
                    area.addLine(to: CGPoint(x: run.last!.x, y: size.height)); area.addLine(to: CGPoint(x: run[0].x, y: size.height))
                    area.closeSubpath()
                    ctx.fill(area, with: .linearGradient(Gradient(colors: [.red.opacity(0.22), .red.opacity(0)]),
                                                        startPoint: .zero, endPoint: CGPoint(x: 0, y: size.height)))
                    ctx.stroke(line, with: .color(.red), style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
                }
            }
            .modifier(HoverTrack(start: start, end: end, hoverT: $hoverT))
            Text(hasRisk ? "Risk · nudge at \(Int((threshold * 100).rounded()))%" : "Risk · none recorded this day")
                .font(.caption2).foregroundStyle(.tertiary)
                .allowsHitTesting(false)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(hasRisk ? "Rabbit-hole risk over the day, nudge at \(Int((threshold * 100).rounded())) percent"
                                    : "No risk recorded this day")
    }
}

private struct HourAxis: View {
    let start: Double, end: Double

    var body: some View {
        GeometryReader { geo in
            ForEach(Array(stride(from: 0, through: 24, by: 3)), id: \.self) { h in
                let x = geo.size.width * CGFloat(h) / 24
                Text(Date(timeIntervalSince1970: start + Double(h) * 3600).formatted(.dateTime.hour()))
                    .font(.caption2).foregroundStyle(.tertiary).fixedSize()
                    .position(x: min(max(x, 14), geo.size.width - 14), y: 6)
            }
        }
        .frame(height: 12)
        .accessibilityHidden(true)
    }
}

// MARK: - Lists

private struct RankedList: View {
    let rows: [RankedTime]
    var colors: [String: Color]? = nil

    var body: some View {
        let top = rows.first?.seconds ?? 1
        VStack(spacing: 8) {
            ForEach(rows, id: \.self.key) { r in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        if let colors {
                            Circle().fill(colors[r.name] ?? AppColors.other).frame(width: 7, height: 7)
                        }
                        Text(r.name).font(.callout).lineLimit(1).truncationMode(.middle).help(r.name)
                        if let d = r.detail { Text(d).font(.caption).foregroundStyle(.tertiary).lineLimit(1) }
                        Spacer(minLength: 8)
                        Text(Fmt.duration(r.seconds)).font(.callout).monospacedDigit().foregroundStyle(.secondary)
                    }
                    Capsule().fill(Color.primary.opacity(0.07))
                        .frame(height: 4)
                        .overlay(alignment: .leading) {
                            GeometryReader { g in
                                Capsule().fill((colors.map { $0[r.name] ?? AppColors.other } ?? Color.accentColor).opacity(0.85))
                                    .frame(width: max(4, g.size.width * r.seconds / max(top, 1)))
                            }
                        }
                }
                .accessibilityElement(children: .combine)
            }
        }
    }
}

private extension RankedTime {
    var key: String { "\(detail ?? "")\u{1F}\(name)" }
}

private func prettyResponse(_ r: String?) -> String {
    guard let r else { return "No answer" }
    let s = r.replacingOccurrences(of: "_", with: " ")
    return s.prefix(1).uppercased() + s.dropFirst()
}

private struct PromptsModule: View {
    let checkins: [CheckinRow]
    let nudges: [NudgeRow]

    private struct Item: Identifiable {
        let id: String, ts: Double, symbol: String, title: String, detail: String, response: String?
    }

    private var items: [Item] {
        (checkins.map { c in
            Item(id: "c\(c.id)", ts: c.askedAt, symbol: "questionmark.bubble",
                 title: c.split == "eval" ? "Check-in (eval)" : "Check-in",
                 detail: c.trigger.replacingOccurrences(of: "_", with: " ")
                    + (c.modelTopState.map { s in " · model: \(s)" + (c.modelTopProb.map { " \(Int(($0 * 100).rounded()))%" } ?? "") } ?? ""),
                 response: c.response)
        } + nudges.map { n in
            Item(id: "n\(n.windowStart)", ts: n.windowStart + 120, symbol: n.holdout ? "bell.slash" : "bell.badge",
                 title: n.holdout ? "Nudge (held out)" : "Nudge",
                 detail: "risk \(Int((n.risk * 100).rounded()))%" + (n.body.map { " · \($0)" } ?? ""),
                 response: n.holdout ? "Not shown" : n.response)
        }).sorted { $0.ts < $1.ts }
    }

    var body: some View {
        Module(title: "Check-ins and nudges") {
            let items = items
            if items.isEmpty {
                Text("No check-ins or nudges on this day.").font(.callout).foregroundStyle(.secondary)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(items.enumerated()), id: \.element.id) { i, it in
                        if i > 0 { Divider().padding(.leading, 30) }
                        HStack(spacing: 10) {
                            Image(systemName: it.symbol).foregroundStyle(.secondary).frame(width: 20)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(it.title).font(.callout.weight(.medium))
                                Text(it.detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            }
                            Spacer(minLength: 8)
                            Text(prettyResponse(it.response))
                                .font(.caption.weight(.medium))
                                .foregroundStyle(it.response == nil ? .tertiary : .secondary)
                                .padding(.horizontal, 8).padding(.vertical, 2)
                                .background(Color.primary.opacity(0.06), in: .capsule)
                            Text(Fmt.clock(it.ts)).font(.caption).monospacedDigit().foregroundStyle(.tertiary)
                                .frame(minWidth: 56, alignment: .trailing)
                        }
                        .padding(.vertical, 7)
                        .accessibilityElement(children: .combine)
                    }
                }
            }
        }
    }
}

/// Debug data, one level down: the newest samples and events of the day.
private struct RawDataModule: View {
    let samples: [SampleRow]
    let events: [EventRow]
    @State private var open = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Module {
            Button {
                withAnimation(Motion.standard(reduceMotion)) { open.toggle() }
            } label: {
                HStack {
                    Text("Recent samples and events").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
                    Spacer()
                    Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                        .rotationEffect(.degrees(open ? 90 : 0))
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityValue(open ? "Expanded" : "Collapsed")

            if open {
                VStack(alignment: .leading, spacing: 14) {
                    Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 6) {
                        GridRow { header("Time"); header("App"); header("Window title or site"); header("Flags") }
                        ForEach(samples, id: \.ts) { s in
                            GridRow {
                                Text(Fmt.clock(s.ts)).monospacedDigit().foregroundStyle(.secondary)
                                Text(s.appName ?? (s.excluded == 1 ? "Excluded" : "—")).lineLimit(1)
                                Text(s.domain ?? s.windowTitle ?? (s.excluded == 1 || s.isPrivate == 1 ? "hidden" : "—"))
                                    .foregroundStyle(s.windowTitle == nil && s.domain == nil ? .tertiary : .primary)
                                    .lineLimit(1).truncationMode(.middle)
                                    .gridColumnAlignment(.leading)
                                flags(s)
                            }
                            .font(.caption)
                        }
                    }
                    if !events.isEmpty {
                        Divider()
                        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 6) {
                            GridRow { header("Time"); header("Event"); header("Detail") }
                            ForEach(Array(events.enumerated()), id: \.offset) { _, e in
                                GridRow {
                                    Text(Fmt.clock(e.ts)).monospacedDigit().foregroundStyle(.secondary)
                                    Text(e.kind.replacingOccurrences(of: "_", with: " "))
                                    Text(e.detail ?? "").foregroundStyle(.secondary)
                                }
                                .font(.caption)
                            }
                        }
                    }
                }
                .textSelection(.enabled)
                .transition(.opacity)
            }
        }
    }

    private func header(_ s: String) -> some View {
        Text(s).font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
    }

    private func flags(_ s: SampleRow) -> some View {
        let all: [(Int?, String, String)] = [
            (s.isPrivate, "eye.slash", "Private"), (s.excluded, "lock.shield", "Excluded"),
            (s.fullscreen, "arrow.up.left.and.arrow.down.right", "Full screen"), (s.locked, "lock", "Locked"),
            (s.mediaPlaying, "play.rectangle", "Video"), (s.keyActive, "keyboard", "Typing"),
            (s.clickActive, "cursorarrow.click", "Clicking"), (s.scrollActive, "scroll", "Scrolling"),
        ]
        return HStack(spacing: 4) {
            ForEach(all.filter { $0.0 == 1 }, id: \.1) { f in
                Image(systemName: f.1).foregroundStyle(.secondary).help(f.2).accessibilityLabel(f.2)
            }
        }
    }
}
