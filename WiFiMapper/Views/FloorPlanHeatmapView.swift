import SwiftUI

public enum MapInteractionMode: String, CaseIterable, Identifiable {
    case panZoom = "Explore"
    case placePrimaryRouter = "Place Router"
    case placeMeshNode = "Place Mesh"
    case drawWall = "Add Wall"
    case labelRoom = "Label Room"

    public var id: String { rawValue }

    public var systemImage: String {
        switch self {
        case .panZoom: return "hand.draw"
        case .placePrimaryRouter: return "wifi.router.fill"
        case .placeMeshNode: return "dot.radiowaves.left.and.right"
        case .drawWall: return "square.split.bottomrightquarter"
        case .labelRoom: return "tag"
        }
    }
}

public struct FloorPlanHeatmapView: View {
    @ObservedObject var coordinator: HouseMappingCoordinator
    @Binding var interactionMode: MapInteractionMode
    var onRequestRoomLabelAt: ((Point2D) -> Void)? = nil

    @State private var zoomScale: CGFloat = 1.0
    @State private var lastZoomScale: CGFloat = 1.0
    @State private var panOffset: CGSize = .zero
    @State private var lastPanOffset: CGSize = .zero
    @State private var showSampleLabels: Bool = true
    @State private var showTrajectory: Bool = true

    private let basePixelsPerMeter: CGFloat = 34.0

    public init(
        coordinator: HouseMappingCoordinator,
        interactionMode: Binding<MapInteractionMode>,
        onRequestRoomLabelAt: ((Point2D) -> Void)? = nil
    ) {
        self.coordinator = coordinator
        self._interactionMode = interactionMode
        self.onRequestRoomLabelAt = onRequestRoomLabelAt
    }

