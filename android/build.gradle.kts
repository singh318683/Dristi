plugins {
    // AGP 9.1.0 is the newest plugin Neetu's Android Studio can sync.
    // Its release notes require Gradle 9.3.1 (that is both the minimum and the default).
    // Built-in Kotlin is on, so this project does not apply org.jetbrains.kotlin.android.
    // The 9.1 compatibility table lists Kotlin Gradle plugin 2.2.10 as the default.
    id("com.android.application") version "9.1.0" apply false
}
