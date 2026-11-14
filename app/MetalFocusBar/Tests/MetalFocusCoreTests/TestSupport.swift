import Foundation
@testable import MetalFocusCore

enum TestRepo {
    /// The metal-focus repo root (the directory that contains pyproject.toml).
    static let root: URL = {
        var url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        while url.path != "/" {
            if FileManager.default.fileExists(atPath: url.appendingPathComponent("pyproject.toml").path) { return url }
            url.deleteLastPathComponent()
        }
        fatalError("repo root not found")
    }()

    static func tempDir() -> URL {
        let d = FileManager.default.temporaryDirectory.appendingPathComponent("mf-\(UUID().uuidString)")
        try! FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }

    /// A temp "repo" with copies of the logger yaml files (+ optional local override text).
    static func repoCopy(local: String? = nil, configEdit: ((String) -> String)? = nil) -> URL {
        let d = tempDir()
        let logger = d.appendingPathComponent("logger")
        try! FileManager.default.createDirectory(at: logger, withIntermediateDirectories: true)
        for f in ["config.yaml", "financial.yaml"] {
            var text = try! String(contentsOf: root.appendingPathComponent("logger/\(f)"), encoding: .utf8)
            if f == "config.yaml", let edit = configEdit { text = edit(text) }
            try! text.write(to: logger.appendingPathComponent(f), atomically: true, encoding: .utf8)
        }
        if let local { try! local.write(to: logger.appendingPathComponent("config.local.yaml"), atomically: true, encoding: .utf8) }
        return d
    }
}