    public var body: some View {
        ZStack(alignment: .bottom) {
            GeometryReader { geo in
                let centerScreen = CGPoint(
                    x: geo.size.width * 0.5 + panOffset.width,
                    y: geo.size.height * 0.5 + panOffset.height
                )
                let ppm = basePixelsPerMeter * zoomScale

                Canvas { context, size in
                    // 1. Background 1-meter Robot Vacuum Reference Grid
                    drawMeterGrid(context: &context, size: size, center: centerScreen, ppm: ppm)

                    // 2. Active Nodes to Render (either Previewing Recommendation or Current Project Nodes)
                    let activeNodes = coordinator.previewingRecommendation?.recommendedNodes ?? coordinator.project.nodes

                    // 3. Interpolated Color-Coded Wi-Fi Heatmap Cells
                    let heatmapCells = RFPropagationEngine.generateHeatmap(
                        grid: coordinator.project.occupancyGrid,
                        samples: coordinator.project.samples,
                        nodes: activeNodes,
                        walls: coordinator.project.walls,
                        metric: coordinator.previewingRecommendation != nil ? .predictedOptimization : coordinator.selectedHeatmapMetric
                    )

                    let cellPixelSize = CGFloat(coordinator.project.occupancyGrid.cellSizeMeters) * ppm + 1.0
                    for cell in heatmapCells {
                        let screenPt = worldToScreen(cell.center, center: centerScreen, ppm: ppm)
                        let rect = CGRect(
                            x: screenPt.x - cellPixelSize * 0.5,
                            y: screenPt.y - cellPixelSize * 0.5,
                            width: cellPixelSize,
                            height: cellPixelSize
                        )
                        let color = Self.heatmapColor(for: cell.normalizedValue)
                        context.fill(Path(rect), with: .color(color.opacity(0.68)))
                    }

                    // 4. vSLAM Occupancy Grid Obstacle Cells (Grey/Dark Wall Pixels from Camera Feature Points)
                    for (idx, state) in coordinator.project.occupancyGrid.cells where state == .wallObstacle {
                        let worldPt = coordinator.project.occupancyGrid.centerPoint(for: idx)
                        let screenPt = worldToScreen(worldPt, center: centerScreen, ppm: ppm)
                        let rect = CGRect(
                            x: screenPt.x - cellPixelSize * 0.5,
                            y: screenPt.y - cellPixelSize * 0.5,
                            width: cellPixelSize,
                            height: cellPixelSize
                        )
                        context.fill(Path(rect), with: .color(Color.white.opacity(0.32)))
                    }

                    // 5. Vector Architectural Walls & Doorways
                    for wall in coordinator.project.walls {
                        let p1 = worldToScreen(wall.start, center: centerScreen, ppm: ppm)
                        let p2 = worldToScreen(wall.end, center: centerScreen, ppm: ppm)
                        var path = Path()
                        path.move(to: p1)
                        path.addLine(to: p2)

                        if wall.isDoorway {
                            context.stroke(
                                path,
                                with: .color(.cyan.opacity(0.8)),
                                style: StrokeStyle(lineWidth: 3.0, lineCap: .round, dash: [6, 5])
                            )
                        } else {
                            let thickness: CGFloat = (wall.material == .brick || wall.material == .concrete) ? 5.5 : 3.5
                            context.stroke(
                                path,
                                with: .color(.white),
                                style: StrokeStyle(lineWidth: thickness, lineCap: .round)
                            )
                        }
                    }

                    // Pending Wall Start Indicator
                    if let pendingStart = coordinator.pendingWallStartPoint {
                        let pStart = worldToScreen(pendingStart, center: centerScreen, ppm: ppm)
                        let circle = Path(ellipseIn: CGRect(x: pStart.x - 6, y: pStart.y - 6, width: 12, height: 12))
                        context.fill(circle, with: .color(.yellow))
                    }

                    // 6. Walked Robot-Vacuum Trajectory Path
                    if showTrajectory && coordinator.project.walkedTrajectory.count > 1 {
                        var trail = Path()
                        for (i, pt) in coordinator.project.walkedTrajectory.enumerated() {
                            let sPt = worldToScreen(pt, center: centerScreen, ppm: ppm)
                            if i == 0 {
                                trail.move(to: sPt)
                            } else {
                                trail.addLine(to: sPt)
                            }
                        }
                        context.stroke(
                            trail,
                            with: .color(.white.opacity(0.55)),
                            style: StrokeStyle(lineWidth: 2.0, lineCap: .round, lineJoin: .round, dash: [4, 4])
                        )
                    }

                    // 7. Room Labels
                    for room in coordinator.project.roomLabels {
                        let sPt = worldToScreen(room.position, center: centerScreen, ppm: ppm)
                        let text = Text(room.name)
                            .font(.system(size: 11, weight: .bold, design: .rounded))
                            .foregroundColor(.white.opacity(0.9))
                        context.draw(text, at: sPt)
                    }

                    // 8. Measured Wi-Fi Sample Dots & dBm / Mbps Labels
                    if showSampleLabels {
                        for sample in coordinator.project.samples {
                            let sPt = worldToScreen(sample.position, center: centerScreen, ppm: ppm)
                            let dotColor = Self.heatmapColor(for: sample.normalizedSignalScore)
                            let dotRect = CGRect(x: sPt.x - 4.5, y: sPt.y - 4.5, width: 9, height: 9)
                            context.fill(Path(ellipseIn: dotRect), with: .color(dotColor))
                            context.stroke(Path(ellipseIn: dotRect), with: .color(.black.opacity(0.8)), lineWidth: 1.2)

                            if zoomScale >= 0.85 {
                                let labelString: String
                                switch coordinator.selectedHeatmapMetric {
                                case .signalDbm, .predictedOptimization:
                                    labelString = "\(sample.rssiDbm)dBm"
                                case .throughputMbps:
                                    labelString = "\(Int(sample.downloadMbps))M"
                                case .latencyMs:
                                    labelString = "\(Int(sample.latencyMs))ms"
                                }
                                let badge = Text(labelString)
                                    .font(.system(size: 9, weight: .semibold, design: .monospaced))
                                    .foregroundColor(.white)
                                context.draw(badge, at: CGPoint(x: sPt.x, y: sPt.y - 11))
                            }
                        }
                    }

                    // 9. Wireless Mesh Backhaul Links & Router/Mesh Node Icons
                    if let primary = activeNodes.first(where: { $0.role == .primaryRouter }) ?? activeNodes.first {
                        let primaryScreen = worldToScreen(primary.position, center: centerScreen, ppm: ppm)
                        for node in activeNodes where node.id != primary.id {
                            let nodeScreen = worldToScreen(node.position, center: centerScreen, ppm: ppm)
                            var linkPath = Path()
                            linkPath.move(to: primaryScreen)
                            linkPath.addLine(to: nodeScreen)
                            context.stroke(
                                linkPath,
                                with: .color(.cyan.opacity(0.85)),
                                style: StrokeStyle(lineWidth: 2.2, dash: [6, 4])
                            )
                        }
                    }

                    for node in activeNodes {
                        let sPt = worldToScreen(node.position, center: centerScreen, ppm: ppm)
                        let pulseRect = CGRect(x: sPt.x - 16, y: sPt.y - 16, width: 32, height: 32)
                        let nodeColor: Color = node.role == .primaryRouter
                            ? .blue
                            : (node.role == .recommendedNode ? .purple : .teal)

                        context.fill(Path(ellipseIn: pulseRect), with: .color(nodeColor.opacity(0.28)))
                        let coreRect = CGRect(x: sPt.x - 9, y: sPt.y - 9, width: 18, height: 18)
                        context.fill(Path(ellipseIn: coreRect), with: .color(nodeColor))
                        context.stroke(Path(ellipseIn: coreRect), with: .color(.white), lineWidth: 2.0)

                        let title = Text(node.name)
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(.white)
                        context.draw(title, at: CGPoint(x: sPt.x, y: sPt.y + 18))
                    }

                    // 10. Current User / Camera Pose + Radar Cone
                    let userScreen = worldToScreen(coordinator.currentPosition, center: centerScreen, ppm: ppm)
                    var fovCone = Path()
                    fovCone.move(to: userScreen)
                    let heading = CGFloat(coordinator.currentHeadingRadians) - .pi / 2
                    fovCone.addArc(
                        center: userScreen,
                        radius: 28,
                        startAngle: .radians(Double(heading - 0.45)),
                        endAngle: .radians(Double(heading + 0.45)),
                        clockwise: false
                    )
                    fovCone.closeSubpath()
                    context.fill(fovCone, with: .color(.cyan.opacity(0.32)))

                    let userDot = CGRect(x: userScreen.x - 6, y: userScreen.y - 6, width: 12, height: 12)
                    context.fill(Path(ellipseIn: userDot), with: .color(.cyan))
                    context.stroke(Path(ellipseIn: userDot), with: .color(.white), lineWidth: 2.0)
                }
                .background(Color(red: 0.07, green: 0.09, blue: 0.13))
                .contentShape(Rectangle())
                .gesture(
                    SpatialTapGesture()
                        .onEnded { event in
                            let worldPt = screenToWorld(event.location, center: centerScreen, ppm: ppm)
                            handleMapTap(at: worldPt)
                        }
                )
                .gesture(
                    DragGesture(minimumDistance: 6)
                        .onChanged { value in
                            panOffset = CGSize(
                                width: lastPanOffset.width + value.translation.width,
                                height: lastPanOffset.height + value.translation.height
                            )
                        }
                        .onEnded { _ in
                            lastPanOffset = panOffset
                        }
                )
                .gesture(
                    MagnificationGesture()
                        .onChanged { value in
                            zoomScale = min(max(lastZoomScale * value, 0.35), 4.0)
                        }
                        .onEnded { _ in
                            lastZoomScale = zoomScale
                        }
                )
            }

            // Bottom Floating Legend & Controls Overlay
            VStack(spacing: 8) {
                if let preview = coordinator.previewingRecommendation {
                    HStack {
                        Image(systemName: "sparkles")
                            .foregroundColor(.yellow)
                        Text("Previewing: \(preview.title)")
                            .font(.caption.bold())
                            .foregroundColor(.white)
                        Spacer()
                        Button("Apply") {
                            coordinator.applyRecommendation(preview)
                        }
                        .font(.caption.bold())
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                        .background(Color.green)
                        .foregroundColor(.black)
                        .clipShape(Capsule())

                        Button("Exit") {
                            coordinator.previewingRecommendation = nil
                        }
                        .font(.caption)
                        .foregroundColor(.white.opacity(0.8))
                    }
                    .padding(10)
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }

                HStack(spacing: 12) {
                    // Color Scale Bar
                    HStack(spacing: 4) {
                        Text("Weak")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundColor(.red)
                        LinearGradient(
                            colors: [.red, .orange, .yellow, .green, .cyan],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                        .frame(width: 90, height: 8)
                        .clipShape(Capsule())
                        Text("Strong")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundColor(.cyan)
                    }

                    Spacer()

                    Toggle("dBm Labels", isOn: $showSampleLabels)
                        .labelsHidden()
                    Text("Labels")
                        .font(.caption2)
                        .foregroundColor(.white.opacity(0.8))

                    Button {
                        withAnimation(.spring()) {
                            panOffset = .zero
                            lastPanOffset = .zero
                            zoomScale = 1.0
                            lastZoomScale = 1.0
                        }
                    } label: {
                        Image(systemName: "scope")
                            .font(.system(size: 14, weight: .bold))
                            .padding(7)
                            .background(Color.white.opacity(0.15))
                            .clipShape(Circle())
                    }
                    .foregroundColor(.white)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(Color.black.opacity(0.72))
                .clipShape(Capsule())
            }
            .padding(12)
        }
    }

    // MARK: - Coordinate Transforms & Drawing Helpers

    private func worldToScreen(_ pt: Point2D, center: CGPoint, ppm: CGFloat) -> CGPoint {
        CGPoint(
            x: center.x + CGFloat(pt.x) * ppm,
            y: center.y - CGFloat(pt.y) * ppm // Invert Y so +Y is North/Up on screen
        )
    }

    private func screenToWorld(_ screenPt: CGPoint, center: CGPoint, ppm: CGFloat) -> Point2D {
        Point2D(
            x: Double((screenPt.x - center.x) / ppm),
            y: Double((center.y - screenPt.y) / ppm)
        )
    }

    private func handleMapTap(at worldPoint: Point2D) {
        switch interactionMode {
        case .panZoom:
            break
        case .placePrimaryRouter:
            coordinator.placeNetworkNode(
                name: "Primary Router",
                role: .primaryRouter,
                at: worldPoint
            )
            interactionMode = .panZoom
        case .placeMeshNode:
            let meshIndex = coordinator.project.nodes.filter { $0.role == .meshNode }.count + 1
            coordinator.placeNetworkNode(
                name: "Mesh Node #\(meshIndex)",
                role: .meshNode,
                at: worldPoint
            )
            interactionMode = .panZoom
        case .drawWall:
            coordinator.markWallCorner(at: worldPoint)
        case .labelRoom:
            onRequestRoomLabelAt?(worldPoint)
            interactionMode = .panZoom
        }
    }

    private func drawMeterGrid(context: inout GraphicsContext, size: CGSize, center: CGPoint, ppm: CGFloat) {
        let step = max(ppm, 16.0)
        var gridPath = Path()

        var x = center.x.truncatingRemainder(dividingBy: step)
        while x < size.width {
            gridPath.move(to: CGPoint(x: x, y: 0))
            gridPath.addLine(to: CGPoint(x: x, y: size.height))
            x += step
        }

        var y = center.y.truncatingRemainder(dividingBy: step)
        while y < size.height {
            gridPath.move(to: CGPoint(x: 0, y: y))
            gridPath.addLine(to: CGPoint(x: size.width, y: y))
            y += step
        }

        context.stroke(gridPath, with: .color(.white.opacity(0.06)), lineWidth: 1.0)
    }

    /// Maps normalized signal/throughput score [0.0 .. 1.0] to a vivid Red -> Orange -> Yellow -> Green -> Cyan gradient
    public static func heatmapColor(for normalizedValue: Double) -> Color {
        let t = min(max(normalizedValue, 0.0), 1.0)
        if t < 0.25 {
            // Red to Orange
            let local = t / 0.25
            return Color(red: 0.95, green: 0.15 + 0.40 * local, blue: 0.15)
        } else if t < 0.55 {
            // Orange to Yellow
            let local = (t - 0.25) / 0.30
            return Color(red: 0.95, green: 0.55 + 0.40 * local, blue: 0.10)
        } else if t < 0.82 {
            // Yellow to Vibrant Green
            let local = (t - 0.55) / 0.27
            return Color(red: 0.95 * (1.0 - local) + 0.12 * local, green: 0.90, blue: 0.15 + 0.25 * local)
        } else {
            // Vibrant Green to Cyan
            let local = (t - 0.82) / 0.18
            return Color(red: 0.10, green: 0.92, blue: 0.40 + 0.55 * local)
        }
    }
}
