//
//  RemoteSessionSettings.swift
//  ClaudeIsland
//
//  Settings for remote SSH session support
//

import Foundation
import Combine

/// Manages settings for remote SSH session support
class RemoteSessionSettings: ObservableObject {
    static let shared = RemoteSessionSettings()

    private enum Keys {
        static let remoteSessionsEnabled = "remoteSessionsEnabled"
        static let tcpPort = "remoteSessionTcpPort"
    }

    /// Whether the TCP server for remote sessions is enabled
    @Published var isEnabled: Bool {
        didSet {
            UserDefaults.standard.set(isEnabled, forKey: Keys.remoteSessionsEnabled)
            notifySettingsChanged()
        }
    }

    /// The TCP port for remote session connections
    @Published var port: UInt16 {
        didSet {
            UserDefaults.standard.set(Int(port), forKey: Keys.tcpPort)
            notifySettingsChanged()
        }
    }

    /// Notification posted when settings change
    static let settingsChangedNotification = Notification.Name("RemoteSessionSettingsChanged")

    private init() {
        self.isEnabled = UserDefaults.standard.bool(forKey: Keys.remoteSessionsEnabled)
        let savedPort = UserDefaults.standard.integer(forKey: Keys.tcpPort)
        self.port = savedPort > 0 ? UInt16(savedPort) : defaultRemoteSessionPort
    }

    private func notifySettingsChanged() {
        NotificationCenter.default.post(name: Self.settingsChangedNotification, object: self)
    }

    /// Get the connection string for display (e.g., for copying to remote machine)
    func getConnectionInstructions() -> String {
        // Get the local IP address
        let hostname = getLocalIPAddress() ?? "YOUR_MAC_IP"
        return """
        # Add to your remote shell profile (.bashrc, .zshrc, etc.):
        export CLAUDE_ISLAND_HOST=\(hostname)
        export CLAUDE_ISLAND_PORT=\(port)
        """
    }

    /// Get the local IP address
    private func getLocalIPAddress() -> String? {
        var address: String?

        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0, let firstAddr = ifaddr else {
            return nil
        }
        defer { freeifaddrs(ifaddr) }

        for ptr in sequence(first: firstAddr, next: { $0.pointee.ifa_next }) {
            let interface = ptr.pointee
            let addrFamily = interface.ifa_addr.pointee.sa_family

            // Check for IPv4
            if addrFamily == UInt8(AF_INET) {
                let name = String(cString: interface.ifa_name)

                // Skip loopback interface
                if name == "lo0" { continue }

                // Prefer en0 (WiFi) or en1 (Ethernet)
                if name.hasPrefix("en") {
                    var hostname = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                    getnameinfo(
                        interface.ifa_addr,
                        socklen_t(interface.ifa_addr.pointee.sa_len),
                        &hostname,
                        socklen_t(hostname.count),
                        nil,
                        0,
                        NI_NUMERICHOST
                    )
                    address = String(cString: hostname)

                    // Prefer en0
                    if name == "en0" {
                        break
                    }
                }
            }
        }

        return address
    }
}
