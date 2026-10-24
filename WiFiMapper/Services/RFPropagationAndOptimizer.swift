import Foundation

// MARK: - Heatmap Visualization Mode

public enum HeatmapMetric: String, CaseIterable, Identifiable, Codable, Sendable {
    case signalDbm = "Signal (dBm)"
    case throughputMbps = "Throughput (Mbps)"
    case latencyMs = "Latency (ms)"
    case predictedOptimization = "Predicted Optimal"

    public var id: String { rawValue }
}

// MARK: - Heatmap Cell Output

public struct HeatmapCell: Identifiable, Sendable {
    public let id: GridCellIndex
    public let center: Point2D
    public let rssiDbm: Double
    public let throughputMbps: Double
    public let latencyMs: Double
    /// Normalized 0.0 (Red / Dead Zone) -> 0.5 (Yellow) -> 1.0 (Green/Cyan Excellent)
    public let normalizedValue: Double
}

// MARK: - Multi-Wall RF Propagation & Heatmap Engine

public struct RFPropagationEngine: Sendable {

    /// Predicts RSSI (dBm) at a target point from a single NetworkNode using the COST-231 Multi-Wall indoor model.
    public static func predictRssi(
        from node: NetworkNode,
        to target: Point2D,
        walls: [WallSegment]
    ) -> Double {
        let horizontalDist = node.position.distance(to: target)
        let dz = abs(node.heightMeters - 1.1) // Assume phone held at ~1.1m height
        let dist3D = max(0.6, (horizontalDist * horizontalDist + dz * dz).squareRoot())

        let l0 = node.band.referenceLoss1mDb
        let n = node.band.indoorPathLossExponent
        let distanceLoss = 10.0 * n * log10(dist3D)

        // Sum wall attenuation along ray from node to target
        var wallLoss = 0.0
        var wallsCrossed = 0
        for wall in walls {
            if wall.intersectsSegment(from: node.position, to: target) {
                wallsCrossed += 1
                if wall.isDoorway {
                    wallLoss += 1.5
                } else {
                    let baseAtten = (node.band == .band2_4GHz)
                        ? wall.material.attenuationDb2_4GHz
                        : wall.material.attenuationDb5GHz
                    wallLoss += baseAtten
                }
            }
        }

        // Empirical diminishing return factor for multi-wall diffraction + indoor furniture/clutter shadowing
        let effectiveWallLoss = wallsCrossed > 0
            ? wallLoss * pow(Double(wallsCrossed), -0.05)
            : 0.0
        let indoorClutterMargin = (node.band == .band2_4GHz) ? 3.5 : 5.5

        let rawRssi = node.txPowerDbm - l0 - distanceLoss - indoorClutterMargin - effectiveWallLoss
        return min(max(rawRssi, -96.0), -28.0)
    }

    /// Predicts effective RSSI and estimated throughput (Mbps) at a point across a full network of router + mesh nodes.
    public static func evaluateNetworkAtPoint(
        point: Point2D,
        nodes: [NetworkNode],
        walls: [WallSegment],
        baselineMaxMbps: Double = 650.0
    ) -> (bestRssi: Double, estimatedMbps: Double, connectedNode: NetworkNode?) {
        guard !nodes.isEmpty else {
            return (-90.0, 0.0, nil)
        }

        let primaryRouter = nodes.first(where: { $0.role == .primaryRouter }) ?? nodes[0]

        var bestRssi = -96.0
        var bestMbps = 0.0
        var bestNode: NetworkNode?

        for node in nodes {
            let clientRssi = predictRssi(from: node, to: point, walls: walls)
            var nodeBackhaulEfficiency = 1.0

            if node.id != primaryRouter.id && node.backhaul == .wirelessMesh {
                // Evaluate wireless backhaul link between Primary Router and this Mesh Satellite
                let backhaulRssi = predictRssi(from: primaryRouter, to: node.position, walls: walls)
                if backhaulRssi >= -58.0 {
                    nodeBackhaulEfficiency = 0.85 // Dual/Tri-band strong mesh backhaul
                } else if backhaulRssi >= -67.0 {
                    nodeBackhaulEfficiency = 0.68
                } else if backhaulRssi >= -74.0 {
                    nodeBackhaulEfficiency = 0.38 // Weak backhaul bottleneck
                } else {
                    nodeBackhaulEfficiency = 0.12 // Disconnected / starving mesh node
                }
            }

            let clientLinkMbps = estimateThroughputFromRssi(
                rssiDbm: clientRssi,
                maxMbps: baselineMaxMbps
            ) * nodeBackhaulEfficiency

            // Roaming selection: client prefers strongest usable signal + throughput
            if clientRssi > bestRssi {
                bestRssi = clientRssi
                bestMbps = clientLinkMbps
                bestNode = node
            }
        }

        return (bestRssi, bestMbps, bestNode)
    }

