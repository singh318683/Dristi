import UIKit
import Capacitor
import WebKit

/// Keeps voice playback inline. Capacitor 6 already sets these on its bridge;
/// this subclass keeps them if that default ever changes.
class DrishtiBridgeViewController: CAPBridgeViewController {
    override func webViewConfiguration(for instanceConfiguration: InstanceConfiguration) -> WKWebViewConfiguration {
        let config = super.webViewConfiguration(for: instanceConfiguration)
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        return config
    }
}
