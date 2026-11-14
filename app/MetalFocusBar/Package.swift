// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "MetalFocusBar",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/sqlcipher/SQLCipher.swift.git", from: "4.20.0"),
        .package(url: "https://github.com/jpsim/Yams.git", from: "5.1.0"),
    ],
    targets: [
        .target(
            name: "MetalFocusCore",
            dependencies: [.product(name: "SQLCipher", package: "SQLCipher.swift"), "Yams"]
        ),
        .executableTarget(name: "MetalFocusBar", dependencies: ["MetalFocusCore"]),
        .testTarget(name: "MetalFocusCoreTests", dependencies: ["MetalFocusCore"]),
    ]
)
