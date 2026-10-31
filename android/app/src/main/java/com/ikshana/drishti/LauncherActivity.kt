package com.ikshana.drishti

import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.util.Log
import com.google.androidbrowserhelper.trusted.TwaLauncher

/**
 * Opens https://dristi-six.vercel.app as a Trusted Web Activity.
 * Play Billing is the Digital Goods API inside that activity. This class does not
 * acknowledge or consume a purchase; the server does that after an answer starts.
 */
class LauncherActivity : com.google.androidbrowserhelper.trusted.LauncherActivity() {
    override fun shouldLaunchImmediately(): Boolean = isOnline()

    override fun onCreate(savedInstanceState: android.os.Bundle?) {
        try {
            super.onCreate(savedInstanceState)
        } catch (error: Exception) {
            // The helper toggles ManageDataLauncherActivity from inside onCreate.
            // A failure there used to kill the process on Android 12. The component
            // is declared in the manifest; this catch is so a rejected toggle still
            // cannot stop the site from opening.
            Log.e(TAG, "Launcher onCreate failed; opening the site another way", error)
            if (!isFinishing) openShell()
            return
        }
        if (!isOnline() && !isFinishing) {
            startActivity(Intent(this, OfflineActivity::class.java))
            finish()
        }
    }

    override fun launchTwa() {
        try {
            super.launchTwa()
        } catch (error: Exception) {
            Log.e(TAG, "Trusted Web Activity launch hit a component-setting failure", error)
            if (isFinishing || componentToggleAfterLaunch(error)) return
            openShell()
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

    /**
     * addSiteSettingsShortcut runs only after TwaLauncher.launch. That path has
     * already asked the browser (or the WebView fallback) to open, so starting
     * another activity here would stack a second copy of the site.
     */
    private fun componentToggleAfterLaunch(error: Throwable): Boolean {
        return generateSequence(error) { it.cause }.any { thrown ->
            thrown.stackTrace.any { frame ->
                frame.methodName == "setComponentEnabledSetting" ||
                    frame.methodName == "addSiteSettingsShortcut"
            }
        }
    }

    private fun openShell() {
        val next = if (isOnline()) SiteActivity::class.java else OfflineActivity::class.java
        startActivity(Intent(this, next))
        finish()
    }

    private fun isOnline(): Boolean {
        val manager = getSystemService(ConnectivityManager::class.java) ?: return true
        val network = manager.activeNetwork ?: return false
        val caps = manager.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    private companion object {
        const val TAG = "Drishti"
    }
}
