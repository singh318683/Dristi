package com.ikshana.drishti

import com.android.billingclient.api.Purchase
import org.json.JSONObject

/**
 * Play Billing 8 sometimes leaves [Purchase.getProducts] empty while the sku is
 * still in the purchase JSON. The library's listPurchases then crashes on
 * get(0), and a strict products.contains(sku) check launches the Play sheet
 * that says the item is already owned.
 */
internal object PlayPurchaseIds {
    fun ids(purchase: Purchase): Set<String> {
        val ids = linkedSetOf<String>()
        purchase.products?.forEach { if (!it.isNullOrEmpty()) ids.add(it) }
        try {
            purchase.skus?.forEach { if (!it.isNullOrEmpty()) ids.add(it) }
        } catch (_: Exception) {
            // getSkus is deprecated and may be absent on a future library.
        }
        try {
            val json = JSONObject(purchase.originalJson)
            json.optString("productId").takeIf { it.isNotEmpty() }?.let { ids.add(it) }
            val many = json.optJSONArray("productIds")
            if (many != null) {
                for (i in 0 until many.length()) {
                    many.optString(i).takeIf { it.isNotEmpty() }?.let { ids.add(it) }
                }
            }
        } catch (_: Exception) {
            // A purchase with no JSON still has getProducts().
        }
        return ids
    }

    fun tokenFor(purchases: List<Purchase>?, sku: String): String? {
        val live = purchases.orEmpty().filter {
            it.purchaseState == Purchase.PurchaseState.PURCHASED ||
                it.purchaseState == Purchase.PurchaseState.PENDING
        }
        val exact = live.firstOrNull { sku in ids(it) && !it.purchaseToken.isNullOrEmpty() }
        if (exact != null) return exact.purchaseToken
        // This app sells one question. The server still checks the product id,
        // so a mismatched token cannot become a free question.
        if (live.size == 1 && !live[0].purchaseToken.isNullOrEmpty()) return live[0].purchaseToken
        return null
    }
}
