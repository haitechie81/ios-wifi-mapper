import Foundation
import Combine
#if canImport(Network)
import Network
#endif
#if canImport(NetworkExtension)
import NetworkExtension
#endif

@MainActor
public final class WiFiProbeService: ObservableObject {

    // MARK: - Published Telemetry State

    @Published public var currentRssiDbm: Int = -58
    @Published public var isEstimatedRssi: Bool = true
    @Published public var currentDownloadMbps: Double = 320.0
    @Published public var currentUploadMbps: Double = 140.0
    @Published public var currentLatencyMs: Double = 6.5
    @Published public var currentJitterMs: Double = 1.4
    @Published public var currentPacketLossPercent: Double = 0.0
    @Published public var currentSSID: String? = nil
    @Published public var currentBSSID: String? = nil
    @Published public var localIPAddress: String? = nil
    @Published public var gatewayIPAddress: String = "192.168.1.1"
    @Published public var companionServerURL: String = "" // e.g., "http://192.168.1.50:8765"
    @Published public var isCompanionConnected: Bool = false
    @Published public var isProbing: Bool = false
    @Published public var lastBridgeUpdateTimestamp: Date? = nil

    private var probeTask: Task<Void, Never>?
    private var rttHistory: [Double] = []
    private let session: URLSession

    public init() {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3.0
        config.timeoutIntervalForResource = 5.0
        config.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        self.session = URLSession(configuration: config)
        refreshLocalNetworkAddresses()
    }

    // MARK: - Continuous Probing Lifecycle

    public func startContinuousProbing(intervalSeconds: Double = 2.0) {
        guard probeTask == nil else { return }
        isProbing = true
        refreshLocalNetworkAddresses()

        probeTask = Task { [weak self] in
            guard let self else { return }
            var cycle = 0
            while !Task.isCancelled {
                await self.performSingleProbe(runThroughputBurst: cycle % 2 == 0)
                cycle += 1
                try? await Task.sleep(nanoseconds: UInt64(intervalSeconds * 1_000_000_000))
            }
        }
    }

    public func stopContinuousProbing() {
        probeTask?.cancel()
        probeTask = nil
        isProbing = false
    }

    // MARK: - Create Snapshot Sample at Current AR/GPS Pose

    public func captureSample(at position: Point2D, gps: GPSCoordinate?) -> WiFiSample {
        WiFiSample(
            position: position,
            gpsCoordinate: gps,
            rssiDbm: currentRssiDbm,
            isEstimatedRssi: isEstimatedRssi,
            downloadMbps: currentDownloadMbps,
            uploadMbps: currentUploadMbps,
            latencyMs: currentLatencyMs,
            jitterMs: currentJitterMs,
            packetLossPercent: currentPacketLossPercent,
            ssid: currentSSID,
            bssid: currentBSSID
        )
    }

    // MARK: - iOS Shortcut / Deep Link RSSI Bridge ("wifimapper://rssi?dbm=-54&ssid=MyWiFi&bssid=aa:bb:cc")

    public func handleIncomingURL(_ url: URL) {
        guard url.scheme?.lowercased() == "wifimapper",
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return
        }