    /// Converts RSSI (dBm) to realistic TCP/HTTP throughput (Mbps) using a calibrated MCS index sigmoid curve.
    public static func estimateThroughputFromRssi(rssiDbm: Double, maxMbps: Double = 650.0) -> Double {
        if rssiDbm <= -88.0 { return 0.5 }
        if rssiDbm >= -42.0 { return maxMbps }
        // Logistic curve centered at -63 dBm
        let k = 0.165
        let midpoint = -62.5
        let factor = 1.0 / (1.0 + exp(-k * (rssiDbm - midpoint)))
        return max(1.0, min(maxMbps, maxMbps * factor))
    }

    /// Wall-Aware Inverse Distance Weighting (IDW) interpolation combining real walked samples and RF physics model.
    public static func generateHeatmap(
        grid: OccupancyGrid,
        samples: [WiFiSample],
        nodes: [NetworkNode],
        walls: [WallSegment],
        metric: HeatmapMetric
    ) -> [HeatmapCell] {
        let freeCells = grid.cells.filter { $0.value == .freeSpace }.map(\.key)
        guard !freeCells.isEmpty else { return [] }

        let maxObservedMbps = max(300.0, samples.map(\.downloadMbps).max() ?? 500.0)

        return freeCells.map { cellIdx in
            let pt = grid.centerPoint(for: cellIdx)

            // If previewing predicted optimization or no samples exist yet, use the RF physics model
            if metric == .predictedOptimization || samples.isEmpty {
                let eval = evaluateNetworkAtPoint(
                    point: pt,
                    nodes: nodes,
                    walls: walls,
                    baselineMaxMbps: maxObservedMbps
                )
                let norm = normalizeRssi(eval.bestRssi)
                let estLatency = eval.bestRssi >= -67 ? 6.0 : (eval.bestRssi >= -75 ? 18.0 : 55.0)
                return HeatmapCell(
                    id: cellIdx,
                    center: pt,
                    rssiDbm: eval.bestRssi,
                    throughputMbps: eval.estimatedMbps,
                    latencyMs: estLatency,
                    normalizedValue: norm
                )
            }

            // Wall-aware IDW interpolation over walked WiFiSample measurements
            var weightSum = 0.0
            var rssiSum = 0.0
            var mbpsSum = 0.0
            var latencySum = 0.0
            var minSampleDist = Double.greatestFiniteMagnitude

            for sample in samples {
                let geomDist = pt.distance(to: sample.position)
                minSampleDist = min(minSampleDist, geomDist)

                if geomDist < 0.08 {
                    weightSum = 1e9
                    rssiSum = Double(sample.rssiDbm) * 1e9
                    mbpsSum = sample.downloadMbps * 1e9
                    latencySum = sample.latencyMs * 1e9
                    break
                }

                // Add virtual distance penalty if a wall separates the sample from this cell
                var wallPenaltyMeters = 0.0
                for wall in walls where !wall.isDoorway {
                    if wall.intersectsSegment(from: pt, to: sample.position) {
                        wallPenaltyMeters += 2.8
                    }
                }

                let effectiveDist = geomDist + wallPenaltyMeters
                let weight = 1.0 / pow(effectiveDist, 2.2)
                weightSum += weight
                rssiSum += Double(sample.rssiDbm) * weight
                mbpsSum += sample.downloadMbps * weight
                latencySum += sample.latencyMs * weight
            }

            var idwRssi = weightSum > 0 ? (rssiSum / weightSum) : -75.0
            var idwMbps = weightSum > 0 ? (mbpsSum / weightSum) : 50.0
            let idwLatency = weightSum > 0 ? (latencySum / weightSum) : 15.0

            // If cell is far (> 3.0m) from walked trajectory and router nodes are placed, blend with RF physics prior
            if minSampleDist > 3.0 && !nodes.isEmpty {
                let modelEval = evaluateNetworkAtPoint(
                    point: pt,
                    nodes: nodes,
                    walls: walls,
                    baselineMaxMbps: maxObservedMbps
                )
                let blend = min(0.7, (minSampleDist - 3.0) / 5.0)
                idwRssi = idwRssi * (1.0 - blend) + modelEval.bestRssi * blend
                idwMbps = idwMbps * (1.0 - blend) + modelEval.estimatedMbps * blend
            }

            let normalized: Double
            switch metric {
            case .signalDbm, .predictedOptimization:
                normalized = normalizeRssi(idwRssi)
            case .throughputMbps:
                normalized = min(max(idwMbps / maxObservedMbps, 0.0), 1.0)
            case .latencyMs:
                // Lower latency is better: 3ms -> 1.0 (green), 80ms+ -> 0.0 (red)
                normalized = 1.0 - min(max((idwLatency - 3.0) / 77.0, 0.0), 1.0)
            }

            return HeatmapCell(
                id: cellIdx,
                center: pt,
                rssiDbm: idwRssi,
                throughputMbps: idwMbps,
                latencyMs: idwLatency,
                normalizedValue: normalized
            )
        }
    }

