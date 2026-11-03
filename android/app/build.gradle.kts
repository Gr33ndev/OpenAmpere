// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
// The Android app (#567): the unchanged server of OpenAmpere in a foreground service (Chaquopy), the web app in a
// WebView. Before building: `cd web && npm run build` (the web app goes into the Python package) and
// `scripts/android-wheels.sh` (pydantic-core for Android).
plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

val repo: File = rootDir.parentFile
// the version of the Python package, so the app and the server inside it always say the same
val appVersion: String = Regex("""(?m)^version = "([^"]+)"""").find(repo.resolve("pyproject.toml").readText())!!
    .groupValues[1]

android {
    namespace = "io.github.gr33ndev.openampere"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.gr33ndev.openampere"
        minSdk = 24 // Android 7.0, the oldest Chaquopy supports: old tablets
        targetSdk = 35
        // 0.37.0 -> 37000; every release gets a higher number, which Android needs for an update
        versionCode = appVersion.split(".").let { (major, minor, patch) ->
            major.toInt() * 1_000_000 + minor.toInt() * 1_000 + patch.toInt()
        }
        versionName = appVersion
        ndk {
            // 32-bit ARM for many old tablets; x86_64 for the emulator
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

// OpenAmpere itself as Python source, with its web app and texts, and the metadata from which it reads its version
// (importlib.metadata), as pip would install it. A source folder, not a pip package: Chaquopy installs pip packages
// again only when the requirements change, so a changed web app with the same version would not get in.
val pythonSource: File = layout.buildDirectory.dir("python-src").get().asFile
val stagePython = tasks.register<Sync>("stagePython") {
    into(pythonSource)
    from(repo.resolve("src")) {
        include("openampere/**")
        exclude("**/__pycache__/**", "**/*.pyc")
    }
    inputs.property("version", appVersion)
    doLast {
        pythonSource.resolve("openampere-$appVersion.dist-info").apply { mkdirs() }
            .resolve("METADATA").writeText("Metadata-Version: 2.1\nName: openampere\nVersion: $appVersion\n")
    }
}
tasks.configureEach {
    if (name.startsWith("merge") && name.endsWith("PythonSources")) dependsOn(stagePython)
}

// The packages of requirements.lock (the Docker image's versions), except where Chaquopy only has an older Android
// build; pyproject.toml allows these. pydantic-core comes from android/wheels/ (scripts/android-wheels.sh).
val chaquopyVersions = mapOf("cryptography" to "42.0.8", "cffi" to "1.17.1")
val requirements: File = layout.buildDirectory.file("requirements-android.txt").get().asFile
requirements.parentFile.mkdirs()
requirements.writeText(
    repo.resolve("requirements.lock").readLines()
        .map { it.trim().removeSuffix("\\").trim() }
        .filter { it.isNotEmpty() && !it.startsWith("#") && !it.startsWith("--hash") }
        .joinToString("\n", postfix = "\n") { line ->
            val name = line.substringBefore("==")
            chaquopyVersions[name]?.let { "$name==$it" } ?: line
        } +
        // Android has no time zone database for Python (Linux has it in /usr/share/zoneinfo)
        "tzdata==2026.6\n",
)

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            options("--find-links", rootDir.resolve("wheels").path)
            install("-r", requirements.path)
        }
        // the server reads its web app and texts from files next to its modules
        extractPackages("openampere")
    }
    sourceSets {
        getByName("main") {
            srcDir(pythonSource)
        }
    }
}
