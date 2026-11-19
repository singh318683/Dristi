import UIKit
import Capacitor
import WebKit

/// Forwards StoreKit purchases into the page without retaining the view controller.
final class DrishtiStoreProxy: NSObject, WKScriptMessageHandler {
    weak var controller: DrishtiBridgeViewController?

    init(controller: DrishtiBridgeViewController) {
        self.controller = controller
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        controller?.handleStoreMessage(message)
    }
}

/// Keeps voice playback inline and exposes `window.DrishtiStore` to the page.
/// Capacitor 6 already sets inline playback; this subclass keeps it if that default changes.
class DrishtiBridgeViewController: CAPBridgeViewController {
    private var storeProxy: DrishtiStoreProxy?

    private static let storeScript = """
    window.DrishtiStore = window.DrishtiStore || {
      purchase: function(productId) {
        return new Promise(function(resolve, reject) {
          var id = Date.now().toString(36) + Math.random().toString(36).slice(2);
          window.__drishtiStorePending = window.__drishtiStorePending || {};
          window.__drishtiStorePending[id] = {resolve: resolve, reject: reject};
          window.webkit.messageHandlers.drishtiStore.postMessage({id: id, productId: String(productId || "")});
        });
      }
    };
    window.__drishtiStoreResolve = function(id, payload) {
      var box = window.__drishtiStorePending && window.__drishtiStorePending[id];
      if (!box) return;
      delete window.__drishtiStorePending[id];
      box.resolve(payload);
    };
    window.__drishtiStoreReject = function(id, code, message) {
      var box = window.__drishtiStorePending && window.__drishtiStorePending[id];
      if (!box) return;
      delete window.__drishtiStorePending[id];
      var err = new Error(message || code || "missing");
      err.code = code || "missing";
      box.reject(err);
    };
    """

    override func webViewConfiguration(for instanceConfiguration: InstanceConfiguration) -> WKWebViewConfiguration {
        let config = super.webViewConfiguration(for: instanceConfiguration)
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        // Do not add scripts here. Capacitor 6 then assigns
        // webConfig.userContentController = delegationHandler.contentController,
        // which drops anything added on the configuration's original controller.
        return config
    }

    override func webView(with frame: CGRect, configuration: WKWebViewConfiguration) -> WKWebView {
        // prepareWebView has already swapped in the live content controller.
        // Register before WKWebView copies that configuration.
        installStoreBridge(on: configuration.userContentController)
        return super.webView(with: frame, configuration: configuration)
    }

    override func capacitorDidLoad() {
        super.capacitorDidLoad()
        // Capacitor injects window.Capacitor onto this same controller after the
        // web view exists and before loadWebView(). Match that.
        if let capBridge = bridge as? CapacitorBridge {
            installStoreBridge(on: capBridge.webViewDelegationHandler.contentController)
        } else if let controller = webView?.configuration.userContentController {
            installStoreBridge(on: controller)
        }
    }

    private func installStoreBridge(on controller: WKUserContentController) {
        if storeProxy == nil {
            storeProxy = DrishtiStoreProxy(controller: self)
        }
        if !controller.userScripts.contains(where: { $0.source.contains("window.__drishtiStoreResolve") }) {
            controller.addUserScript(WKUserScript(
                source: Self.storeScript,
                injectionTime: .atDocumentStart,
                forMainFrameOnly: true
            ))
        }
        controller.removeScriptMessageHandler(forName: "drishtiStore")
        if let storeProxy {
            controller.add(storeProxy, name: "drishtiStore")
        }
    }

    func handleStoreMessage(_ message: WKScriptMessage) {
        guard message.name == "drishtiStore",
              let body = message.body as? [String: Any],
              let productId = body["productId"] as? String else { return }
        let id: String
        if let text = body["id"] as? String {
            id = text
        } else if let number = body["id"] as? NSNumber {
            id = number.stringValue
        } else {
            return
        }
        if #available(iOS 15.0, *) {
            Task { @MainActor in
                await self.finishStorePurchase(id: id, productId: productId)
            }
        } else {
            rejectStore(id: id, code: "missing", message: "StoreKit requires iOS 15.")
        }
    }

    @available(iOS 15.0, *)
    @MainActor
    private func finishStorePurchase(id: String, productId: String) async {
        do {
            let payload = try await DrishtiStore().purchase(productId)
            let js = "window.__drishtiStoreResolve(\(Self.jsonText(id)), \(Self.jsonText(payload)))"
            webView?.evaluateJavaScript(js, completionHandler: nil)
        } catch {
            rejectStore(id: id, code: Self.storeCode(error), message: error.localizedDescription)
        }
    }

    private func rejectStore(id: String, code: String, message: String) {
        let js = "window.__drishtiStoreReject(\(Self.jsonText(id)), \(Self.jsonText(code)), \(Self.jsonText(message)))"
        webView?.evaluateJavaScript(js, completionHandler: nil)
    }

    @available(iOS 15.0, *)
    private static func storeCode(_ error: Error) -> String {
        guard let store = error as? DrishtiStore.StoreError else { return "missing" }
        switch store {
        case .cancelled: return "cancelled"
        case .pending: return "pending"
        case .unverified: return "unverified"
        case .missing: return "missing"
        }
    }

    private static func jsonText(_ value: Any) -> String {
        if let text = value as? String {
            guard let data = try? JSONSerialization.data(withJSONObject: [text]),
                  let wrapped = String(data: data, encoding: .utf8),
                  wrapped.count >= 2 else { return "null" }
            return String(wrapped.dropFirst().dropLast())
        }
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value),
              let text = String(data: data, encoding: .utf8) else { return "null" }
        return text
    }
}
