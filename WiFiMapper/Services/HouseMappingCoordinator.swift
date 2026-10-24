import Foundation
import Combine
import CoreLocation
import simd
#if canImport(ARKit)
import ARKit
#endif
#if canImport(RoomPlan)
import RoomPlan
#endif

@MainActor
public final class HouseMappingCoordinator: NSObject, ObservableObject {

    // MARK: - Published Mapping & Project State

    @Published public var project: SurveyProject
    @Published public var currentPosition: Point2D = Point2D(x: 0, y: 0)
    @Published public var currentHeadingRadians: Double = 0.0
    @Published public var currentGPS: GPSCoordinate? = nil
    @Published public var isMappingActive: Bool = false
    @Published public var trackingStatusMessage: String = "Ready to scan house with Camera + IMU"
    @Published public var featurePointsTrackedCount: Int = 0
    @Published public var autoSampleDistanceMeters: Double = 1.25
    @Published public var useLiDARRoomPlanIfAvailable: Bool = true
    @Published public var selectedHeatmapMetric: HeatmapMetric = .signalDbm
    @Published public var previewingRecommendation: PlacementRecommendation? = nil

    // Wall drawing / pinning state in AR or 2D
    @Published public var pendingWallStartPoint: Point2D? = nil

    public let wifiProbe: WiFiProbeService

    private let locationManager = CLLocationManager()
    private var lastSampledPosition: Point2D? = nil
    private var lastTrajectoryPosition: Point2D? = nil
    private var detectedPlaneWallIds: [UUID: UUID] = [:] // ARPlaneAnchor.identifier -> WallSegment.id
    nonisolated(unsafe) private var frameCounter: Int = 0

    public init(wifiProbe: WiFiProbeService, initialProject: SurveyProject = SurveyProject()) {
        self.wifiProbe = wifiProbe
        self.project = initialProject
        super.init()
        setupLocationManager()
    }

    // MARK: - Hardware Capability Checks

