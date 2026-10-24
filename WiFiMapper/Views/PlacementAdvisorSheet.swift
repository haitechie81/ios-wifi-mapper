import SwiftUI

public struct PlacementAdvisorSheet: View {
    @ObservedObject var coordinator: HouseMappingCoordinator
    @Environment(\.dismiss) private var dismiss

    @State private var newNodeName: String = "Mesh Node"
    @State private var newNodeRole: NodeRole = .meshNode
    @State private var newNodeBand: WiFiBand = .band5GHz
    @State private var newNodeBackhaul: BackhaulType = .wirelessMesh
    @State private var manualRssiInput: String = ""
    @State private var syncStatusMessage: String? = nil

    public init(coordinator: HouseMappingCoordinator) {
        self.coordinator = coordinator
    }

    public var body: some View {
        NavigationStack {
            List {
                // SECTION 1: AI & RF Physics Placement Recommendations
                Section {
                    if coordinator.project.recommendations.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Walk through at least 2 rooms or load the Demo House to generate optimal router & mesh placement suggestions.")
                                .font(.subheadline)
                                .foregroundColor(.secondary)
                            Button("Recompute Placement Analysis") {
                                coordinator.recomputeRecommendations()
                            }
                            .buttonStyle(.borderedProminent)
                        }
                        .padding(.vertical, 6)
                    } else {
                        ForEach(coordinator.project.recommendations) { rec in
                            VStack(alignment: .leading, spacing: 10) {
                                HStack {
                                    Image(systemName: "sparkles.tv.fill")
                                        .foregroundColor(.purple)
                                    Text(rec.title)
                                        .font(.headline)
                                }

                                Text(rec.summary)
                                    .font(.subheadline)
                                    .foregroundColor(.secondary)

                                // Coverage Before vs After Comparison Pills
                                HStack(spacing: 12) {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text("GOOD COVERAGE")
                                            .font(.system(size: 10, weight: .bold))
                                            .foregroundColor(.secondary)
                                        Text(String(format: "%.0f%% → %.0f%%", rec.currentGoodCoveragePercent, rec.predictedGoodCoveragePercent))
                                            .font(.system(size: 14, weight: .bold, design: .rounded))
                                            .foregroundColor(.green)
                                    }
                                    .padding(8)
                                    .background(Color.green.opacity(0.12))
                                    .clipShape(RoundedRectangle(cornerRadius: 8))

                                    VStack(alignment: .leading, spacing: 2) {
                                        Text("DEAD ZONES")
                                            .font(.system(size: 10, weight: .bold))
                                            .foregroundColor(.secondary)
                                        Text(String(format: "%.0f%% → %.0f%%", rec.currentDeadZonePercent, rec.predictedDeadZonePercent))
                                            .font(.system(size: 14, weight: .bold, design: .rounded))
                                            .foregroundColor(.orange)
                                    }
                                    .padding(8)
                                    .background(Color.orange.opacity(0.12))
                                    .clipShape(RoundedRectangle(cornerRadius: 8))

                                    if let backhaul = rec.meshBackhaulRssiDbm {
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text("MESH BACKHAUL")
                                                .font(.system(size: 10, weight: .bold))
                                                .foregroundColor(.secondary)
                                            Text("\(backhaul) dBm")
                                                .font(.system(size: 14, weight: .bold, design: .monospaced))
                                                .foregroundColor(.cyan)
                                        }
                                        .padding(8)
                                        .background(Color.cyan.opacity(0.12))
                                        .clipShape(RoundedRectangle(cornerRadius: 8))
                                    }
                                }

                                VStack(alignment: .leading, spacing: 4) {
                                    ForEach(rec.actionSteps, id: \.self) { step in
                                        HStack(alignment: .top, spacing: 6) {
                                            Image(systemName: "checkmark.circle.fill")
                                                .font(.caption)
                                                .foregroundColor(.green)
                                                .padding(.top, 2)
                                            Text(step)
                                                .font(.caption)
                                        }
                                    }
                                }

                                HStack(spacing: 10) {
                                    Button {
                                        coordinator.previewingRecommendation = rec
                                        coordinator.selectedHeatmapMetric = .predictedOptimization
                                        dismiss()
                                    } label: {
                                        Label("Preview on Map", systemImage: "eye.fill")
                                            .font(.caption.bold())
                                    }
                                    .buttonStyle(.borderedProminent)
                                    .tint(.purple)

                                    Button {
                                        coordinator.applyRecommendation(rec)
                                        dismiss()
                                    } label: {
                                        Label("Apply Nodes", systemImage: "arrow.triangle.2.circlepath")
                                            .font(.caption.bold())
                                    }
                                    .buttonStyle(.bordered)
                                }
                                .padding(.top, 4)
                            }
                            .padding(.vertical, 6)
                        }
                    }
                } header: {
                    Text("Router & Mesh Placement Optimizer")
                }

                // SECTION 2: Current Routers & Mesh Nodes
                Section {
                    ForEach(coordinator.project.nodes) { node in
                        HStack {
                            Image(systemName: node.role == .primaryRouter ? "wifi.router.fill" : "dot.radiowaves.left.and.right")
                                .foregroundColor(node.role == .primaryRouter ? .blue : .teal)
                                .font(.title3)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(node.name)
                                    .font(.subheadline.bold())
                                Text(String(format: "%@ • %@ • (%.1fm, %.1fm)", node.role.rawValue, node.band.rawValue, node.position.x, node.position.y))
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                            Spacer()
                            Button(role: .destructive) {
                                coordinator.removeNode(nodeId: node.id)
                            } label: {
                                Image(systemName: "trash")
                            }
                        }
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        Text("Add Router / Mesh Node at Current Position")
                            .font(.caption.bold())
                            .foregroundColor(.secondary)
                        TextField("Node Name (e.g., Hallway Mesh)", text: $newNodeName)
                            .textFieldStyle(.roundedBorder)
                        Picker("Role", selection: $newNodeRole) {
                            Text("Primary Router").tag(NodeRole.primaryRouter)
                            Text("Mesh Satellite").tag(NodeRole.meshNode)
                        }
                        .pickerStyle(.segmented)
                        Picker("Frequency Band", selection: $newNodeBand) {
                            ForEach(WiFiBand.allCases) { band in
                                Text(band.rawValue).tag(band)
                            }
                        }
                        if newNodeRole == .meshNode {
                            Picker("Backhaul", selection: $newNodeBackhaul) {
                                ForEach(BackhaulType.allCases) { bh in
                                    Text(bh.rawValue).tag(bh)
                                }
                            }
                        }
                        Button {
                            coordinator.placeNetworkNode(
                                name: newNodeName.isEmpty ? newNodeRole.rawValue : newNodeName,
                                role: newNodeRole,
                                band: newNodeBand,
                                backhaul: newNodeBackhaul
                            )
                        } label: {
                            Label("Add Node at Current Position", systemImage: "plus.circle.fill")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                    }
                    .padding(.vertical, 4)
                } header: {
                    Text("Installed Routers & Mesh Nodes (\(coordinator.project.nodes.count))")
                }

                // SECTION 3: Companion Windows PC LAN Speed Server & iOS Shortcut RSSI Bridge
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Companion Windows PC LAN Server (Optional)")
                            .font(.subheadline.bold())
                        Text("Run `python CompanionServer/server.py` on your Windows PC to measure pure local Wi-Fi throughput (up to 1000+ Mbps) without ISP bottlenecks and view the live map in your PC browser.")
                            .font(.caption)
                            .foregroundColor(.secondary)

                        TextField("http://192.168.1.50:8765", text: Binding(
                            get: { coordinator.wifiProbe.companionServerURL },
                            set: { coordinator.wifiProbe.companionServerURL = $0 }
                        ))
                        .textFieldStyle(.roundedBorder)
                        .autocorrectionDisabled()

                        HStack {
                            Button {
                                Task {
                                    do {
                                        try await coordinator.wifiProbe.syncProjectToCompanionServer(coordinator.project)
                                        syncStatusMessage = "Synced map & \(coordinator.project.samples.count) samples to Windows PC!"
                                    } catch {
                                        syncStatusMessage = "Could not connect to \(coordinator.wifiProbe.companionServerURL)"
                                    }
                                }
                            } label: {
                                Label("Sync Map to PC Server", systemImage: "arrow.triangle.2.circlepath.icloud")
                            }
                            .buttonStyle(.borderedProminent)

                            if let msg = syncStatusMessage {
                                Text(msg)
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                    .padding(.vertical, 4)

                    VStack(alignment: .leading, spacing: 8) {
                        Text("Exact Hardware dBm RSSI Bridge (iOS Shortcuts)")
                            .font(.subheadline.bold())
                        Text("An iOS Shortcut using 'Get Network Details → RSSI' can open `wifimapper://rssi?dbm=-54&ssid=MyWiFi` or copy the dBm value to clipboard.")
                            .font(.caption)
                            .foregroundColor(.secondary)

                        HStack {
                            TextField("Enter dBm (e.g. -56)", text: $manualRssiInput)
                                .textFieldStyle(.roundedBorder)
                            Button("Set dBm") {
                                _ = coordinator.wifiProbe.ingestShortcutPayload(manualRssiInput)
                                manualRssiInput = ""
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                    .padding(.vertical, 4)
                } header: {
                    Text("LAN Speed Server & Hardware RSSI Bridge")
                }
            }
            .navigationTitle("Network & Optimizer")
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
