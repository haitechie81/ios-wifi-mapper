import Foundation
import CoreGraphics

// MARK: - 2D Meter-Space Coordinate (ARKit X-Z Plane Projected to 2D)

public struct Point2D: Codable, Hashable, Sendable {
    public var x: Double // Meters East/Right from scan origin
    public var y: Double // Meters North/Forward (ARKit Z) from scan origin

    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }

    public func distance(to other: Point2D) -> Double {
        let dx = x - other.x
        let dy = y - other.y
        return (dx * dx + dy * dy).squareRoot()
    }

    public func interpolated(to other: Point2D, t: Double) -> Point2D {
        Point2D(
            x: x + (other.x - x) * t,
            y: y + (other.y - y) * t
        )
    }

    public var cgPoint: CGPoint {
        CGPoint(x: x, y: y)
    }
}

// MARK: - GPS & Compass Anchor

public struct GPSCoordinate: Codable, Hashable, Sendable {
    public var latitude: Double
    public var longitude: Double
    public var altitude: Double
    public var horizontalAccuracy: Double
    public var magneticHeadingDegrees: Double?

    public init(
        latitude: Double,
        longitude: Double,
        altitude: Double = 0,
        horizontalAccuracy: Double = 5.0,
        magneticHeadingDegrees: Double? = nil
    ) {
        self.latitude = latitude
        self.longitude = longitude
        self.altitude = altitude
        self.horizontalAccuracy = horizontalAccuracy
        self.magneticHeadingDegrees = magneticHeadingDegrees
    }
}

// MARK: - Wall Material & Segments

public enum WallMaterial: String, Codable, CaseIterable, Identifiable, Sendable {
    case drywall = "Drywall / Plaster"
    case wood = "Wood / Door"
    case glass = "Glass Window"
    case brick = "Brick / Masonry"
    case concrete = "Concrete / Load-Bearing"

    public var id: String { rawValue }

    /// Typical RF attenuation (dB) at 5 GHz when passing through a single wall of this material
    public var attenuationDb5GHz: Double {
        switch self {
        case .drywall: return 4.0
        case .wood: return 3.5
        case .glass: return 3.0
        case .brick: return 8.5
        case .concrete: return 12.0
        }
    }

    /// Typical RF attenuation (dB) at 2.4 GHz
    public var attenuationDb2_4GHz: Double {
        switch self {
        case .drywall: return 3.0
        case .wood: return 2.5
        case .glass: return 2.0
        case .brick: return 6.0
        case .concrete: return 9.5
        }
    }
}

public struct WallSegment: Identifiable, Codable, Hashable, Sendable {
    public var id: UUID
    public var start: Point2D
    public var end: Point2D
    public var material: WallMaterial
    public var isDoorway: Bool
    public var confidence: Double // 0.0 - 1.0 from ARKit plane / vSLAM feature cluster

    public init(
        id: UUID = UUID(),
        start: Point2D,
        end: Point2D,
        material: WallMaterial = .drywall,
        isDoorway: Bool = false,
        confidence: Double = 1.0
    ) {
        self.id = id
        self.start = start
        self.end = end
        self.material = material
        self.isDoorway = isDoorway
        self.confidence = confidence
    }

    public var length: Double {
        start.distance(to: end)
    }

    public var midpoint: Point2D {
        start.interpolated(to: end, t: 0.5)
    }

    /// Checks if line segment AB intersects this wall segment CD
    public func intersectsSegment(from a: Point2D, to b: Point2D) -> Bool {
        Self.segmentsIntersect(a, b, start, end)
    }

    public static func segmentsIntersect(_ p1: Point2D, _ p2: Point2D, _ p3: Point2D, _ p4: Point2D) -> Bool {
        func ccw(_ A: Point2D, _ B: Point2D, _ C: Point2D) -> Double {
            (C.y - A.y) * (B.x - A.x) - (B.y - A.y) * (C.x - A.x)
        }
        let d1 = ccw(p3, p4, p1)
        let d2 = ccw(p3, p4, p2)
        let d3 = ccw(p1, p2, p3)
        let d4 = ccw(p1, p2, p4)

        if ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) &&
           ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0)) {
            return true
        }
        return false
    }
}

// MARK: - Robot-Vacuum Style Occupancy Grid

public enum OccupancyState: Int, Codable, Sendable {
    case unknown = 0
    case freeSpace = 1
    case wallObstacle = 2
}

public struct GridCellIndex: Hashable, Codable, Sendable {
    public var gx: Int
    public var gy: Int

    public init(gx: Int, gy: Int) {
        self.gx = gx
        self.gy = gy
    }
}

