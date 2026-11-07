package com.ikshana.drishti

import com.google.androidbrowserhelper.trusted.DelegationService

/**
 * Lets Chrome ask Play for product details and for purchases that are not yet consumed.
 * [DigitalGoodsRequestHandler] consumes a token only when the page calls consume().
 * The Drishti page never does. The server consumes the token after the answer starts.
 */
class DrishtiDelegationService : DelegationService() {
    override fun onCreate() {
        // First handler wins. This one answers listPurchases; every other
        // Digital Goods command is delegated to the library handler.
        registerExtraCommandHandler(OwnedListPurchasesHandler(applicationContext))
        super.onCreate()
    }
}
