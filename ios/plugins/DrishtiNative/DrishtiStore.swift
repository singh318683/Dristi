import Foundation
import StoreKit
import CallKit
import AVFoundation

/// StoreKit 2 consumables. Product ids: acharya_question, wallet_5, wallet_10, wallet_20.
/// The page calls `window.DrishtiStore.purchase(productId)` and posts the transaction id to the server.
@MainActor
final class DrishtiStore: NSObject {
    func purchase(_ productId: String) async throws -> [String: String] {
        let products = try await Product.products(for: [productId])
        guard let product = products.first else { throw StoreError.missing }
        let result = try await product.purchase()
        switch result {
        case .success(let verification):
            let transaction = try checkVerified(verification)
            let payload: [String: String] = [
                "transactionId": String(transaction.id),
                "signedTransaction": verification.jwsRepresentation,
                "productId": transaction.productID,
            ]
            await transaction.finish()
            return payload
        case .userCancelled:
            throw StoreError.cancelled
        case .pending:
            throw StoreError.pending
        @unknown default:
            throw StoreError.missing
        }
    }

    private func checkVerified<T>(_ result: VerificationResult<T>) throws -> T {
        switch result {
        case .unverified:
            throw StoreError.unverified
        case .verified(let value):
            return value
        }
    }

    enum StoreError: Error { case missing, cancelled, pending, unverified }
}

/// Reports an in-app voice session to the system. Audio stays in the WebView's WebRTC room.
final class DrishtiCall: NSObject, CXProviderDelegate {
    private let provider: CXProvider = {
        let config = CXProviderConfiguration()
        config.supportsVideo = false
        config.maximumCallsPerCallGroup = 1
        config.supportedHandleTypes = [.generic]
        return CXProvider(configuration: config)
    }()

    override init() {
        super.init()
        provider.setDelegate(self, queue: nil)
        try? AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetooth])
    }

    func start(name: String) {
        let update = CXCallUpdate()
        update.remoteHandle = CXHandle(type: .generic, value: name)
        update.hasVideo = false
        provider.reportNewIncomingCall(with: UUID(), update: update) { _ in }
    }

    func providerDidReset(_ provider: CXProvider) {}
}