public struct OccupancyGrid: Codable, Sendable {
    /// Grid cell size in meters (default 0.20m = 20cm, matching LIDAR/vSLAM robot vacuums)
    public var cellSizeMeters: Double
    /// Sparse map of discovered cells so the house map can expand dynamically in any direction
    public var cells: [GridCellIndex: OccupancyState]
    /// Log-odds occupancy probabilities (-4.0 = definitely free, +4.0 = definitely wall)
    public var logOdds: [GridCellIndex: Double]

    public init(cellSizeMeters: Double = 0.20) {
        self.cellSizeMeters = cellSizeMeters
        self.cells = [:]
        self.logOdds = [:]
    }

    public func index(for point: Point2D) -> GridCellIndex {
        GridCellIndex(
            gx: Int(floor(point.x / cellSizeMeters)),
            gy: Int(floor(point.y / cellSizeMeters))
        )
    }

    public func centerPoint(for index: GridCellIndex) -> Point2D {
        Point2D(
            x: (Double(index.gx) + 0.5) * cellSizeMeters,
            y: (Double(index.gy) + 0.5) * cellSizeMeters
        )
    }

    /// Marks a circular footprint around the user's walked camera pose as free interior floor space
    public mutating func markWalkedFootprint(at position: Point2D, radiusMeters: Double = 1.0) {
        let cellRadius = Int(ceil(radiusMeters / cellSizeMeters))
        let centerIdx = index(for: position)

        for dx in -cellRadius...cellRadius {
            for dy in -cellRadius...cellRadius {
                let candidate = GridCellIndex(gx: centerIdx.gx + dx, gy: centerIdx.gy + dy)
                let candidatePt = centerPoint(for: candidate)
                if candidatePt.distance(to: position) <= radiusMeters {
                    updateCell(candidate, deltaLogOdds: -0.85)
                }
            }
        }
    }

    /// Updates occupancy using a vSLAM camera raycast from cameraPose to a detected feature/obstacle point
    public mutating func integrateRay(from cameraPose: Point2D, to hitPoint: Point2D, isObstacleHit: Bool) {
        let startIdx = index(for: cameraPose)
        let endIdx = index(for: hitPoint)

        // Bresenham's line algorithm to clear free space along the camera ray
        let rayIndices = Self.bresenhamLine(from: startIdx, to: endIdx)
        for (i, cellIdx) in rayIndices.enumerated() {
            if i == rayIndices.count - 1 && isObstacleHit {
                updateCell(cellIdx, deltaLogOdds: +1.15)
            } else {
                updateCell(cellIdx, deltaLogOdds: -0.45)
            }
        }
    }

    public mutating func updateCell(_ idx: GridCellIndex, deltaLogOdds: Double) {
        let current = logOdds[idx] ?? 0.0
        let updated = min(max(current + deltaLogOdds, -4.5), 4.5)
        logOdds[idx] = updated

        if updated >= 0.85 {
            cells[idx] = .wallObstacle
        } else if updated <= -0.35 {
            cells[idx] = .freeSpace
        }
    }

    public static func bresenhamLine(from start: GridCellIndex, to end: GridCellIndex) -> [GridCellIndex] {
        var result: [GridCellIndex] = []
        var x0 = start.gx
        var y0 = start.gy
        let x1 = end.gx
        let y1 = end.gy

        let dx = abs(x1 - x0)
        let dy = -abs(y1 - y0)
        let sx = x0 < x1 ? 1 : -1
        let sy = y0 < y1 ? 1 : -1
        var err = dx + dy

        var maxSteps = 250
        while maxSteps > 0 {
            result.append(GridCellIndex(gx: x0, gy: y0))
            if x0 == x1 && y0 == y1 { break }
            let e2 = 2 * err
            if e2 >= dy {
                err += dy
                x0 += sx
            }
            if e2 <= dx {
                err += dx
                y0 += sy
            }
            maxSteps -= 1
        }
        return result
    }

    /// Bounding box in meters of all discovered cells
    public var boundsInMeters: (min: Point2D, max: Point2D)? {
        guard !cells.isEmpty else { return nil }
        var minX = Int.max, minY = Int.max
        var maxX = Int.min, maxY = Int.min
        for idx in cells.keys {
            minX = min(minX, idx.gx)
            minY = min(minY, idx.gy)
            maxX = max(maxX, idx.gx)
            maxY = max(maxY, idx.gy)
        }
        return (
            min: Point2D(x: Double(minX) * cellSizeMeters, y: Double(minY) * cellSizeMeters),
            max: Point2D(x: Double(maxX + 1) * cellSizeMeters, y: Double(maxY + 1) * cellSizeMeters)
        )
    }

