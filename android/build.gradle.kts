plugins {
    // Current stable Android Studio (Rabbit 2, 2026.2.2) syncs AGP 9.5.
    // Built-in Kotlin is on by default in AGP 9, so this project does not apply
    // the org.jetbrains.kotlin.android plugin.
    id("com.android.application") version "9.5.0" apply false
}
