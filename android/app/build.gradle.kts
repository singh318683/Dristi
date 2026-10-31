plugins {
    id("com.android.application")
}

android {
    namespace = "com.ikshana.drishti"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.ikshana.drishti"
        // Play Billing library 8, used by android-browser-helper billing 1.2.0, requires 23.
        minSdk = 24
        // Google Play requires new apps to target Android 16 (API 36) from 31 August 2026.
        targetSdk = 36
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        release {
            // No keystore is stored in the repo. Android Studio signs the bundle
            // when Neetu uses Build > Generate Signed App Bundle. This task stays unsigned.
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    buildFeatures {
        viewBinding = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation("com.google.androidbrowserhelper:androidbrowserhelper:2.7.3")
    // Play Billing Library 8.3.0. Required for Play submissions after 31 August 2026.
    // The page buys through the Digital Goods API. This app never consumes a purchase.
    implementation("com.google.androidbrowserhelper:billing:1.2.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
}
