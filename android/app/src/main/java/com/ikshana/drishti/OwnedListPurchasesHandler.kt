package com.ikshana.drishti

import android.content.Context
import android.os.Bundle
import androidx.browser.trusted.TrustedWebActivityCallbackRemote
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.PendingPurchasesParams
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.QueryPurchasesParams
import com.google.androidbrowserhelper.playbilling.digitalgoods.DigitalGoodsRequestHandler
import com.google.androidbrowserhelper.trusted.ExtraCommandHandler

/**
 * Handles Chrome's listPurchases command without the library crash.
 *
 * android-browser-helper billing 1.2.0 calls Purchase.getProducts().get(0).
 * An empty product list throws, the callback never returns, and the page
 * opens the Play sheet. listPurchaseHistory() in that library is a no-op.
 * This handler is registered first and answers listPurchases from the same
 * query, including a purchase whose sku is only in the original JSON.
 */
class OwnedListPurchasesHandler(context: Context) : ExtraCommandHandler {
    private val appContext = context.applicationContext
    private val fallback = DigitalGoodsRequestHandler(appContext)

    override fun handleExtraCommand(
        context: Context,
        commandName: String,
        args: Bundle,
        callback: TrustedWebActivityCallbackRemote?,
    ): Bundle {
        if (commandName == "listPurchases" && callback != null) {
            reply(callback)
            return Bundle().apply {
                putBoolean(ExtraCommandHandler.EXTRA_COMMAND_SUCCESS, true)
                putInt("digital_goods_api_version", 2)
            }
        }
        return fallback.handleExtraCommand(context, commandName, args, callback)
    }

    private fun reply(callback: TrustedWebActivityCallbackRemote) {
        val client = BillingClient.newBuilder(appContext)
            .setListener { _, _ -> }
            .enablePendingPurchases(
                PendingPurchasesParams.newBuilder().enableOneTimeProducts().build(),
            )
            .build()
        client.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                if (result.responseCode != BillingClient.BillingResponseCode.OK) {
                    send(callback, emptyList())
                    client.endConnection()
                    return
                }
                query(client) { purchases ->
                    send(callback, purchases)
                    client.endConnection()
                }
            }

            override fun onBillingServiceDisconnected() {
                send(callback, emptyList())
                client.endConnection()
            }
        })
    }

    private fun query(client: BillingClient, done: (List<Purchase>) -> Unit) {
        val all = mutableListOf<Purchase>()
        var remaining = 2
        for (type in listOf(BillingClient.ProductType.INAPP, BillingClient.ProductType.SUBS)) {
            val params = QueryPurchasesParams.newBuilder().setProductType(type).build()
            client.queryPurchasesAsync(params) { _, purchases ->
                synchronized(all) {
                    if (purchases != null) all.addAll(purchases)
                    remaining -= 1
                    if (remaining == 0) done(all.toList())
                }
            }
        }
    }

    private fun send(callback: TrustedWebActivityCallbackRemote, purchases: List<Purchase>) {
        val bundles = purchases.mapNotNull { purchase ->
            val token = purchase.purchaseToken ?: return@mapNotNull null
            if (token.isEmpty()) return@mapNotNull null
            if (purchase.purchaseState != Purchase.PurchaseState.PURCHASED &&
                purchase.purchaseState != Purchase.PurchaseState.PENDING
            ) {
                return@mapNotNull null
            }
            Bundle().apply {
                putString("purchaseDetails.itemId", PlayPurchaseIds.ids(purchase).firstOrNull() ?: "")
                putString("purchaseDetails.purchaseToken", token)
            }
        }
        val args = Bundle().apply {
            putInt("listPurchases.responseCode", 0)
            putParcelableArray("listPurchases.purchasesList", bundles.toTypedArray())
        }
        try {
            callback.runExtraCallback("listPurchases.response", args)
        } catch (_: Exception) {
            // Chrome went away.
        }
    }
}
