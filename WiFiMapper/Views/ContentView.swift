import SwiftUI

public enum WorkspaceDisplayMode: String, CaseIterable, Identifiable {
    case splitCameraAndMap = "Split AR + Map"
    case fullMap = "2D FloorPlan"
    case fullCamera = "AR Camera"

    public var id: String { rawValue }
}

public struct ContentView: View {
    @ObservedObject var coordinator: HouseMappingCoordinator
    @State private var displayMode: WorkspaceDisplayMode = .splitCameraAndMap
    @State private var interactionMode: MapInteractionMode = .panZoom
    @State private var showAdvisorSheet: Bool = false
    @State private var showRoomLabelPrompt: Bool = false
    @State private var pendingRoomLabelPoint: Point2D? = nil
    @State private var roomLabelText: String = ""

    public init(coordinator: HouseMappingCoordinator) {
        self.coordinator = coordinator
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // 1. Live Wi-Fi & vSLAM Telemetry Header Strip
                telemetryHeaderBar

                // 2. Main Viewport (Split AR Camera + 2D Robot Vacuum Map, or Full Screen)
                GeometryReader { geo in
                    switch displayMode {
                    case .splitCameraAndMap:
                        VStack(spacing: 0) {
                            ARCameraMappingView(coordinator: coordinator)
                                .frame(height: geo.size.height * 0.44)
                            Divider()
                                .background(Color.white.opacity(0.2))
                            FloorPlanHeatmapView(
                                coordinator: coordinator,
                                interactionMode: $interactionMode,
                                onRequestRoomLabelAt: { pt in
                                    pendingRoomLabelPoint = pt
                                    roomLabelText = ""
                                    showRoomLabelPrompt = true
                                }
                            )
                            .frame(height: geo.size.height * 0.56)
                        }
                    case .fullMap:
                        FloorPlanHeatmapView(
                            coordinator: coordinator,
                            interactionMode: $interactionMode,
                            onRequestRoomLabelAt: { pt in
                                pendingRoomLabelPoint = pt
                                roomLabelText = ""
                                showRoomLabelPrompt = true
                            }
                        )
                    case .fullCamera:
                        ARCameraMappingView(coordinator: coordinator)
                    }
                }

                // 3. Bottom Control Deck (Heatmap Metric + Map Tools + Scan / Optimizer Buttons)
                bottomControlDeck
            }
            .background(Color(red: 0.06, green: 0.07, blue: 0.11))
            .navigationTitle("Wi-Fi House Mapper")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .automatic) {
                    Picker("View", selection: $displayMode) {
                        ForEach(WorkspaceDisplayMode.allCases) { mode in
                            Text(mode.rawValue).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 240)
                }

                ToolbarItem(placement: .automatic) {
                    Menu {
                        Button {
                            coordinator.loadDemoHouseSurvey()
                        } label: {
                            Label("Load 2-Bedroom Demo House", systemImage: "house.fill")
                        }

                        Button {
                            coordinator.recomputeRecommendations()
                            showAdvisorSheet = true
                        } label: {
                            Label("Router & Mesh Placement Advisor", systemImage: "sparkles")
                        }

                        Button {
                            coordinator.saveProjectToDisk()
                        } label: {
                            Label("Save Survey Project", systemImage: "square.and.arrow.down")
                        }

                        Divider()

                        Button(role: .destructive) {
                            coordinator.project = SurveyProject()
                            coordinator.previewingRecommendation = nil
                        } label: {
                            Label("Clear & Start Fresh Map", systemImage: "trash")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle.fill")
                            .font(.title3)
                    }
                }
            }
            .sheet(isPresented: $showAdvisorSheet) {
                PlacementAdvisorSheet(coordinator: coordinator)
            }
            .alert("Label Room", isPresented: $showRoomLabelPrompt) {
                TextField("e.g. Living Room, Kitchen, Office", text: $roomLabelText)
                Button("Add Label") {
                    if !roomLabelText.isEmpty {
                        coordinator.addRoomLabel(roomLabelText, at: pendingRoomLabelPoint)
                    }
                }
                Button("Cancel", role: .cancel) {}
            }
            .onAppear {
                if coordinator.project.occupancyGrid.cells.isEmpty && !coordinator.loadProjectFromDisk() {
                    coordinator.loadDemoHouseSurvey()
                }
            }
        }
    }

    // MARK: - Live Telemetry Strip

    private var telemetryHeaderBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 14) {
                // Signal dBm Pill
                TelemetryMetricPill(
                    title: coordinator.wifiProbe.isEstimatedRssi ? "SIGNAL (EST)" : "SIGNAL (RSSI)",
                    value: "\(coordinator.wifiProbe.currentRssiDbm) dBm",
                    subtitle: signalQualityLabel(for: coordinator.wifiProbe.currentRssiDbm),
                    tint: signalTintColor(for: coordinator.wifiProbe.currentRssiDbm)
                )

                // Download Throughput Pill
                TelemetryMetricPill(
                    title: "THROUGHPUT",
                    value: String(format: "%.0f Mbps", coordinator.wifiProbe.currentDownloadMbps),
                    subtitle: String(format: "↑ %.0f Mbps", coordinator.wifiProbe.currentUploadMbps),
                    tint: .cyan
                )

                // Gateway Latency & Jitter Pill
                TelemetryMetricPill(
                    title: "PING / JITTER",
                    value: String(format: "%.1f ms", coordinator.wifiProbe.currentLatencyMs),
                    subtitle: String(format: "±%.1f ms", coordinator.wifiProbe.currentJitterMs),
                    tint: coordinator.wifiProbe.currentLatencyMs < 20 ? .green : .orange
                )

                // Mapped Area & Samples Pill
                TelemetryMetricPill(
                    title: "MAPPED HOUSE",
                    value: String(format: "%.0f m²", coordinator.project.occupancyGrid.mappedAreaSqMeters),
                    subtitle: "\(coordinator.project.samples.count) pts • \(coordinator.project.walls.count) walls",
                    tint: .purple
                )

                // GPS & Heading Lock Pill
                TelemetryMetricPill(
                    title: "GPS ANCHOR",
                    value: coordinator.project.originGPS != nil ? "Locked" : "Indoor SLAM",
                    subtitle: coordinator.wifiProbe.currentSSID ?? (coordinator.wifiProbe.localIPAddress ?? "Wi-Fi"),
                    tint: .blue
                )
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .background(Color(red: 0.10, green: 0.12, blue: 0.17))
    }

    // MARK: - Bottom Control Deck

    private var bottomControlDeck: some View {
        VStack(spacing: 10) {
            // Heatmap Metric Selector
            Picker("Heatmap Metric", selection: $coordinator.selectedHeatmapMetric) {
                ForEach(HeatmapMetric.allCases) { metric in
                    Text(metric.rawValue).tag(metric)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 12)

            // Map Interaction Tools (Explore, Place Router, Place Mesh, Add Wall, Label Room)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(MapInteractionMode.allCases) { mode in
                        Button {
                            interactionMode = mode
                        } label: {
                            HStack(spacing: 5) {
                                Image(systemName: mode.systemImage)
                                Text(mode.rawValue)
                            }
                            .font(.system(size: 12, weight: .semibold, design: .rounded))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 7)
                            .background(interactionMode == mode ? Color.blue : Color.white.opacity(0.12))
                            .foregroundColor(.white)
                            .clipShape(Capsule())
                        }
                    }
                }
                .padding(.horizontal, 12)
            }

            // Primary Action Row: Start/Pause Scan + Optimize Placement Advisor Button
            HStack(spacing: 12) {
                Button {
                    if coordinator.isMappingActive {
                        coordinator.stopMapping()
                    } else {
                        coordinator.startMapping()
                    }
                } label: {
                    HStack {
                        Image(systemName: coordinator.isMappingActive ? "stop.circle.fill" : "record.circle")
                        Text(coordinator.isMappingActive ? "Pause Mapping" : "Start House Scan")
                            .bold()
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(coordinator.isMappingActive ? Color.red : Color.green)
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }

                Button {
                    coordinator.recomputeRecommendations()
                    showAdvisorSheet = true
                } label: {
                    HStack {
                        Image(systemName: "sparkles")
                        Text("Optimize Mesh (\(coordinator.project.recommendations.count))")
                            .bold()
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(Color.purple)
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 8)
        }
        .padding(.top, 8)
        .background(Color(red: 0.09, green: 0.11, blue: 0.16))
    }

    private func signalQualityLabel(for rssi: Int) -> String {
        switch rssi {
        case -55...0: return "Excellent"
        case -67...(-56): return "Good"
        case -75...(-68): return "Fair"
        default: return "Dead Zone"
        }
    }

    private func signalTintColor(for rssi: Int) -> Color {
        switch rssi {
        case -55...0: return .cyan
        case -67...(-56): return .green
        case -75...(-68): return .yellow
        default: return .red
        }
    }
}

private struct TelemetryMetricPill: View {
    let title: String
    let value: String
    let subtitle: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.system(size: 9, weight: .bold, design: .rounded))
                .foregroundColor(.white.opacity(0.6))
            Text(value)
                .font(.system(size: 14, weight: .bold, design: .monospaced))
                .foregroundColor(tint)
            Text(subtitle)
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(.white.opacity(0.8))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color.white.opacity(0.07))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}
