import SwiftUI

@main
public struct WiFiMapperApp: App {
    @StateObject private var wifiProbe: WiFiProbeService
    @StateObject private var coordinator: HouseMappingCoordinator

    public init() {
        let probe = WiFiProbeService()
        _wifiProbe = StateObject(wrappedValue: probe)
        _coordinator = StateObject(wrappedValue: HouseMappingCoordinator(wifiProbe: probe))
    }

    public var body: some Scene {
        WindowGroup {
            ContentView(coordinator: coordinator)
                .preferredColorScheme(.dark)
                .onOpenURL { url in
                    // Handles incoming iOS Shortcut deep links: wifimapper://rssi?dbm=-54&ssid=MyHomeWiFi
                    coordinator.wifiProbe.handleIncomingURL(url)
                }
        }
    }
}
