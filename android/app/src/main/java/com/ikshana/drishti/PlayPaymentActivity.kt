package com.ikshana.drishti

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import androidx.browser.trusted.TokenStore
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.PendingPurchasesParams
import com.android.billingclient.api.ProductDetails
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.PurchasesUpdatedListener
import com.android.billingclient.api.QueryProductDetailsParams
import com.android.billingclient.api.QueryPurchasesParams
import com.google.androidbrowserhelper.playbilling.provider.MethodData
import com.google.androidbrowserhelper.trusted.ChromeOsSupport
import com.google.androidbrowserhelper.trusted.SharedPreferencesTokenStore
import org.json.JSONObject

/**
 * Chrome's Play payment sheet. The library activity fails an already-owned
 * consumable without returning its token, and listPurchaseHistory() is empty
 * on Play Billing 8. This activity hands the existing token back instead.
 * It never acknowledges or consumes; the server does that after an answer starts.
 */
class PlayPaymentActivity : Activity(), PurchasesUpdatedListener {
    private var billing: BillingClient? = null
    private var sku: String = ""
    private var finished = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val caller = callingActivity?.packageName
        if (!callerMayPay(caller)) {
            fail("Launching app is not verified.")
            return
        }
        val data = MethodData.fromIntent(intent)
        if (data == null || data.sku.isNullOrEmpty() || data.isPriceChangeConfirmation) {
            fail("Could not parse product ID.")
            return
        }
        sku = data.sku
        val client = BillingClient.newBuilder(this)
            .setListener(this)
            .enablePendingPurchases(
                PendingPurchasesParams.newBuilder().enableOneTimeProducts().build(),
            )
            .build()
        billing = client
        client.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                if (result.responseCode != BillingClient.BillingResponseCode.OK) {
                    fail("BillingClient disconnected.")
                    return
                }
                returnOwnedOrBuy()
            }

            override fun onBillingServiceDisconnected() {
                fail("BillingClient disconnected.")
            }
        })
    }

    private fun callerMayPay(packageName: String?): Boolean {
        if (packageName.isNullOrEmpty()) return false
        if (ChromeOsSupport.isRunningOnArc(packageManager) &&
            packageName == ChromeOsSupport.ARC_PAYMENT_APP
        ) {
            return true
        }
        val store: TokenStore = SharedPreferencesTokenStore(this)
        val verified = store.load() ?: return false
        return verified.matches(packageName, packageManager)
    }

    private fun returnOwnedOrBuy(allowLaunch: Boolean = true) {
        if (billing == null) return
        queryOwned { purchases ->
            val owned = ownedToken(purchases)
            if (owned != null) {
                succeed(owned)
                return@queryOwned
            }
            if (allowLaunch) launchFlow()
            else fail("Payment attempt failed (have you already bought the item?).")
        }
    }

    private fun queryOwned(done: (List<Purchase>) -> Unit) {
        val client = billing ?: return
        val all = mutableListOf<Purchase>()
        var remaining = 2
        val types = listOf(BillingClient.ProductType.INAPP, BillingClient.ProductType.SUBS)
        for (type in types) {
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

    private fun ownedToken(purchases: List<Purchase>?): String? {
        return PlayPurchaseIds.tokenFor(purchases, sku)
    }

    private fun launchFlow() {
        val client = billing ?: return
        val product = QueryProductDetailsParams.Product.newBuilder()
            .setProductId(sku)
            .setProductType(BillingClient.ProductType.INAPP)
            .build()
        val query = QueryProductDetailsParams.newBuilder()
            .setProductList(listOf(product))
            .build()
        client.queryProductDetailsAsync(query) { result, details ->
            val item: ProductDetails? = details.productDetailsList?.firstOrNull()
            if (result.responseCode != BillingClient.BillingResponseCode.OK || item == null) {
                fail("Play Billing did not find product.")
                return@queryProductDetailsAsync
            }
            val flow = BillingFlowParams.newBuilder()
                .setProductDetailsParamsList(
                    listOf(
                        BillingFlowParams.ProductDetailsParams.newBuilder()
                            .setProductDetails(item)
                            .build(),
                    ),
                )
                .build()
            runOnUiThread {
                val launched = client.launchBillingFlow(this, flow)
                if (launched.responseCode == BillingClient.BillingResponseCode.OK) return@runOnUiThread
                if (launched.responseCode == BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED) {
                    returnOwnedOrBuy(allowLaunch = false)
                    return@runOnUiThread
                }
                fail("Payment attempt failed (have you already bought the item?).")
            }
        }
    }

    override fun onPurchasesUpdated(result: BillingResult, purchases: MutableList<Purchase>?) {
        val owned = ownedToken(purchases)
        if (owned != null &&
            (result.responseCode == BillingClient.BillingResponseCode.OK ||
                result.responseCode == BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED)
        ) {
            succeed(owned)
            return
        }
        if (result.responseCode == BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED) {
            returnOwnedOrBuy(allowLaunch = false)
            return
        }
        if (result.responseCode == BillingClient.BillingResponseCode.USER_CANCELED) {
            fail("The user closed the payment request.")
            return
        }
        fail("Purchase flow ended with result: $result")
    }

    private fun succeed(token: String) {
        if (finished) return
        finished = true
        val details = JSONObject().put("token", token).put("purchaseToken", token).toString()
        setResult(
            RESULT_OK,
            Intent()
                .putExtra("methodName", "https://play.google.com/billing")
                .putExtra("details", details),
        )
        finish()
    }

    private fun fail(message: String) {
        if (finished) return
        finished = true
        val details = JSONObject().put("error", message).toString()
        setResult(
            RESULT_CANCELED,
            Intent()
                .putExtra("methodName", "https://play.google.com/billing")
                .putExtra("details", details),
        )
        finish()
    }

    override fun onDestroy() {
        billing?.endConnection()
        super.onDestroy()
    }
}
