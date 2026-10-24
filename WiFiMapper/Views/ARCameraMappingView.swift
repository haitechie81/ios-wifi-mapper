import SwiftUI
#if os(iOS) && canImport(ARKit) && canImport(RealityKit)
import ARKit
import RealityKit
#endif
#if os(iOS) && canImport(RoomPlan)
import RoomPlan
#endif

public struct ARCameraMappingView: View {
    @ObservedObject var coordinator: HouseMappingCoordinator
    @State private var showLiDARRoomPlanSheet: Bool = false

    public init(coordinator: HouseMappingCoordinator) {
        self.coordinator = coordinator
    }

    public var body: some View {
        ZStack(alignment: .top) {
            #if os(iOS) && canImport(ARKit)
            ARKitContainerRepresentable(coordinator: coordinator)
                .ignoresSafeArea()
            #else
            // Fallback preview when running on macOS / non-AR simulator
            ZStack {
                Color.black
                VStack(spacing: 12) {
                    Image(systemName: "camera.viewfinder")
                        .font(.system(size: 46))
                        .foregroundColor(.cyan)
                    Text("Camera vSLAM + IMU Active on iPhone Hardware")
                        .font(.headline)
                        .foregroundColor(.white)
                    Text("Uses ARWorldTrackingConfiguration (Camera Optical Flow + Gyro/Accelerometer + GPS Heading) to build a 2D occupancy map like a robot vacuum.")
                        .font(.caption)
                        .foregroundColor(.white.opacity(0.75))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 28)
                }
            }
            #endif

            // Top HUD Overlay showing live vSLAM status, coordinates, and quick actions
            VStack(spacing: 10) {
                HStack(spacing: 10) {
                    Circle()
                        .fill(coordinator.isMappingActive ? Color.green : Color.orange)
                        .frame(width: 9, height: 9)
                    Text(coordinator.trackingStatusMessage)
                        .font(.system(size: 12, weight: .semibold, design: .rounded))
                        .foregroundColor(.white)
                        .lineLimit(1)
                    Spacer()
                    Text(String(format: "X: %.1fm  Y: %.1fm", coordinator.currentPosition.x, coordinator.currentPosition.y))
                        .font(.system(size: 11, weight: .bold, design: .monospaced))
                        .foregroundColor(.cyan)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(Color.black.opacity(0.72))
                .clipShape(Capsule())

                // Quick In-Camera Action Bar (Drop Router Here, Mark Wall Corner, Sample Wi-Fi, LiDAR RoomPlan)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        Button {
                            coordinator.recordWiFiSampleAtCurrentPosition()
                        } label: {
                            Label("Sample Wi-Fi Here", systemImage: "waveform.badge.plus")
                        }
                        .buttonStyle(HUDActionPillStyle(tint: .green))

                        Button {
                            coordinator.placeNetworkNode(
                                name: coordinator.project.nodes.isEmpty ? "Primary Router" : "Mesh Node #\(coordinator.project.nodes.count)",
                                role: coordinator.project.nodes.isEmpty ? .primaryRouter : .meshNode
                            )
                        } label: {
                            Label(
                                coordinator.project.nodes.isEmpty ? "Drop Router Here" : "Drop Mesh Here",
                                systemImage: "wifi.router.fill"
                            )
                        }
                        .buttonStyle(HUDActionPillStyle(tint: .blue))

                        Button {
                            coordinator.markWallCorner()
                        } label: {
                            Label(
                                coordinator.pendingWallStartPoint == nil ? "Pin Wall Start" : "Pin Wall End",
                                systemImage: "square.split.bottomrightquarter"
                            )
                        }
                        .buttonStyle(HUDActionPillStyle(tint: coordinator.pendingWallStartPoint == nil ? .orange : .yellow))

                        if coordinator.isLiDARRoomPlanSupported {
                            Button {
                                showLiDARRoomPlanSheet = true
                            } label: {
                                Label("LiDAR RoomScan", systemImage: "cube.transparent.fill")
                            }
                            .buttonStyle(HUDActionPillStyle(tint: .purple))
                        }
                    }
                    .padding(.horizontal, 4)
                }
            }
            .padding(12)
        }
        #if os(iOS) && canImport(RoomPlan)
        .sheet(isPresented: $showLiDARRoomPlanSheet) {
            if #available(iOS 16.0, *) {
                RoomPlanScannerSheet(coordinator: coordinator)
            }
        }
        #endif
    }
}

private struct HUDActionPillStyle: ButtonStyle {
    let tint: Color

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .bold, design: .rounded))
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(tint.opacity(configuration.isPressed ? 0.55 : 0.88))
            .foregroundColor(.white)
            .clipShape(Capsule())
    }
}