    public static func normalizeRssi(_ rssi: Double) -> Double {
        let clamped = min(max(rssi, -88.0), -40.0)
        return (clamped - (-88.0)) / (-40.0 - (-88.0))
    }
}

// MARK: - Router & Mesh Node Placement Optimizer

public struct PlacementOptimizer: Sendable {

    /// Generates ranked router & mesh placement recommendations based on the mapped floorplan, walls, and signal samples.
    public static func generateRecommendations(
        grid: OccupancyGrid,
        walls: [WallSegment],
        samples: [WiFiSample],
        currentNodes: [NetworkNode]
    ) -> [PlacementRecommendation] {
        let freeIndices = grid.cells.filter { $0.value == .freeSpace }.map(\.key)
        let obstacleIndices = Set(grid.cells.filter { $0.value == .wallObstacle }.map(\.key))

        // Build evaluation points across the house (subsampled every ~0.6m for fast optimization)
        var evalPoints: [Point2D] = freeIndices.enumerated().compactMap { idx, cell in
            (idx % 3 == 0) ? grid.centerPoint(for: cell) : nil
        }

        // Fall back to walked trajectory points if occupancy grid is small
        if evalPoints.isEmpty {
            evalPoints = samples.map(\.position)
        }
        guard !evalPoints.isEmpty else { return [] }

        // Candidate node locations: interior free space cells at least 0.4m away from wall obstacles
        let candidatePositions: [Point2D] = evalPoints.filter { pt in
            let cellIdx = grid.index(for: pt)
            let adjacentObstacle = (-1...1).contains { dx in
                (-1...1).contains { dy in
                    obstacleIndices.contains(GridCellIndex(gx: cellIdx.gx + dx, gy: cellIdx.gy + dy))
                }
            }
            return !adjacentObstacle
        }
        let searchCandidates = candidatePositions.isEmpty ? evalPoints : candidatePositions

        // Baseline coverage stats from current measurements + current nodes
        let baselineStats = computeCoverageStats(
            evalPoints: evalPoints,
            nodes: currentNodes,
            samples: samples,
            walls: walls,
            useMeasuredSamples: !samples.isEmpty
        )

        var recommendations: [PlacementRecommendation] = []

        let existingPrimary = currentNodes.first(where: { $0.role == .primaryRouter })
            ?? currentNodes.first
            ?? NetworkNode(
                name: "Primary Router",
                role: .primaryRouter,
                position: evalPoints.first ?? Point2D(x: 0, y: 0)
            )

        // STRATEGY 1: Optimal Single Primary Router Placement (Relocate Primary Router to RF Centroid)
        var bestPrimaryPos = existingPrimary.position
        var bestPrimaryScore = -Double.infinity

        for candidate in searchCandidates {
            var testRouter = existingPrimary
            testRouter.position = candidate
            let score = scoreConfiguration(evalPoints: evalPoints, nodes: [testRouter], walls: walls)
            if score > bestPrimaryScore {
                bestPrimaryScore = score
                bestPrimaryPos = candidate
            }
        }

        var relocatedPrimary = existingPrimary
        relocatedPrimary.position = bestPrimaryPos
        var relocatedNodes = [relocatedPrimary]

        // If user also has existing mesh satellites, re-optimize their positions around the relocated primary
        let existingSatellites = currentNodes.filter { $0.id != existingPrimary.id }
        for sat in existingSatellites {
            let bestSatPos = findBestMeshSatellitePosition(
                primary: relocatedPrimary,
                existingNodes: relocatedNodes,
                candidates: searchCandidates,
                evalPoints: evalPoints,
                walls: walls
            )
            var updatedSat = sat
            updatedSat.position = bestSatPos
            relocatedNodes.append(updatedSat)
        }

        let relocatedStats = computeCoverageStats(
            evalPoints: evalPoints,
            nodes: relocatedNodes,
            samples: [],
            walls: walls,
            useMeasuredSamples: false
        )

        let moveDist = existingPrimary.position.distance(to: bestPrimaryPos)
        recommendations.append(
            PlacementRecommendation(
                title: existingSatellites.isEmpty
                    ? "Relocate Primary Router to Optimal Line-of-Sight Center"
                    : "Rebalance Primary Router + Mesh Nodes",
                summary: String(
                    format: "Moving '%@' %.1fm toward (%.1fm, %.1fm) minimizes multi-wall attenuation and raises good Wi-Fi coverage from %.0f%% to %.0f%%.",
                    existingPrimary.name,
                    moveDist,
                    bestPrimaryPos.x,
                    bestPrimaryPos.y,
                    baselineStats.goodCoveragePercent,
                    relocatedStats.goodCoveragePercent
                ),
                recommendedNodes: relocatedNodes,
                currentGoodCoveragePercent: baselineStats.goodCoveragePercent,
                predictedGoodCoveragePercent: relocatedStats.goodCoveragePercent,
                currentDeadZonePercent: baselineStats.deadZonePercent,
                predictedDeadZonePercent: relocatedStats.deadZonePercent,
                meshBackhaulRssiDbm: relocatedNodes.count > 1
                    ? Int(RFPropagationEngine.predictRssi(from: relocatedPrimary, to: relocatedNodes[1].position, walls: walls))
                    : nil,
                actionSteps: [
                    String(format: "Place '%@' at map coordinates (%.1f m, %.1f m) elevated ~1.2m off the floor.", existingPrimary.name, bestPrimaryPos.x, bestPrimaryPos.y),
                    "Keep the router at least 40 cm away from thick concrete/brick walls and metal appliances.",
                    existingSatellites.isEmpty
                        ? "Centralizing the router balances 5 GHz propagation across rooms without adding hardware."
                        : "Reposition mesh satellites to maintain a -56 to -65 dBm wireless backhaul link to the primary router."
                ]
            )
        )

        // STRATEGY 2: Keep Primary Router Fixed (ISP Wall Jack Fixed) + Add/Move Mesh Satellite for Dead Zones
        var fixedPrimaryNodes: [NetworkNode] = [existingPrimary]
        let satelliteCountToPlace = max(1, existingSatellites.count)

        for idx in 0..<satelliteCountToPlace {
            let bestSatPos = findBestMeshSatellitePosition(
                primary: existingPrimary,
                existingNodes: fixedPrimaryNodes,
                candidates: searchCandidates,
                evalPoints: evalPoints,
                walls: walls
            )
            let satNode = NetworkNode(
                name: idx < existingSatellites.count ? existingSatellites[idx].name : "Suggested Mesh Node #\(idx + 1)",
                role: idx < existingSatellites.count ? .meshNode : .recommendedNode,
                position: bestSatPos,
                heightMeters: 1.1,
                band: existingPrimary.band,
                txPowerDbm: existingPrimary.txPowerDbm,
                backhaul: .wirelessMesh
            )
            fixedPrimaryNodes.append(satNode)
        }

        let meshStats = computeCoverageStats(
            evalPoints: evalPoints,
            nodes: fixedPrimaryNodes,
            samples: [],
            walls: walls,
            useMeasuredSamples: false
        )

        if let firstSat = fixedPrimaryNodes.dropFirst().first {
            let backhaulRssi = Int(RFPropagationEngine.predictRssi(from: existingPrimary, to: firstSat.position, walls: walls))
            let isAddingNew = existingSatellites.isEmpty
            recommendations.append(
                PlacementRecommendation(
                    title: isAddingNew
                        ? "Keep Router at ISP Jack + Add 1 Mesh Node"
                        : "Keep Router at ISP Jack + Optimize Mesh Satellite",
                    summary: String(
                        format: "%@ at (%.1fm, %.1fm) with a strong %d dBm wireless backhaul link, cutting dead zones from %.0f%% down to %.0f%%.",
                        isAddingNew ? "Add a Mesh Satellite" : "Move your Mesh Satellite",
                        firstSat.position.x,
                        firstSat.position.y,
                        backhaulRssi,
                        baselineStats.deadZonePercent,
                        meshStats.deadZonePercent
                    ),
                    recommendedNodes: fixedPrimaryNodes,
                    currentGoodCoveragePercent: baselineStats.goodCoveragePercent,
                    predictedGoodCoveragePercent: meshStats.goodCoveragePercent,
                    currentDeadZonePercent: baselineStats.deadZonePercent,
                    predictedDeadZonePercent: meshStats.deadZonePercent,
                    meshBackhaulRssiDbm: backhaulRssi,
                    actionSteps: [
                        String(format: "Leave '%@' connected to your existing modem/ONT port at (%.1f m, %.1f m).", existingPrimary.name, existingPrimary.position.x, existingPrimary.position.y),
                        String(format: "Place '%@' at (%.1f m, %.1f m) — halfway toward the weakest room where backhaul signal from the main router is %d dBm.", firstSat.name, firstSat.position.x, firstSat.position.y, backhaulRssi),
                        "Avoid placing the mesh node directly inside the dead zone itself; it needs strong signal from the primary router to relay high throughput."
                    ]
                )
            )
        }

        return recommendations.sorted { $0.predictedGoodCoveragePercent > $1.predictedGoodCoveragePercent }
    }