    public var isLiDARRoomPlanSupported: Bool {
        #if os(iOS) && canImport(RoomPlan)
        if #available(iOS 16.0, *) {
            return RoomCaptureSession.isSupported
        }
        #endif
        return false
    }

    // MARK: - Start / Stop House Mapping

    public func startMapping() {
        isMappingActive = true
        locationManager.requestWhenInUseAuthorization()
        locationManager.startUpdatingLocation()
        #if os(iOS)
        locationManager.startUpdatingHeading()
        #endif
        wifiProbe.startContinuousProbing(intervalSeconds: 2.0)
        trackingStatusMessage = "Move iPhone slowly to map floor & walls..."
    }

    public func stopMapping() {
        isMappingActive = false
        locationManager.stopUpdatingLocation()
        #if os(iOS)
        locationManager.stopUpdatingHeading()
        #endif
        wifiProbe.stopContinuousProbing()
        recomputeRecommendations()
        saveProjectToDisk()
        trackingStatusMessage = "Mapping paused — \(project.samples.count) Wi-Fi samples recorded"
    }

    // MARK: - Pose & vSLAM Frame Integration (Robot-Vacuum Style Mapping)

    /// Called at 10-30 Hz with the camera's 2D meter coordinates, yaw heading, and 3D vSLAM feature points projected to 2D.
    public func processCameraPoseUpdate(
        position: Point2D,
        yawRadians: Double,
        vslamObstaclePoints: [Point2D],
        freeRayEndpoints: [Point2D]
    ) {
        guard isMappingActive else { return }
        self.currentPosition = position
        self.currentHeadingRadians = yawRadians
        self.featurePointsTrackedCount = vslamObstaclePoints.count + freeRayEndpoints.count

        // 1. Mark circular walked footprint around user's current position as free interior space
        project.occupancyGrid.markWalkedFootprint(at: position, radiusMeters: 0.85)

        // 2. Append to trajectory trail if moved >= 0.18m
        if let lastPos = lastTrajectoryPosition {
            if lastPos.distance(to: position) >= 0.18 {
                project.walkedTrajectory.append(position)
                lastTrajectoryPosition = position
            }
        } else {
            project.walkedTrajectory.append(position)
            lastTrajectoryPosition = position
        }

        // 3. Integrate vSLAM camera rays into the 2D occupancy grid (free space along ray, obstacle at wall feature hit)
        for freePt in freeRayEndpoints.prefix(25) {
            project.occupancyGrid.integrateRay(from: position, to: freePt, isObstacleHit: false)
        }
        for obstaclePt in vslamObstaclePoints.prefix(40) {
            project.occupancyGrid.integrateRay(from: position, to: obstaclePt, isObstacleHit: true)
        }

        // 4. Auto-capture a Wi-Fi signal + throughput sample every `autoSampleDistanceMeters` walked
        if let lastSamplePos = lastSampledPosition {
            if lastSamplePos.distance(to: position) >= autoSampleDistanceMeters {
                recordWiFiSampleAtCurrentPosition()
            }
        } else {
            recordWiFiSampleAtCurrentPosition()
        }
    }

    // MARK: - Manual & Automatic Actions

    public func recordWiFiSampleAtCurrentPosition() {
        let sample = wifiProbe.captureSample(at: currentPosition, gps: currentGPS)
        project.samples.append(sample)
        project.updatedAt = Date()
        lastSampledPosition = currentPosition
    }

    public func placeNetworkNode(
        name: String,
        role: NodeRole,
        at position: Point2D? = nil,
        band: WiFiBand = .band5GHz,
        backhaul: BackhaulType = .wirelessMesh
    ) {
        let targetPos = position ?? currentPosition
        let node = NetworkNode(
            name: name,
            role: role,
            position: targetPos,
            heightMeters: 1.1,
            band: band,
            txPowerDbm: 21.0,
            backhaul: role == .primaryRouter ? .wiredEthernet : backhaul
        )
        project.nodes.append(node)
        project.updatedAt = Date()
        recomputeRecommendations()
    }

    public func updateNodePosition(nodeId: UUID, newPosition: Point2D) {
        guard let idx = project.nodes.firstIndex(where: { $0.id == nodeId }) else { return }
        project.nodes[idx].position = newPosition
        project.updatedAt = Date()
        recomputeRecommendations()
    }

    public func removeNode(nodeId: UUID) {
        project.nodes.removeAll { $0.id == nodeId }
        recomputeRecommendations()
    }

    /// Two-tap wall pinning in AR or on the 2D map (for non-LiDAR devices or quick wall marking)
    public func markWallCorner(at point: Point2D? = nil, material: WallMaterial = .drywall) {
        let pt = point ?? currentPosition
        if let start = pendingWallStartPoint {
            if start.distance(to: pt) >= 0.25 {
                let wall = WallSegment(start: start, end: pt, material: material)
                project.walls.append(wall)
                // Also mark along wall in occupancy grid
                let startCell = project.occupancyGrid.index(for: start)
                let endCell = project.occupancyGrid.index(for: pt)
                for cell in OccupancyGrid.bresenhamLine(from: startCell, to: endCell) {
                    project.occupancyGrid.updateCell(cell, deltaLogOdds: +3.0)
                }
                recomputeRecommendations()
            }
            pendingWallStartPoint = nil
        } else {
            pendingWallStartPoint = pt
        }
    }

    public func addRoomLabel(_ name: String, at position: Point2D? = nil) {
        let label = RoomLabel(name: name, position: position ?? currentPosition)
        project.roomLabels.append(label)
    }

    public func recomputeRecommendations() {
        project.recommendations = PlacementOptimizer.generateRecommendations(
            grid: project.occupancyGrid,
            walls: project.walls,
            samples: project.samples,
            currentNodes: project.nodes
        )
    }

    public func applyRecommendation(_ recommendation: PlacementRecommendation) {
        project.nodes = recommendation.recommendedNodes.map { node in
            var copy = node
            if copy.role == .recommendedNode {
                copy.role = .meshNode
            }
            return copy
        }
        previewingRecommendation = nil
        selectedHeatmapMetric = .predictedOptimization
        recomputeRecommendations()
        saveProjectToDisk()
    }

    // MARK: - Demo House Generator (For Instant Testing on Any Device or Simulator)

    public func loadDemoHouseSurvey() {
        var demo = SurveyProject(name: "2-Bedroom Home Survey")
        demo.originGPS = GPSCoordinate(
            latitude: -33.8688,
            longitude: 151.2093,
            altitude: 24.0,
            horizontalAccuracy: 4.2,
            magneticHeadingDegrees: 18.0
        )

        // Carve a 12m x 8m house interior (-6m..+6m X, -4m..+4m Y)
        let cellSize = demo.occupancyGrid.cellSizeMeters
        let minGx = Int(-6.0 / cellSize)
        let maxGx = Int(6.0 / cellSize)
        let minGy = Int(-4.0 / cellSize)
        let maxGy = Int(4.0 / cellSize)

        for gx in minGx...maxGx {
            for gy in minGy...maxGy {
                let idx = GridCellIndex(gx: gx, gy: gy)
                let isExteriorWall = (gx == minGx || gx == maxGx || gy == minGy || gy == maxGy)
                demo.occupancyGrid.updateCell(idx, deltaLogOdds: isExteriorWall ? 3.5 : -2.5)
            }
        }

        // Exterior + Interior partition walls (Living Room on Left, Hallway Middle, Master Bedroom & Office on Right)
        demo.walls = [
            // Exterior perimeter
            WallSegment(start: Point2D(x: -6, y: -4), end: Point2D(x: 6, y: -4), material: .brick),
            WallSegment(start: Point2D(x: 6, y: -4), end: Point2D(x: 6, y: 4), material: .brick),
            WallSegment(start: Point2D(x: 6, y: 4), end: Point2D(x: -6, y: 4), material: .brick),
            WallSegment(start: Point2D(x: -6, y: 4), end: Point2D(x: -6, y: -4), material: .brick),
            // Interior wall dividing Living Room (x < -1) from Hall/Bedrooms (x > -1)
            WallSegment(start: Point2D(x: -1.0, y: -4.0), end: Point2D(x: -1.0, y: -0.6), material: .drywall),
            WallSegment(start: Point2D(x: -1.0, y: -0.6), end: Point2D(x: -1.0, y: 0.6), material: .wood, isDoorway: true),
            WallSegment(start: Point2D(x: -1.0, y: 0.6), end: Point2D(x: -1.0, y: 4.0), material: .drywall),
            // Interior wall dividing Bedrooms (x = 2.5)
            WallSegment(start: Point2D(x: 2.5, y: -4.0), end: Point2D(x: 2.5, y: -0.8), material: .brick),
            WallSegment(start: Point2D(x: 2.5, y: 0.4), end: Point2D(x: 2.5, y: 4.0), material: .drywall),
            // Horizontal divider between Master Bedroom and Office
            WallSegment(start: Point2D(x: -1.0, y: 0.2), end: Point2D(x: 6.0, y: 0.2), material: .drywall)
        ]

        demo.roomLabels = [
            RoomLabel(name: "Living Room", position: Point2D(x: -3.6, y: 1.5)),
            RoomLabel(name: "Kitchen", position: Point2D(x: -3.6, y: -2.2)),
            RoomLabel(name: "Hallway", position: Point2D(x: 0.7, y: -1.8)),
            RoomLabel(name: "Master Bedroom", position: Point2D(x: 4.2, y: 2.2)),
            RoomLabel(name: "Home Office", position: Point2D(x: 4.2, y: -2.2))
        ]

        // Place Primary Router in far left corner of Living Room (typical suboptimal ISP corner placement)
        let cornerRouter = NetworkNode(
            name: "Living Room ISP Router",
            role: .primaryRouter,
            position: Point2D(x: -5.2, y: 3.1),
            heightMeters: 1.0,
            band: .band5GHz,
            txPowerDbm: 21.0,
            backhaul: .wiredEthernet
        )
        demo.nodes = [cornerRouter]

        // Generate walked trajectory & realistic Wi-Fi samples across all rooms
        let waypoints: [Point2D] = [
            Point2D(x: -5.0, y: 2.8), Point2D(x: -4.2, y: 2.0), Point2D(x: -3.5, y: 0.8),
            Point2D(x: -3.8, y: -1.5), Point2D(x: -2.5, y: -2.5), Point2D(x: -1.2, y: 0.0),
            Point2D(x: 0.5, y: 0.0), Point2D(x: 0.8, y: 2.2), Point2D(x: 1.2, y: -2.0),
            Point2D(x: 3.8, y: 2.2), Point2D(x: 4.8, y: 2.8), Point2D(x: 4.2, y: -2.0),
            Point2D(x: 5.1, y: -2.8)
        ]
        demo.walkedTrajectory = waypoints

        for pt in waypoints {
            let eval = RFPropagationEngine.evaluateNetworkAtPoint(
                point: pt,
                nodes: demo.nodes,
                walls: demo.walls,
                baselineMaxMbps: 680.0
            )
            let rssiInt = Int(eval.bestRssi.rounded())
            let latency = rssiInt >= -65 ? 5.2 : (rssiInt >= -74 ? 19.5 : 68.0)
            let jitter = rssiInt >= -65 ? 1.1 : (rssiInt >= -74 ? 6.4 : 24.0)
            demo.samples.append(
                WiFiSample(
                    position: pt,
                    gpsCoordinate: demo.originGPS,
                    rssiDbm: rssiInt,
                    isEstimatedRssi: false,
                    downloadMbps: eval.estimatedMbps,
                    uploadMbps: eval.estimatedMbps * 0.45,
                    latencyMs: latency,
                    jitterMs: jitter,
                    packetLossPercent: rssiInt < -76 ? 3.5 : 0.0,
                    ssid: "HomeMesh-5G",
                    bssid: "a4:83:e7:11:22:33"
                )
            )
        }

        self.project = demo
        self.currentPosition = waypoints.last ?? Point2D(x: 0, y: 0)
        recomputeRecommendations()
        trackingStatusMessage = "Loaded 2-Bedroom Demo House — See Dead Zones in Office & Master Bedroom"
    }

    // MARK: - Persistence (JSON Save / Load)

    private var saveFileURL: URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return docs.appendingPathComponent("wifi_mapper_survey.json")
    }

    public func saveProjectToDisk() {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? encoder.encode(project) {
            try? data.write(to: saveFileURL, options: .atomic)
        }
    }

    public func loadProjectFromDisk() -> Bool {
        guard let data = try? Data(contentsOf: saveFileURL) else { return false }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard let loaded = try? decoder.decode(SurveyProject.self, from: data) else { return false }
        self.project = loaded
        recomputeRecommendations()
        return true
    }

    // MARK: - Vertical ARPlaneAnchor -> Vector WallSegment Extraction

    public func upsertDetectedVerticalWall(anchorId: UUID, start: Point2D, end: Point2D) {
        guard start.distance(to: end) >= 0.35 else { return }
        if let existingWallId = detectedPlaneWallIds[anchorId],
           let idx = project.walls.firstIndex(where: { $0.id == existingWallId }) {
            project.walls[idx].start = start
            project.walls[idx].end = end
        } else {
            let newWall = WallSegment(start: start, end: end, material: .drywall, confidence: 0.9)
            detectedPlaneWallIds[anchorId] = newWall.id
            project.walls.append(newWall)
        }

        // Mark wall cells in occupancy grid
        let c0 = project.occupancyGrid.index(for: start)
        let c1 = project.occupancyGrid.index(for: end)
        for cell in OccupancyGrid.bresenhamLine(from: c0, to: c1) {
            project.occupancyGrid.updateCell(cell, deltaLogOdds: +2.2)
        }
    }

    private func setupLocationManager() {
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyBestForNavigation
    }
}

