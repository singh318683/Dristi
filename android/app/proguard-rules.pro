# Release builds do not minify. If that is turned on later, keep Play Billing
# and the Trusted Web Activity service, and still do not add a consume rule of our own.
-keep class com.google.androidbrowserhelper.** { *; }
-keep class com.android.billingclient.** { *; }