// MARK: - UIKit ARSCNView Wrapper for Camera + IMU vSLAM Mapping (Works on ALL iPhones)

#if os(iOS) && canImport(ARKit)
struct ARKitContainerRepresentable: UIViewRepresentable {
    @ObservedObject var coordinator: HouseMappingCoordinator

    func makeUIView(context: Context) -> ARSCNView {
        let sceneView = ARSCNView(frame: .zero)
        sceneView.session.delegate = coordinator
        sceneView.automaticallyUpdatesLighting = true
        // Show tracked vSLAM feature keypoints & world origin so user sees the camera mapping walls/floors in real time
        sceneView.debugOptions = [.showFeaturePoints]

        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal, .vertical]
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
        }
        // Align coordinate system to gravity + compass North when available
        config.worldAlignment = .gravityAndHeading
        sceneView.session.run(config, options: [])
        return sceneView
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {}

    static func dismantleUIView(_ uiView: ARSCNView, coordinator: ()) {
        uiView.session.pause()
    }
}
#endif

// MARK: - Optional Apple RoomPlan Scanner Sheet for LiDAR iPhones (iOS 16+)

#if os(iOS) && canImport(RoomPlan)
@available(iOS 16.0, *)
struct RoomPlanScannerSheet: View {
    @ObservedObject var coordinator: HouseMappingCoordinator
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            RoomCaptureViewRepresentable(coordinator: coordinator)
                .ignoresSafeArea()
                .navigationTitle("LiDAR RoomPlan Scanner")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") {
                            dismiss()
                        }
                        .bold()
                    }
                }
        }
    }
}

@available(iOS 16.0, *)
struct RoomCaptureViewRepresentable: UIViewRepresentable {
    @ObservedObject var coordinator: HouseMappingCoordinator

    func makeCoordinator() -> RoomCaptureSessionDelegateHandler {
        RoomCaptureSessionDelegateHandler(mappingCoordinator: coordinator)
    }

    func makeUIView(context: Context) -> RoomCaptureView {
        let captureView = RoomCaptureView(frame: .zero)
        captureView.captureSession.delegate = context.coordinator
        let config = RoomCaptureSession.Configuration()
        captureView.captureSession.run(configuration: config)
        return captureView
    }

    func updateUIView(_ uiView: RoomCaptureView, context: Context) {}

    static func dismantleUIView(_ uiView: RoomCaptureView, coordinator: RoomCaptureSessionDelegateHandler) {
        uiView.captureSession.stop()
    }

    final class RoomCaptureSessionDelegateHandler: NSObject, RoomCaptureSessionDelegate {
        let mappingCoordinator: HouseMappingCoordinator

        init(mappingCoordinator: HouseMappingCoordinator) {
            self.mappingCoordinator = mappingCoordinator
        }

        func captureSession(_ session: RoomCaptureSession, didUpdate room: CapturedRoom) {
            // Project 3D CapturedRoom walls & doors into 2D WallSegments
            var extractedWalls: [WallSegment] = []
            for surface in room.walls {
                let transform = surface.transform
                let halfWidth = surface.dimensions.x * 0.5
                let p0 = simd_mul(transform, SIMD4<Float>(-halfWidth, 0, 0, 1))
                let p1 = simd_mul(transform, SIMD4<Float>(halfWidth, 0, 0, 1))
                extractedWalls.append(
                    WallSegment(
                        id: surface.identifier,
                        start: Point2D(x: Double(p0.x), y: Double(p0.z)),
                        end: Point2D(x: Double(p1.x), y: Double(p1.z)),
                        material: .drywall,
                        isDoorway: false
                    )
                )
            }
            for door in room.doors {
                let transform = door.transform
                let halfWidth = door.dimensions.x * 0.5
                let p0 = simd_mul(transform, SIMD4<Float>(-halfWidth, 0, 0, 1))
                let p1 = simd_mul(transform, SIMD4<Float>(halfWidth, 0, 0, 1))
                extractedWalls.append(
                    WallSegment(
                        id: door.identifier,
                        start: Point2D(x: Double(p0.x), y: Double(p0.z)),
                        end: Point2D(x: Double(p1.x), y: Double(p1.z)),
                        material: .wood,
                        isDoorway: true
                    )
                )
            }

            let finalWalls = extractedWalls
            Task { @MainActor in
                self.mappingCoordinator.project.walls = finalWalls
                self.mappingCoordinator.recomputeRecommendations()
            }
        }
    }
}
#endif