// MARK: - CLLocationManagerDelegate (GPS Anchor + Compass Heading)

extension HouseMappingCoordinator: CLLocationManagerDelegate {

    nonisolated public func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        let gps = GPSCoordinate(
            latitude: loc.coordinate.latitude,
            longitude: loc.coordinate.longitude,
            altitude: loc.altitude,
            horizontalAccuracy: loc.horizontalAccuracy,
            magneticHeadingDegrees: nil
        )
        Task { @MainActor in
            self.currentGPS = gps
            if self.project.originGPS == nil {
                self.project.originGPS = gps
            }
        }
    }

    #if os(iOS)
    nonisolated public func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        let headingDeg = newHeading.trueHeading >= 0 ? newHeading.trueHeading : newHeading.magneticHeading
        Task { @MainActor in
            if var gps = self.currentGPS {
                gps.magneticHeadingDegrees = headingDeg
                self.currentGPS = gps
            }
        }
    }
    #endif
}

// MARK: - ARKit Session Delegate Helpers (iOS Camera + IMU vSLAM)

#if os(iOS) && canImport(ARKit)
extension HouseMappingCoordinator: ARSessionDelegate {

    nonisolated public func session(_ session: ARSession, didUpdate frame: ARFrame) {
        frameCounter += 1
        // Process every 4th frame (~15 Hz) to keep UI & battery smooth
        guard frameCounter % 4 == 0 else { return }

        let cameraTransform = frame.camera.transform
        let camX = Double(cameraTransform.columns.3.x)
        let camY = Double(cameraTransform.columns.3.y)
        let camZ = Double(cameraTransform.columns.3.z)
        let camPos2D = Point2D(x: camX, y: camZ)

        // Extract camera yaw around vertical Y-axis
        let forwardX = -Double(cameraTransform.columns.2.x)
        let forwardZ = -Double(cameraTransform.columns.2.z)
        let yaw = atan2(forwardX, forwardZ)

        // Extract 3D visual feature points from ARKit vSLAM point cloud
        var obstacleHits: [Point2D] = []
        var floorHits: [Point2D] = []

        if let rawPoints = frame.rawFeaturePoints?.points {
            let strideStep = max(1, rawPoints.count / 60)
            for i in stride(from: 0, to: rawPoints.count, by: strideStep) {
                let pt = rawPoints[i]
                let pt2D = Point2D(x: Double(pt.x), y: Double(pt.z))
                let dist = camPos2D.distance(to: pt2D)
                guard dist >= 0.4 && dist <= 4.5 else { continue }

                let relativeHeight = Double(pt.y) - camY
                if relativeHeight >= -0.45 && relativeHeight <= 0.75 {
                    // Point is near wall/obstacle height
                    obstacleHits.append(pt2D)
                } else if relativeHeight < -0.65 {
                    // Point is on the floor surface
                    floorHits.append(pt2D)
                }
            }
        }

        let trackingState = frame.camera.trackingState
        let statusText: String
        switch trackingState {
        case .normal:
            statusText = "vSLAM Tracking Active (\(obstacleHits.count + floorHits.count) keypoints)"
        case .limited(let reason):
            switch reason {
            case .initializing: statusText = "Initializing Camera + IMU SLAM — move iPhone slowly"
            case .excessiveMotion: statusText = "Slow down movement for accurate wall mapping"
            case .insufficientFeatures: statusText = "Point camera at textured walls/edges"
            case .relocalizing: statusText = "Relocalizing position..."
            @unknown default: statusText = "Limited tracking"
            }
        case .notAvailable:
            statusText = "AR Tracking unavailable"
        }

        let finalObstacleHits = obstacleHits
        let finalFloorHits = floorHits

        Task { @MainActor in
            self.trackingStatusMessage = statusText
            self.processCameraPoseUpdate(
                position: camPos2D,
                yawRadians: yaw,
                vslamObstaclePoints: finalObstacleHits,
                freeRayEndpoints: finalFloorHits
            )
        }
    }

    nonisolated public func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        handlePlaneAnchors(anchors)
    }

    nonisolated public func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        handlePlaneAnchors(anchors)
    }

    nonisolated private func handlePlaneAnchors(_ anchors: [ARAnchor]) {
        for anchor in anchors {
            guard let plane = anchor as? ARPlaneAnchor,
                  plane.alignment == .vertical else { continue }

            let transform = plane.transform
            let center = SIMD3<Float>(plane.center.x, plane.center.y, plane.center.z)
            let halfLength = plane.planeExtent.width * 0.5

            let localStart = SIMD4<Float>(center.x - halfLength, center.y, center.z, 1.0)
            let localEnd = SIMD4<Float>(center.x + halfLength, center.y, center.z, 1.0)
            let worldStart = simd_mul(transform, localStart)
            let worldEnd = simd_mul(transform, localEnd)

            let pStart = Point2D(x: Double(worldStart.x), y: Double(worldStart.z))
            let pEnd = Point2D(x: Double(worldEnd.x), y: Double(worldEnd.z))
            let anchorId = plane.identifier

            Task { @MainActor in
                self.upsertDetectedVerticalWall(anchorId: anchorId, start: pStart, end: pEnd)
            }
        }
    }
}
#endif
