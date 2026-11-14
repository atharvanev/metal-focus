import Foundation
import Security

public protocol KeyStore: Sendable {
    func get() throws -> String?
    func set(_ key: String) throws
}

public enum DBError: Error, Equatable, CustomStringConvertible {
    case keyMissing, wrongKey, schemaMissing(String), keychain(Int32), sqlite(code: Int32, message: String)
    public var description: String {
        switch self {
        case .keyMissing: "Database key missing from Keychain — refusing to create a new one."
        case .wrongKey: "The Keychain key does not open the database."
        case .schemaMissing(let p): "Schema file not found: \(p)"
        case .keychain(let s): "Keychain error \(s)"
        case .sqlite(let c, let m): "SQLite error \(c): \(m)"
        }
    }
}

/// Generic password shared with the repo's `keyring`-based readers (service "metal-focus", account "sqlcipher-key").
public struct KeychainKeyStore: KeyStore {
    public static let service = "metal-focus", account = "sqlcipher-key"
    public init() {}

    public func get() throws -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: Self.service,
                                kSecAttrAccount as String: Self.account, kSecReturnData as String: true,
                                kSecMatchLimit as String: kSecMatchLimitOne]
        var out: AnyObject?
        let status = SecItemCopyMatching(q as CFDictionary, &out)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = out as? Data else { throw DBError.keychain(status) }
        return String(decoding: data, as: UTF8.self)
    }

    public func set(_ key: String) throws {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: Self.service,
                                kSecAttrAccount as String: Self.account, kSecValueData as String: Data(key.utf8)]
        let status = SecItemAdd(q as CFDictionary, nil)
        guard status == errSecSuccess else { throw DBError.keychain(status) }
    }
}

public enum KeyRules {
    /// The stored key; or a new one only if there is no key AND no DB file. Never mints a key for an existing DB.
    public static func getOrCreateKey(dbURL: URL, store: KeyStore) throws -> String {
        if let k = try store.get(), !k.isEmpty { return k }
        if FileManager.default.fileExists(atPath: dbURL.path) { throw DBError.keyMissing }
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { throw DBError.keychain(-1) }
        let key = bytes.map { String(format: "%02x", $0) }.joined()
        try store.set(key)
        return key
    }
}

/// Data directory: the `MFHome` default (Swift's METAL_FOCUS_HOME) or ~/Library/Application Support/metal-focus.
public enum DataHome {
    public static func directory(defaults: UserDefaults = .standard) -> URL {
        let dir = defaults.string(forKey: "MFHome").map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Application Support/metal-focus")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir.path)
        return dir
    }

    public static func dbURL(defaults: UserDefaults = .standard) -> URL {
        directory(defaults: defaults).appendingPathComponent("activity.db")
    }
}
