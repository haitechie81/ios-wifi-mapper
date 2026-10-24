// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "WiFiMapperCore",
    platforms: [
        .iOS(.v16),
        .macOS(.v13)
    ],
    products: [
        .library(
            name: "WiFiMapperCore",
            targets: ["WiFiMapperCore"]
        )
    ],
    targets: [
        .target(
            name: "WiFiMapperCore",
            path: "WiFiMapper",
            exclude: [
                "Info.plist",
                "WiFiMapper.entitlements",
                "WiFiMapperApp.swift"
            ]
        ),
        .testTarget(
            name: "WiFiMapperTests",
            dependencies: ["WiFiMapperCore"],
            path: "Tests/WiFiMapperTests"
        )
    ]
)
