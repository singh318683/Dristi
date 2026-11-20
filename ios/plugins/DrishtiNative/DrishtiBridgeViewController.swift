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
        if message.name == "drishtiFile" {
            controller?.handleFileMessage(message)
        } else {
            controller?.handleStoreMessage(message)
        }
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

    private static let fileScript = """
    window.DrishtiFiles = window.DrishtiFiles || {
      sharePdf: function(filename, data) {
        return new Promise(function(resolve, reject) {
          var id = Date.now().toString(36) + Math.random().toString(36).slice(2);
          window.__drishtiFilePending = window.__drishtiFilePending || {};
          window.__drishtiFilePending[id] = {resolve: resolve, reject: reject};
          window.webkit.messageHandlers.drishtiFile.postMessage({
            id: id,
            filename: String(filename || "drishti-kundli.pdf"),
            data: String(data || "")
          });
        });
      }
    };
    window.__drishtiFileResolve = function(id, payload) {
      var box = window.__drishtiFilePending && window.__drishtiFilePending[id];
      if (!box) return;
      delete window.__drishtiFilePending[id];
      box.resolve(payload || {ok: true});
    };
    window.__drishtiFileReject = function(id, code, message) {
      var box = window.__drishtiFilePending && window.__drishtiFilePending[id];
      if (!box) return;
      delete window.__drishtiFilePending[id];
      var err = new Error(message || code || "share_failed");
      err.code = code || "share_failed";
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
        if !controller.userScripts.contains(where: { $0.source.contains("window.__drishtiFileResolve") }) {
            controller.addUserScript(WKUserScript(
                source: Self.fileScript,
                injectionTime: .atDocumentStart,
                forMainFrameOnly: true
            ))
        }
        controller.removeScriptMessageHandler(forName: "drishtiStore")
        controller.removeScriptMessageHandler(forName: "drishtiFile")
        if let storeProxy {
            controller.add(storeProxy, name: "drishtiStore")
            controller.add(storeProxy, name: "drishtiFile")
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

    func handleFileMessage(_ message: WKScriptMessage) {
        guard message.name == "drishtiFile",
              let body = message.body as? [String: Any] else { return }
        let id: String
        if let text = body["id"] as? String {
            id = text
        } else if let number = body["id"] as? NSNumber {
            id = number.stringValue
        } else {
            return
        }
        let filename = (body["filename"] as? String) ?? "drishti-kundli.pdf"
        let encoded = (body["data"] as? String) ?? ""
        guard let data = Data(base64Encoded: encoded, options: .ignoreUnknownCharacters), !data.isEmpty else {
            rejectFile(id: id, code: "empty", message: "The PDF was empty.")
            return
        }
        do {
            let url = try writePdf(filename: filename, data: data)
            DispatchQueue.main.async { [weak self] in
                self?.presentPdfShare(url: url, id: id)
            }
        } catch {
            rejectFile(id: id, code: "write_failed", message: "The PDF could not be saved.")
        }
    }

    private func writePdf(filename: String, data: Data) throws -> URL {
        let base = (filename as NSString).lastPathComponent
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: ".-"))
        let cleaned = String(base.unicodeScalars.map { allowed.contains($0) ? Character($0) : "-" }.prefix(80))
        var name = cleaned.isEmpty ? "drishti-kundli.pdf" : cleaned
        if !name.lowercased().hasSuffix(".pdf") {
            name += ".pdf"
        }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(name)
        try data.write(to: url, options: .atomic)
        return url
    }

    private func presentPdfShare(url: URL, id: String) {
        let activity = UIActivityViewController(activityItems: [url], applicationActivities: nil)
        if let popover = activity.popoverPresentationController {
            let anchor = view ?? UIView()
            popover.sourceView = anchor
            popover.sourceRect = CGRect(x: anchor.bounds.midX, y: anchor.bounds.midY, width: 1, height: 1)
            popover.permittedArrowDirections = []
        }
        activity.completionWithItemsHandler = { [weak self] _, _, _, error in
            if let error = error {
                self?.rejectFile(id: id, code: "share_failed", message: error.localizedDescription)
            } else {
                self?.resolveFile(id: id)
            }
        }
        let host = presentedViewController ?? self
        host.present(activity, animated: true)
    }

    private func resolveFile(id: String) {
        let js = "window.__drishtiFileResolve(\(Self.jsonText(id)), {\"ok\":true})"
        webView?.evaluateJavaScript(js, completionHandler: nil)
    }

    private func rejectFile(id: String, code: String, message: String) {
        let js = "window.__drishtiFileReject(\(Self.jsonText(id)), \(Self.jsonText(code)), \(Self.jsonText(message)))"
        webView?.evaluateJavaScript(js, completionHandler: nil)
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
