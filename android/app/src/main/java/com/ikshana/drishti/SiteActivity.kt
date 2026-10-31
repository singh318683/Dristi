package com.ikshana.drishti

import android.annotation.SuppressLint
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
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
import org.json.JSONArray
import org.json.JSONObject

/**
 * Used only when the phone has no browser that can run a Trusted Web Activity.
 * Exposes window.DrishtiPlay exactly as the website expects.
 *
 * This class never calls consumeAsync or acknowledgePurchase. The server consumes
 * the token after an Acharya answer starts, which is also how Play is told the
 * purchase was delivered.
 */
class SiteActivity : AppCompatActivity(), PurchasesUpdatedListener {
    private lateinit var web: WebView
    private lateinit var billing: BillingClient
    private var billingReady = false
    private var pendingPurchase: PendingPurchase? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)
        configureWeb()
        billing = BillingClient.newBuilder(this)
            .setListener(this)
            .enablePendingPurchases(
                PendingPurchasesParams.newBuilder().enableOneTimeProducts().build(),
            )
            .build()
        billing.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                billingReady = result.responseCode == BillingClient.BillingResponseCode.OK
            }

            override fun onBillingServiceDisconnected() {
                billingReady = false
            }
        })
        web.loadUrl(ORIGIN)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWeb() {
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.addJavascriptInterface(Bridge(), "DrishtiPlayNative")
        web.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                view?.evaluateJavascript(BRIDGE_JS, null)
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?,
            ) {
                if (request?.isForMainFrame == true) {
                    startActivity(android.content.Intent(this@SiteActivity, OfflineActivity::class.java))
                    finish()
                }
            }
        }
    }

    override fun onPurchasesUpdated(result: BillingResult, purchases: MutableList<Purchase>?) {
        val pending = pendingPurchase
        pendingPurchase = null
        if (pending == null) return
        val bought = purchases?.firstOrNull { it.purchaseState == Purchase.PurchaseState.PURCHASED }
        if (result.responseCode == BillingClient.BillingResponseCode.OK && bought != null) {
            finishPurchase(pending.requestId, bought)
            return
        }
        if (result.responseCode == BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED) {
            deliverOwned(pending.requestId, pending.productId)
            return
        }
        // Empty token: the page treats that as a cancelled purchase and does not ask the server.
        finishEmpty(pending.requestId)
    }

    private fun deliverOwned(requestId: String, productId: String) {
        val params = QueryPurchasesParams.newBuilder()
            .setProductType(BillingClient.ProductType.INAPP)
            .build()
        billing.queryPurchasesAsync(params) { _, purchases ->
            val owned = purchases.firstOrNull {
                it.purchaseState == Purchase.PurchaseState.PURCHASED && it.products.contains(productId)
            }
            if (owned != null) finishPurchase(requestId, owned) else finishEmpty(requestId)
        }
    }

    private fun finishPurchase(requestId: String, purchase: Purchase) {
        val body = JSONObject()
            .put("purchaseToken", purchase.purchaseToken)
            .put("productId", purchase.products.firstOrNull() ?: "")
            .put("orderId", purchase.orderId ?: "")
        finishJson(requestId, body)
    }

    private fun finishEmpty(requestId: String) {
        finishJson(requestId, JSONObject().put("purchaseToken", ""))
    }

    private fun finishJson(requestId: String, body: JSONObject) {
        val script = "window.__drishtiPlayFinish(${JSONObject.quote(requestId)}, $body)"
        runOnUiThread { web.evaluateJavascript(script, null) }
    }

    private fun finishList(requestId: String, purchases: List<Purchase>) {
        val rows = JSONArray()
        for (purchase in purchases) {
            if (purchase.purchaseState != Purchase.PurchaseState.PURCHASED) continue
            val productId = purchase.products.firstOrNull() ?: continue
            rows.put(
                JSONObject()
                    .put("itemId", productId)
                    .put("productId", productId)
                    .put("purchaseToken", purchase.purchaseToken),
            )
        }
        val script = "window.__drishtiPlayFinish(${JSONObject.quote(requestId)}, $rows)"
        runOnUiThread { web.evaluateJavascript(script, null) }
    }

    override fun onDestroy() {
        if (this::billing.isInitialized) billing.endConnection()
        if (this::web.isInitialized) web.destroy()
        super.onDestroy()
    }

    private inner class Bridge {
        @JavascriptInterface
        fun beginPurchase(requestId: String, productId: String) {
            runOnUiThread {
                if (!billingReady) {
                    finishEmpty(requestId)
                    return@runOnUiThread
                }
                val product = QueryProductDetailsParams.Product.newBuilder()
                    .setProductId(productId)
                    .setProductType(BillingClient.ProductType.INAPP)
                    .build()
                val query = QueryProductDetailsParams.newBuilder()
                    .setProductList(listOf(product))
                    .build()
                billing.queryProductDetailsAsync(query) { result, details ->
                    runOnUiThread {
                        val item: ProductDetails? = details.productDetailsList?.firstOrNull()
                        if (result.responseCode != BillingClient.BillingResponseCode.OK || item == null) {
                            finishEmpty(requestId)
                            return@runOnUiThread
                        }
                        pendingPurchase = PendingPurchase(requestId, productId)
                        val flow = BillingFlowParams.newBuilder()
                            .setProductDetailsParamsList(
                                listOf(
                                    BillingFlowParams.ProductDetailsParams.newBuilder()
                                        .setProductDetails(item)
                                        .build(),
                                ),
                            )
                            .build()
                        val launched = billing.launchBillingFlow(this@SiteActivity, flow)
                        if (launched.responseCode != BillingClient.BillingResponseCode.OK) {
                            pendingPurchase = null
                            if (launched.responseCode == BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED) {
                                deliverOwned(requestId, productId)
                            } else {
                                finishEmpty(requestId)
                            }
                        }
                    }
                }
            }
        }

        @JavascriptInterface
        fun beginList(requestId: String) {
            runOnUiThread {
                if (!billingReady) {
                    finishList(requestId, emptyList())
                    return@runOnUiThread
                }
                val params = QueryPurchasesParams.newBuilder()
                    .setProductType(BillingClient.ProductType.INAPP)
                    .build()
                billing.queryPurchasesAsync(params) { _, purchases ->
                    finishList(requestId, purchases)
                }
            }
        }
    }

    private data class PendingPurchase(val requestId: String, val productId: String)

    companion object {
        private const val ORIGIN = "https://dristi-six.vercel.app/"
        private val BRIDGE_JS = """
            (function () {
              if (window.DrishtiPlay && window.DrishtiPlay.__native) return;
              window.__drishtiPlayWait = window.__drishtiPlayWait || {};
              window.__drishtiPlayFinish = function (id, value) {
                var waiter = window.__drishtiPlayWait[id];
                if (!waiter) return;
                delete window.__drishtiPlayWait[id];
                waiter(value);
              };
              function wait(start) {
                return new Promise(function (resolve) {
                  var id = Math.random().toString(36).slice(2);
                  window.__drishtiPlayWait[id] = resolve;
                  start(id);
                });
              }
              window.DrishtiPlay = {
                __native: true,
                purchase: function (productId) {
                  return wait(function (id) { window.DrishtiPlayNative.beginPurchase(id, String(productId)); });
                },
                listPurchases: function () {
                  return wait(function (id) { window.DrishtiPlayNative.beginList(id); });
                }
              };
            })();
        """.trimIndent()
    }
}