        let queryItems = components.queryItems ?? []
        if let dbmString = queryItems.first(where: { $0.name == "dbm" || $0.name == "rssi" })?.value,
           let dbm = Int(dbmString), (-100...(-20)).contains(dbm) {
            self.currentRssiDbm = dbm
            self.isEstimatedRssi = false
            self.lastBridgeUpdateTimestamp = Date()
        }
        if let ssid = queryItems.first(where: { $0.name == "ssid" })?.value, !ssid.isEmpty {
            self.currentSSID = ssid
        }
        if let bssid = queryItems.first(where: { $0.name == "bssid" })?.value, !bssid.isEmpty {
            self.currentBSSID = bssid
        }
        if let server = queryItems.first(where: { $0.name == "server" })?.value, !server.isEmpty {
            self.companionServerURL = server
        }
    }

    /// Parses JSON or plain-text RSSI copied from an iOS Shortcut to clipboard (e.g. `{"rssi":-57,"ssid":"HomeNet"}` or `-57`)
    public func ingestShortcutPayload(_ text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if let directInt = Int(trimmed), (-100...(-20)).contains(directInt) {
            currentRssiDbm = directInt
            isEstimatedRssi = false
            lastBridgeUpdateTimestamp = Date()
            return true
        }

        guard let data = trimmed.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return false
        }

        if let rssi = json["rssi"] as? Int ?? (json["rssi"] as? String).flatMap(Int.init),
           (-100...(-20)).contains(rssi) {
            currentRssiDbm = rssi
            isEstimatedRssi = false
            lastBridgeUpdateTimestamp = Date()
        }
        if let ssid = json["ssid"] as? String, !ssid.isEmpty {
            currentSSID = ssid
        }
        if let bssid = json["bssid"] as? String, !bssid.isEmpty {
            currentBSSID = bssid
        }
        return true
    }

    // MARK: - Single Active Probe Execution

    public func performSingleProbe(runThroughputBurst: Bool = true) async {
        // 1. Query NEHotspotNetwork if available on iOS
        await fetchHotspotNetworkInfoIfAvailable()

        // 2. Measure RTT Latency & Jitter to Companion Server or Local Gateway
        let pingResult = await measureLatencyAndJitter()
        currentLatencyMs = pingResult.latencyMs
        currentJitterMs = pingResult.jitterMs
        currentPacketLossPercent = pingResult.packetLossPercent

        // 3. Measure Active Download/Upload Throughput (Mbps)
        if runThroughputBurst {
            let speeds = await measureActiveThroughput()
            if speeds.downloadMbps > 0 {
                currentDownloadMbps = speeds.downloadMbps
            }
            if speeds.uploadMbps > 0 {
                currentUploadMbps = speeds.uploadMbps
            }
        }

        // 4. If no recent hardware RSSI reading from Shortcut or NEHotspotNetwork within 20s, estimate RSSI from link telemetry
        let hasFreshBridgeRssi = lastBridgeUpdateTimestamp.map { Date().timeIntervalSince($0) < 20.0 } ?? false
        if !hasFreshBridgeRssi {
            isEstimatedRssi = true
            currentRssiDbm = Self.estimateRssiFromLinkTelemetry(
                downloadMbps: currentDownloadMbps,
                latencyMs: currentLatencyMs,
                jitterMs: currentJitterMs,
                packetLossPercent: currentPacketLossPercent
            )
        }
    }

    // MARK: - Latency & Jitter Measurement

    private func measureLatencyAndJitter() async -> (latencyMs: Double, jitterMs: Double, packetLossPercent: Double) {
        let probeCount = 3
        var samples: [Double] = []
        var failures = 0

        for _ in 0..<probeCount {
            let start = DispatchTime.now().uptimeNanoseconds
            let success: Bool

            if let companionPingURL = URL(string: "\(companionServerURL.trimmingCharacters(in: .whitespacesAndNewlines))/api/ping"),
               !companionServerURL.isEmpty {
                do {
                    let (_, response) = try await session.data(from: companionPingURL)
                    success = (response as? HTTPURLResponse)?.statusCode == 200
                    isCompanionConnected = success
                } catch {
                    success = false
                    isCompanionConnected = false
                }
            } else {
                // Probe local gateway port 80/443 or Cloudflare 1.1.1.1 HTTP endpoint
                success = await probeTCPOrGatewayReachability()
            }

            let elapsedMs = Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000.0
            if success {
                samples.append(min(elapsedMs, 350.0))
            } else {
                failures += 1
            }
        }

        let lossPercent = (Double(failures) / Double(probeCount)) * 100.0
        guard !samples.isEmpty else {
            return (latencyMs: 95.0, jitterMs: 25.0, packetLossPercent: lossPercent)
        }

        let avgLatency = samples.reduce(0, +) / Double(samples.count)
        rttHistory.append(avgLatency)
        if rttHistory.count > 8 {
            rttHistory.removeFirst(rttHistory.count - 8)
        }

        let mean = rttHistory.reduce(0, +) / Double(rttHistory.count)
        let variance = rttHistory.map { ($0 - mean) * ($0 - mean) }.reduce(0, +) / Double(max(1, rttHistory.count))
        let jitter = max(0.4, variance.squareRoot())

        return (latencyMs: avgLatency, jitterMs: jitter, packetLossPercent: lossPercent)
    }

    private func probeTCPOrGatewayReachability() async -> Bool {
        // Probe local gateway HTTP root; even an HTTP 401/403/404 or TCP reset proves local Wi-Fi airlink RTT
        if let gatewayURL = URL(string: "http://\(gatewayIPAddress)/") {
            var request = URLRequest(url: gatewayURL)
            request.httpMethod = "HEAD"
            request.timeoutInterval = 1.2
            do {
                let (_, _) = try await session.data(for: request)
                return true
            } catch let error as URLError {
                // If gateway actively refused or returned bad server response, packets still traversed the Wi-Fi link!
                if error.code == .cannotConnectToHost || error.code == .badServerResponse || error.code == .userAuthenticationRequired {
                    return true
                }
            } catch {}
        }

        // Fallback to fast edge ping
        guard let fallbackURL = URL(string: "https://1.1.1.1/cdn-cgi/trace") else { return false }
        var req = URLRequest(url: fallbackURL)
        req.httpMethod = "HEAD"
        req.timeoutInterval = 1.8
        return (try? await session.data(for: req)) != nil
    }

    // MARK: - Active Download / Upload Throughput Burst

    private func measureActiveThroughput() async -> (downloadMbps: Double, uploadMbps: Double) {
        // 1. Prefer Companion Windows PC LAN Server if configured (measures pure local Wi-Fi speed without ISP cap)
        let trimmedServer = companionServerURL.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedServer.isEmpty,
           let downURL = URL(string: "\(trimmedServer)/api/download?bytes=4000000") {
            let start = DispatchTime.now().uptimeNanoseconds
            if let (data, response) = try? await session.data(from: downURL),
               (response as? HTTPURLResponse)?.statusCode == 200,
               !data.isEmpty {
                let seconds = max(0.005, Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000_000.0)
                let downMbps = (Double(data.count) * 8.0) / (seconds * 1_000_000.0)
                isCompanionConnected = true

                // Quick 1 MB upload burst to Companion PC Server
                var upMbps = downMbps * 0.65
                if let upURL = URL(string: "\(trimmedServer)/api/upload") {
                    var upReq = URLRequest(url: upURL)
                    upReq.httpMethod = "POST"
                    let payload = Data(repeating: 0x5A, count: 1_000_000)
                    let upStart = DispatchTime.now().uptimeNanoseconds
                    if let (_, upResp) = try? await session.upload(for: upReq, from: payload),
                       (upResp as? HTTPURLResponse)?.statusCode == 200 {
                        let upSeconds = max(0.005, Double(DispatchTime.now().uptimeNanoseconds - upStart) / 1_000_000_000.0)
                        upMbps = (Double(payload.count) * 8.0) / (upSeconds * 1_000_000.0)
                    }
                }
                return (downloadMbps: downMbps, uploadMbps: upMbps)
            }
        }

        // 2. Fallback to Cloudflare edge speed burst (1.5 MB non-cached slice)
        guard let cfDownURL = URL(string: "https://speed.cloudflare.com/__down?bytes=1500000") else {
            return (0, 0)
        }
        let start = DispatchTime.now().uptimeNanoseconds
        do {
            let (data, response) = try await session.data(from: cfDownURL)
            guard (response as? HTTPURLResponse)?.statusCode == 200, !data.isEmpty else {
                return (0, 0)
            }
            let seconds = max(0.01, Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000_000.0)
            let downMbps = (Double(data.count) * 8.0) / (seconds * 1_000_000.0)
            return (downloadMbps: downMbps, uploadMbps: downMbps * 0.45)
        } catch {
            return (0, 0)
        }
    }

    // MARK: - Sync Survey Project to Windows Companion Server

    public func syncProjectToCompanionServer(_ project: SurveyProject) async throws {
        let trimmed = companionServerURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let url = URL(string: "\(trimmed)/api/sync") else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let body = try encoder.encode(project)
        let (_, response) = try await session.upload(for: request, from: body)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        isCompanionConnected = true
    }

    // MARK: - Telemetry -> Estimated RSSI Synthesis

    public static func estimateRssiFromLinkTelemetry(
        downloadMbps: Double,
        latencyMs: Double,
        jitterMs: Double,
        packetLossPercent: Double
    ) -> Int {
        // Combine speed score (0..1) and latency/jitter stability score (0..1)
        let speedScore = min(max(downloadMbps / 550.0, 0.0), 1.0)
        let latencyPenalty = min(max((latencyMs - 4.0) / 80.0, 0.0), 1.0)
        let jitterPenalty = min(max((jitterMs - 1.0) / 30.0, 0.0), 1.0)
        let lossPenalty = min(max(packetLossPercent / 25.0, 0.0), 1.0)

        let stabilityScore = max(0.0, 1.0 - (0.45 * latencyPenalty + 0.35 * jitterPenalty + 0.20 * lossPenalty))
        let composite = 0.62 * speedScore + 0.38 * stabilityScore

        // Map [0.0, 1.0] -> [-86 dBm, -42 dBm]
        let estimated = -86.0 + composite * 44.0
        return Int( estimated.rounded() )
    }

    // MARK: - iOS NEHotspotNetwork & Interface IP Discovery

    private func fetchHotspotNetworkInfoIfAvailable() async {
        #if os(iOS) && canImport(NetworkExtension)
        if #available(iOS 14.0, *) {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                NEHotspotNetwork.fetchCurrent { [weak self] network in
                    Task { @MainActor in
                        if let network {
                            self?.currentSSID = network.ssid
                            self?.currentBSSID = network.bssid
                            // Note: signalStrength is non-zero when app has Hotspot Helper entitlement
                            if network.signalStrength > 0.01 {
                                let mappedDbm = Int(-90.0 + network.signalStrength * 50.0)
                                self?.currentRssiDbm = mappedDbm
                                self?.isEstimatedRssi = false
                                self?.lastBridgeUpdateTimestamp = Date()
                            }
                        }
                        continuation.resume()
                    }
                }
            }
        }
        #endif
    }

    public func refreshLocalNetworkAddresses() {
        var address: String?
        var ifaddr: UnsafeMutablePointer<ifaddrs>?

        if getifaddrs(&ifaddr) == 0 {
            var ptr = ifaddr
            while ptr != nil {
                defer { ptr = ptr?.pointee.ifa_next }
                guard let interface = ptr?.pointee else { continue }
                let addrFamily = interface.ifa_addr.pointee.sa_family
                if addrFamily == UInt8(AF_INET) {
                    let name = String(cString: interface.ifa_name)
                    if name == "en0" { // iOS Wi-Fi interface
                        var hostname = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                        getnameinfo(
                            interface.ifa_addr,
                            socklen_t(interface.ifa_addr.pointee.sa_len),
                            &hostname,
                            socklen_t(hostname.count),
                            nil,
                            socklen_t(0),
                            NI_NUMERICHOST
                        )
                        address = String(cString: hostname)
                    }
                }
            }
            freeifaddrs(ifaddr)
        }

        if let ip = address, !ip.isEmpty {
            self.localIPAddress = ip
            // Infer standard /24 gateway (.1)
            let parts = ip.split(separator: ".")
            if parts.count == 4 {
                self.gatewayIPAddress = "\(parts[0]).\(parts[1]).\(parts[2]).1"
            }
        }
    }
}