    /// Total mapped interior floor area in square meters
    public var mappedAreaSqMeters: Double {
        let freeCount = cells.values.filter { $0 == .freeSpace }.count
        return Double(freeCount) * cellSizeMeters * cellSizeMeters
    }
}

// MARK: - Router & Mesh Network Nodes

public enum NodeRole: String, Codable, CaseIterable, Identifiable, Sendable {
    case primaryRouter = "Primary Router"
    case meshNode = "Mesh Satellite / Extender"
    case recommendedNode = "Suggested Mesh Node"

    public var id: String { rawValue }
}

public enum WiFiBand: String, Codable, CaseIterable, Identifiable, Sendable {
    case band2_4GHz = "2.4 GHz"
    case band5GHz = "5 GHz"
    case band6GHz = "6 GHz (Wi-Fi 6E/7)"

    public var id: String { rawValue }

    /// Reference path loss at 1 meter (dB) using Free Space Path Loss formula
    public var referenceLoss1mDb: Double {
        switch self {
        case .band2_4GHz: return 40.0
        case .band5GHz: return 46.5
        case .band6GHz: return 48.0
        }
    }

    /// Path loss exponent indoors
    public var indoorPathLossExponent: Double {
        switch self {
        case .band2_4GHz: return 2.7
        case .band5GHz: return 3.1
        case .band6GHz: return 3.35
        }
    }
}

public enum BackhaulType: String, Codable, CaseIterable, Identifiable, Sendable {
    case wiredEthernet = "Wired Ethernet Backhaul"
    case wirelessMesh = "Wireless Mesh Backhaul"

    public var id: String { rawValue }
}

public struct NetworkNode: Identifiable, Codable, Hashable, Sendable {
    public var id: UUID
    public var name: String
    public var role: NodeRole
    public var position: Point2D
    public var heightMeters: Double
    public var band: WiFiBand
    public var txPowerDbm: Double // Typical consumer router: 20 - 23 dBm EIRP
    public var backhaul: BackhaulType

    public init(
        id: UUID = UUID(),
        name: String,
        role: NodeRole,
        position: Point2D,
        heightMeters: Double = 1.1,
        band: WiFiBand = .band5GHz,
        txPowerDbm: Double = 21.0,
        backhaul: BackhaulType = .wirelessMesh
    ) {
        self.id = id
        self.name = name
        self.role = role
        self.position = position
        self.heightMeters = heightMeters
        self.band = band
        self.txPowerDbm = txPowerDbm
        self.backhaul = backhaul
    }
}

// MARK: - Wi-Fi Signal & Throughput Measurement Sample

public enum SignalQualityTier: String, Codable, Sendable {
    case excellent = "Excellent" // >= -55 dBm
    case good = "Good"           // -56 to -67 dBm
    case fair = "Fair"           // -68 to -75 dBm
    case weak = "Weak / Dead Zone" // < -75 dBm
}

public struct WiFiSample: Identifiable, Codable, Hashable, Sendable {
    public var id: UUID
    public var timestamp: Date
    public var position: Point2D
    public var gpsCoordinate: GPSCoordinate?
    /// Measured or derived RSSI in dBm (-30 dBm strongest to -95 dBm dead zone)
    public var rssiDbm: Int
    /// True if estimated from throughput/latency/jitter rather than direct OS/Shortcut RSSI reading
    public var isEstimatedRssi: Bool
    /// Measured HTTP/TCP Download Throughput in Mbps
    public var downloadMbps: Double
    /// Measured HTTP/TCP Upload Throughput in Mbps
    public var uploadMbps: Double
    /// Gateway / Ping RTT latency in milliseconds
    public var latencyMs: Double
    /// Ping jitter (standard deviation of RTT) in milliseconds
    public var jitterMs: Double
    /// Packet loss percentage (0 - 100)
    public var packetLossPercent: Double
    /// Connected Wi-Fi SSID if available
    public var ssid: String?
    /// Connected AP BSSID (MAC) if available to identify roaming between mesh nodes
    public var bssid: String?

