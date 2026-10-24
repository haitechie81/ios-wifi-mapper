# WiFi Mapper for iOS (Camera vSLAM Floorplan + RF Heatmap + Mesh Optimizer)

An iOS app built with **SwiftUI, ARKit (Visual-Inertial SLAM), RoomPlan, and CoreLocation** that maps your house floorplan like a camera-based robot vacuum while measuring and mapping your home's **Wi-Fi signal strength (dBm), network throughput (Mbps), and latency/jitter (ms)**—and recommends optimal **Primary Router & Mesh Node placement**.

---

## Key Features

1. **Robot-Vacuum Style Indoor Mapping (Camera + IMU vSLAM + GPS Anchor)**
   - Works on **all iPhones** using `ARWorldTrackingConfiguration` (60 Hz Camera Optical Flow + Gyroscope/Accelerometer Visual-Inertial Odometry) and `CoreLocation` GPS/Compass North heading (`gravityAndHeading`).
   - Builds a real-time 20 cm **2D Occupancy Grid** ([`OccupancyGrid`](WiFiMapper/Models/FloorPlanModels.swift)) using Bresenham raycasting from the camera pose to 3D visual feature points (`rawFeaturePoints`), carving walked interior floor space and detecting wall obstacles.
   - Automatically extracts vector walls from vertical `ARPlaneAnchor`s and upgrades to **Apple `RoomPlan` (`RoomCaptureSession`)** on LiDAR-equipped iPhone Pro models ([`ARCameraMappingView.swift`](WiFiMapper/Views/ARCameraMappingView.swift)).
2. **Multi-Mode Wi-Fi Signal & Active Throughput Probing** ([`WiFiProbeService.swift`](WiFiMapper/Services/WiFiProbeService.swift))
   - **On-Device Active Probing**: Continuously measures local router gateway RTT latency (ms), jitter (ms), packet loss (%), and active HTTP/TCP download & upload throughput (Mbps).
   - **Companion Windows PC LAN Speed Server** ([`CompanionServer/server.py`](CompanionServer/server.py)): Measures pure local Wi-Fi speed (up to 1000+ Mbps) between your iPhone and your Windows PC over LAN without ISP bandwidth limits.
   - **iOS Shortcuts Hardware dBm RSSI Bridge**: Receives exact hardware RSSI (`dBm`), `SSID`, and `BSSID` via `wifimapper://rssi?dbm=-54&ssid=MyWiFi` or clipboard without requiring a paid Apple Hotspot Helper entitlement.
3. **Color-Coded Floorplan Heatmap** ([`FloorPlanHeatmapView.swift`](WiFiMapper/Views/FloorPlanHeatmapView.swift))
   - Wall-aware Inverse Distance Weighting (IDW) + Multi-Wall RF Physics interpolation (`Red → Orange → Yellow → Green → Cyan`).
   - Switch between **Signal (dBm)**, **Throughput (Mbps)**, **Latency (ms)**, and **Predicted Optimal** heatmaps.
4. **Multi-Wall RF Physics Router & Mesh Placement Optimizer** ([`RFPropagationAndOptimizer.swift`](WiFiMapper/Services/RFPropagationAndOptimizer.swift))
   - Uses the COST-231 / ITU-R P.1238 Multi-Wall indoor attenuation model (accounting for Drywall, Brick, Concrete, Glass, and Wood doorways) and wireless mesh backhaul link quality (`-52` to `-66 dBm` sweet spot).
   - Recommends both **Relocating Primary Router** and **Keeping Router at ISP Jack + Adding/Moving Mesh Nodes**, with 1-tap **Before vs. After Coverage Preview**.

---

## Project Structure

- [`WiFiMapper/Models/FloorPlanModels.swift`](WiFiMapper/Models/FloorPlanModels.swift) — 2D coordinate system, `OccupancyGrid` (Bresenham raycasting & log-odds occupancy), `WallSegment`, `NetworkNode`, `WiFiSample`, `PlacementRecommendation`, and `SurveyProject`.
- [`WiFiMapper/Services/RFPropagationAndOptimizer.swift`](WiFiMapper/Services/RFPropagationAndOptimizer.swift) — Multi-Wall RF path loss engine, wall-aware IDW heatmap generator, and router/mesh placement optimizer.
- [`WiFiMapper/Services/HouseMappingCoordinator.swift`](WiFiMapper/Services/HouseMappingCoordinator.swift) — ARKit `ARSessionDelegate` vSLAM feature point & vertical plane extractor, CoreLocation GPS/heading delegate, and auto-sampling state machine.
- [`WiFiMapper/Services/WiFiProbeService.swift`](WiFiMapper/Services/WiFiProbeService.swift) — Gateway ping/jitter probe, HTTP/TCP throughput tester, `NEHotspotNetwork` reader, and `wifimapper://` URL scheme bridge.
- [`WiFiMapper/Views/ContentView.swift`](WiFiMapper/Views/ContentView.swift) — Split-screen AR Camera + 2D Robot-Vacuum Map workspace and live telemetry bar.
- [`WiFiMapper/Views/FloorPlanHeatmapView.swift`](WiFiMapper/Views/FloorPlanHeatmapView.swift) — Interactive 2D Canvas floorplan & heatmap renderer.
- [`WiFiMapper/Views/ARCameraMappingView.swift`](WiFiMapper/Views/ARCameraMappingView.swift) — Live ARKit camera view + optional LiDAR `RoomPlan` scanner sheet.
- [`WiFiMapper/Views/PlacementAdvisorSheet.swift`](WiFiMapper/Views/PlacementAdvisorSheet.swift) — Router/mesh manager, placement optimizer advisor, and companion server settings.
- [`CompanionServer/server.py`](CompanionServer/server.py) — Zero-dependency Python LAN speed test server & live web heatmap visualizer for Windows.
- [`.github/workflows/ios-build.yml`](.github/workflows/ios-build.yml) — GitHub Actions workflow that runs unit tests and compiles an unsigned `WiFiMapper-unsigned.ipa` on macOS runners.

---

## Running on Windows + Installing on Your iPhone

### 1. Start the Companion Windows LAN Speed Server & Web Visualizer
Run the included Python server on your Windows PC:
```powershell
python CompanionServer/server.py --port 8765
```
- Open `http://localhost:8765` on your PC or `http://<YOUR_PC_LAN_IP>:8765` in Safari on your iPhone.
- In the iOS app's **Network & Optimizer** sheet, enter `http://<YOUR_PC_LAN_IP>:8765` to measure pure local Wi-Fi throughput and sync your floorplan map to your PC.

### 2. Build & Sideload the Native iOS App onto Your iPhone from Windows
1. Push this repository to GitHub.
2. Open the **Actions** tab on GitHub and run the **Build iOS WiFiMapper IPA & Run Tests** workflow ([`.github/workflows/ios-build.yml`](.github/workflows/ios-build.yml)).
3. Download the `WiFiMapper-iOS-IPA` artifact (`WiFiMapper-unsigned.ipa`).
4. Install it onto your iPhone from Windows using **Sideloadly** or **AltStore / SideStore** with your free Apple ID (or open `project.yml` / `WiFiMapper.xcodeproj` in Xcode on a Mac).