    private static func findBestMeshSatellitePosition(
        primary: NetworkNode,
        existingNodes: [NetworkNode],
        candidates: [Point2D],
        evalPoints: [Point2D],
        walls: [WallSegment]
    ) -> Point2D {
        var bestPos = primary.position
        var bestScore = -Double.infinity

        for candidate in candidates {
            // Mesh node shouldn't be right on top of an existing node (< 2.0m)
            let tooClose = existingNodes.contains { $0.position.distance(to: candidate) < 2.0 }
            if tooClose { continue }

            // Check backhaul RSSI from primary router: sweet spot is -52 to -66 dBm
            let backhaulRssi = RFPropagationEngine.predictRssi(from: primary, to: candidate, walls: walls)
            var backhaulBonus = 0.0
            if (-66.0...(-50.0)).contains(backhaulRssi) {
                backhaulBonus = 18.0
            } else if backhaulRssi < -72.0 {
                backhaulBonus = -45.0 // Heavy penalty if satellite is starved of backhaul signal
            }

            let candidateNode = NetworkNode(
                name: "Candidate Mesh",
                role: .recommendedNode,
                position: candidate,
                band: primary.band,
                txPowerDbm: primary.txPowerDbm,
                backhaul: .wirelessMesh
            )
            let totalScore = scoreConfiguration(
                evalPoints: evalPoints,
                nodes: existingNodes + [candidateNode],
                walls: walls
            ) + backhaulBonus

            if totalScore > bestScore {
                bestScore = totalScore
                bestPos = candidate
            }
        }

        return bestPos
    }