    public init(
        id: UUID = UUID(),
        timestamp: Date = Date(),
        position: Point2D,
        gpsCoordinate: GPSCoordinate? = nil,
        rssiDbm: Int,
        isEstimatedRssi: Bool = false,
        downloadMbps: Double,
        uploadMbps: Double = 0,
        latencyMs: Double,
        jitterMs: Double = 1.5,
        packetLossPercent: Double = 0.0,
        ssid: String? = nil,
        bssid: String? = nil
    ) {
        self.id = id
        self.timestamp = timestamp
        self.position = position
        self.gpsCoordinate = gpsCoordinate
        self.rssiDbm = rssiDbm
        self.isEstimatedRssi = isEstimatedRssi
        self.downloadMbps = downloadMbps
        self.uploadMbps = uploadMbps
        self.latencyMs = latencyMs
        self.jitterMs = jitterMs
        self.packetLossPercent = packetLossPercent
        self.ssid = ssid
        self.bssid = bssid
    }

    /// Normalized 0.0 (worst: -90 dBm) to 1.0 (best: -38 dBm) signal score
    public var normalizedSignalScore: Double {
        let clamped = Double(min(max(rssiDbm, -90), -38))
        return (clamped - (-90.0)) / (-38.0 - (-90.0))
    }

    public var qualityTier: SignalQualityTier {
        switch rssiDbm {
        case -55...0: return .excellent
        case -67...(-56): return .good
        case -75...(-68): return .fair
        default: return .weak
        }
    }
}

// MARK: - Placement Optimization Recommendation

public struct PlacementRecommendation: Identifiable, Codable, Hashable, Sendable {
    public var id: UUID
    public var title: String
    public var summary: String
    public var recommendedNodes: [NetworkNode]
    /// Percentage of mapped floor cells with >= -67 dBm (good/excellent coverage) before optimization
    public var currentGoodCoveragePercent: Double
    /// Percentage of mapped floor cells with >= -67 dBm after applying recommendation
    public var predictedGoodCoveragePercent: Double
    /// Percentage of mapped floor cells in dead zones (< -75 dBm) before optimization
    public var currentDeadZonePercent: Double
    /// Percentage of mapped floor cells in dead zones (< -75 dBm) after optimization
    public var predictedDeadZonePercent: Double
    /// Estimated average mesh wireless backhaul signal between primary router and mesh satellites (dBm)
    public var meshBackhaulRssiDbm: Int?
    public var actionSteps: [String]

    public init(
        id: UUID = UUID(),
        title: String,
        summary: String,
        recommendedNodes: [NetworkNode],
        currentGoodCoveragePercent: Double,
        predictedGoodCoveragePercent: Double,
        currentDeadZonePercent: Double,
        predictedDeadZonePercent: Double,
        meshBackhaulRssiDbm: Int? = nil,
        actionSteps: [String]
    ) {
        self.id = id
        self.title = title
        self.summary = summary
        self.recommendedNodes = recommendedNodes
        self.currentGoodCoveragePercent = currentGoodCoveragePercent
        self.predictedGoodCoveragePercent = predictedGoodCoveragePercent
        self.currentDeadZonePercent = currentDeadZonePercent
        self.predictedDeadZonePercent = predictedDeadZonePercent
        self.meshBackhaulRssiDbm = meshBackhaulRssiDbm
        self.actionSteps = actionSteps
    }
}

// MARK: - Complete Home Survey Project

public struct RoomLabel: Identifiable, Codable, Hashable, Sendable {
    public var id: UUID
    public var name: String
    public var position: Point2D

    public init(id: UUID = UUID(), name: String, position: Point2D) {
        self.id = id
        self.name = name
        self.position = position
    }
}

public struct SurveyProject: Identifiable, Codable, Sendable {
    public var id: UUID
    public var name: String
    public var createdAt: Date
    public var updatedAt: Date
    public var originGPS: GPSCoordinate?
    public var occupancyGrid: OccupancyGrid
    public var walls: [WallSegment]
    public var walkedTrajectory: [Point2D]
    public var samples: [WiFiSample]
    public var nodes: [NetworkNode]
    public var roomLabels: [RoomLabel]
    public var recommendations: [PlacementRecommendation]

    public init(
        id: UUID = UUID(),
        name: String = "My Home Wi-Fi Map",
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        originGPS: GPSCoordinate? = nil,
        occupancyGrid: OccupancyGrid = OccupancyGrid(),
        walls: [WallSegment] = [],
        walkedTrajectory: [Point2D] = [],
        samples: [WiFiSample] = [],
        nodes: [NetworkNode] = [],
        roomLabels: [RoomLabel] = [],
        recommendations: [PlacementRecommendation] = []
    ) {
        self.id = id
        self.name = name
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.originGPS = originGPS
        self.occupancyGrid = occupancyGrid
        self.walls = walls
        self.walkedTrajectory = walkedTrajectory
        self.samples = samples
        self.nodes = nodes
        self.roomLabels = roomLabels
        self.recommendations = recommendations
    }
}
