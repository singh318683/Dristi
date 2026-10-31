package com.ikshana.drishti

import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.google.androidbrowserhelper.trusted.TwaLauncher

/**
 * Opens https://dristi-six.vercel.app as a Trusted Web Activity.
 * Play Billing is the Digital Goods API inside that activity. This class does not
 * acknowledge or consume a purchase; the server does that after an answer starts.
 */
class LauncherActivity : com.google.androidbrowserhelper.trusted.LauncherActivity() {
    override fun shouldLaunchImmediately(): Boolean = isOnline()

    override fun onCreate(savedInstanceState: android.os.Bundle?) {
        super.onCreate(savedInstanceState)
        if (!isOnline() && !isFinishing) {
            startActivity(Intent(this, OfflineActivity::class.java))
            finish()
        }
    }

    override fun getFallbackStrategy(): TwaLauncher.FallbackStrategy {
        // A device without a Trusted Web Activity browser still gets the site, and
        // window.DrishtiPlay, without consuming the Play purchase.
        return TwaLauncher.FallbackStrategy { context, _, _, done ->
            context.startActivity(Intent(context, SiteActivity::class.java))
            done?.run()
        }
    }

    private fun isOnline(): Boolean {
        val manager = getSystemService(ConnectivityManager::class.java) ?: return true
        val network = manager.activeNetwork ?: return false
        val caps = manager.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }
}
