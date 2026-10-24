import XCTest
@testable import WiFiMapperCore

final class RFPropagationAndOptimizerTests: XCTestCase {

    func testOccupancyGridBresenhamAndFootprint() {
        var grid = OccupancyGrid(cellSizeMeters: 0.25)
        let origin = Point2D(x: 0.0, y: 0.0)
        grid.markWalkedFootprint(at: origin, radiusMeters: 0.8)

        XCTAssertGreaterThan(grid.mappedAreaSqMeters, 1.0)
        XCTAssertEqual(grid.cells[grid.index(for: origin)], .freeSpace)

        let wallHit = Point2D(x: 2.0, y: 0.0)
        // Integrate multiple hits to exceed log-odds wall threshold
        grid.integrateRay(from: origin, to: wallHit, isObstacleHit: true)
        grid.integrateRay(from: origin, to: wallHit, isObstacleHit: true)
        XCTAssertEqual(grid.cells[grid.index(for: wallHit)], .wallObstacle)
    }

    func testMultiWallAttenuationReducesSignal() {
        let router = NetworkNode(
            name: "Main Router",
            role: .primaryRouter,
            position: Point2D(x: 0, y: 0),
            band: .band5GHz,
            txPowerDbm: 21.0
        )
        let target = Point2D(x: 5.0, y: 0.0)

        let freeSpaceRssi = RFPropagationEngine.predictRssi(from: router, to: target, walls: [])
        let brickWall = WallSegment(
            start: Point2D(x: 2.5, y: -3.0),
            end: Point2D(x: 2.5, y: 3.0),
            material: .brick
        )
        let obstructedRssi = RFPropagationEngine.predictRssi(from: router, to: target, walls: [brickWall])

        XCTAssertLessThan(obstructedRssi, freeSpaceRssi - 7.0)
    }

    func testPlacementOptimizerImprovesDeadZones() {
        var grid = OccupancyGrid(cellSizeMeters: 0.5)
        for gx in -10...10 {
            for gy in -6...6 {
                grid.updateCell(GridCellIndex(gx: gx, gy: gy), deltaLogOdds: -2.0)
            }
        }

        let partitionWall = WallSegment(
            start: Point2D(x: 0, y: -3),
            end: Point2D(x: 0, y: 3),
            material: .brick
        )
        let cornerRouter = NetworkNode(
            name: "Corner Router",
            role: .primaryRouter,
            position: Point2D(x: -4.5, y: 2.5)
        )

        let recommendations = PlacementOptimizer.generateRecommendations(
            grid: grid,
            walls: [partitionWall],
            samples: [],
            currentNodes: [cornerRouter]
        )

        XCTAssertFalse(recommendations.isEmpty)
        if let top = recommendations.first {
            XCTAssertGreaterThanOrEqual(top.predictedGoodCoveragePercent, top.currentGoodCoveragePercent)
            XCTAssertLessThanOrEqual(top.predictedDeadZonePercent, top.currentDeadZonePercent)
        }
    }
}