    private static func scoreConfiguration(
        evalPoints: [Point2D],
        nodes: [NetworkNode],
        walls: [WallSegment]
    ) -> Double {
        var score = 0.0
        for pt in evalPoints {
            let eval = RFPropagationEngine.evaluateNetworkAtPoint(point: pt, nodes: nodes, walls: walls)
            // Reward good coverage (>= -67 dBm) and heavily penalize dead zones (< -75 dBm)
            if eval.bestRssi >= -60.0 {
                score += 1.2
            } else if eval.bestRssi >= -67.0 {
                score += 1.0
            } else if eval.bestRssi >= -74.0 {
                score += 0.3
            } else {
                score -= 1.5
            }
        }
        return score / Double(max(1, evalPoints.count)) * 100.0
    }

    private static func computeCoverageStats(
        evalPoints: [Point2D],
        nodes: [NetworkNode],
        samples: [WiFiSample],
        walls: [WallSegment],
        useMeasuredSamples: Bool
    ) -> (goodCoveragePercent: Double, deadZonePercent: Double) {
        if useMeasuredSamples && !samples.isEmpty {
            let good = samples.filter { $0.rssiDbm >= -67 }.count
            let dead = samples.filter { $0.rssiDbm < -75 }.count
            let total = Double(samples.count)
            return (
                goodCoveragePercent: (Double(good) / total) * 100.0,
                deadZonePercent: (Double(dead) / total) * 100.0
            )
        }

        guard !evalPoints.isEmpty, !nodes.isEmpty else {
            return (0.0, 100.0)
        }

        var goodCount = 0
        var deadCount = 0
        for pt in evalPoints {
            let eval = RFPropagationEngine.evaluateNetworkAtPoint(point: pt, nodes: nodes, walls: walls)
            if eval.bestRssi >= -67.0 {
                goodCount += 1
            } else if eval.bestRssi < -75.0 {
                deadCount += 1
            }
        }

        let total = Double(evalPoints.count)
        return (
            goodCoveragePercent: (Double(goodCount) / total) * 100.0,
            deadZonePercent: (Double(deadCount) / total) * 100.0
        )
    }
}
