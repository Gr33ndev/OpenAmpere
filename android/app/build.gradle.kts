// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
// The Android app (#567): the unchanged server of OpenAmpere in a foreground service (Chaquopy), the web app in a
// WebView. Before building: `cd web && npm run build` (the web app goes into the Python package) and
// `scripts/android-wheels.sh` (pydantic-core for Android).
import java.security.MessageDigest

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
        // every release gets a higher number, which Android needs for an update: 0.37.0 -> 370009, its release
        // candidates 0.37.0-rc.1 -> 370001 ... -rc.8 -> 370008, below the final version. scripts/release.sh and the
        // release workflow allow only these forms
        versionCode = Regex("""^(\d+)\.(\d+)\.(\d+)(?:-rc\.([1-8]))?$""").matchEntire(appVersion)
            ?.destructured?.let { (major, minor, patch, candidate) ->
                (major.toInt() * 1_000_000 + minor.toInt() * 1_000 + patch.toInt()) * 10 + (candidate.toIntOrNull() ?: 9)
            } ?: error("version $appVersion in pyproject.toml: only X.Y.Z or X.Y.Z-rc.1 to -rc.8")
        versionName = appVersion
        ndk {
            // 32-bit ARM for many old tablets; x86_64 for the emulator
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
    }

    // The release key (#567): Android installs an update only when it is signed with the same key as the installed
    // app, so this key is what owners trust. It lives only as secrets of the release workflow and in the
    // maintainers' offline backup (docs/android-signing.md); without these variables a release build is unsigned.
    val keystore: String? = System.getenv("OPENAMPERE_KEYSTORE")
    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = file(keystore)
                storePassword = System.getenv("OPENAMPERE_KEYSTORE_PASSWORD")
                keyAlias = "openampere"
                keyPassword = System.getenv("OPENAMPERE_KEYSTORE_PASSWORD") // PKCS12: one password for both
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystore != null) signingConfig = signingConfigs.getByName("release")
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

// The packages of requirements.lock with their hashes (the Docker image's versions), so pip checks every file that
// goes into the APK. Native packages come as Chaquopy's Android builds instead (cryptography and cffi only in older
// versions, which pyproject.toml allows), with the SHA-256 of each file for the three ABIs; pydantic-core from
// android/wheels/ (scripts/android-wheels.sh), with the hashes of the wheels built there.
val chaquopyWheels = mapOf(
    "cryptography" to ("42.0.8" to listOf(
        "efca75f2ad2b0e29e2c6994316af98b59fc8f28161065e599a816678f061258e", // arm64-v8a
        "7e44183f6d297d75e415da20ead13bb193b9923a27a8f61c31af592f9651ab2b", // armeabi-v7a
        "9edef96c41a8a7c48d565ee9a706cb436242a43531616a74819c1d7bf32c67b8", // x86_64
    )),
    "cffi" to ("1.17.1" to listOf(
        "87f4d395d14d77f3cbe65a3468904b35214bd68c8c34233bc0fc6422a4c0052b",
        "8495e21e45face6f5643b0c545fa171175aa41210c496481a211a41083e2377a",
        "7da440706719d4cce43de1ccae1feba11b55871e2cf279949a97571155b02536",
    )),
    "pyyaml" to ("6.0.3" to listOf(
        "75013ba172066d8e009cf8c7a2fa7a2b2d9e83fc9913470d7f5f707872c4885d",
        "7e4ac4964aaeebc6d8aeedadb05d8936e9e192d119ee011680da57b2cc2c519b",
        "480ad0e88271416c9d297150dc722680b9ba95b216ed19ecf2f83e96d63ca1aa",
    )),
)
val chaquopyLibraries = mapOf(
    "chaquopy-libffi" to ("3.3" to listOf(
        "86873c2f8e5e43b07b72233bf35e06ad8dae41ba98e0556202d61d00d7ad952f",
        "8e9c18da8173747ffd7a26df408b4137e9756f856a247c617fa43270f86baef8",
        "bf5e4a533efe7c1b2687c8db33e5c93c102de12aea40d860570e5ec11e29dc31",
    )),
    "chaquopy-libyaml" to ("0.2.5" to listOf(
        "77f594d82adf2a1baf5f8340324ef06b7f54b1b0725b7d9b4ce8367e270ad5a2",
        "99bbbe319e99ef092ab8677f644b9203b71f7bdc499dec75974c67effede5752",
        "752dc7ffa8337196a053d5a58a74c66a9f8b3cfe1afee122b97b5be7762b8a5d",
    )),
)
val wheels: File = rootDir.resolve("wheels")
fun sha256(file: File): String =
    MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }

val requirements: File = layout.buildDirectory.file("requirements-android.txt").get().asFile
requirements.parentFile.mkdirs()
requirements.writeText(
    // one block per package: "name==version ; marker" followed by its "--hash=" lines
    repo.resolve("requirements.lock").readText().replace("\\\n", " ").lines()
        .map { it.trim() }
        .filter { it.isNotEmpty() && !it.startsWith("#") }
        .joinToString("\n", postfix = "\n") { line ->
            val name = line.substringBefore("==")
            val version = line.substringAfter("==").substringBefore(" ")
            val hashes = when {
                name in chaquopyWheels -> chaquopyWheels.getValue(name).let { (v, h) -> v to h }
                name == "pydantic-core" -> version to (wheels.listFiles { f -> f.name.startsWith("pydantic_core-$version-") }
                    ?.map(::sha256)?.takeIf { it.isNotEmpty() }
                    ?: error("no pydantic-core $version in $wheels: run scripts/android-wheels.sh"))
                else -> null
            }
            hashes?.let { (v, h) -> "$name==$v " + h.joinToString(" ") { "--hash=sha256:$it" } } ?: line
        } +
        // the native libraries Chaquopy's builds of cffi and pyyaml bring along
        chaquopyLibraries.entries.joinToString("\n", postfix = "\n") { (name, pin) ->
            "$name==${pin.first} " + pin.second.joinToString(" ") { "--hash=sha256:$it" }
        },
)

// The texts of the app (notification, starting page, messages) come from the locale files of the web app, like all
// texts (#567): web/src/locales/<lang>/android.json -> res/values-<lang>/strings.xml, key android.<group>.<name> as
// R.string.<group>_<name> (web/scripts/check-i18n.mjs checks them). The default for other device languages is
// English, with German where English has no text yet. {placeholders} become %1$s, %2$s in the order of the German text.
val locales: File = repo.resolve("web/src/locales")
val generatedRes: File = layout.buildDirectory.dir("generated/locales").get().asFile
val androidTexts = tasks.register("androidTexts") {
    inputs.files(fileTree(locales) { include("*/android.json") })
    outputs.dir(generatedRes)
    doLast {
        fun flatten(node: Map<*, *>, prefix: String = ""): Map<String, String> = node.entries.flatMap { (key, value) ->
            if (value is Map<*, *>) flatten(value, "$prefix${key}_").entries.map { it.toPair() }
            else listOf("$prefix$key" to value.toString())
        }.toMap()
        fun texts(lang: String): Map<String, String> = locales.resolve("$lang/android.json").takeIf { it.isFile }
            ?.let { flatten(groovy.json.JsonSlurper().parse(it) as Map<*, *>) } ?: emptyMap()
        val german = texts("de")
        val order = german.mapValues { (_, text) -> Regex("""\{(\w+)\}""").findAll(text).map { it.groupValues[1] }.distinct().toList() }
        fun resource(text: String, names: List<String>): String {
            var out = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\\", "\\\\").replace("'", "\\'").replace("\"", "\\\"")
            // % only means something to getString with arguments: doubled only in texts with placeholders
            if (names.isNotEmpty()) out = out.replace("%", "%%")
            if (out.startsWith("@") || out.startsWith("?")) out = "\\" + out
            names.forEachIndexed { i, name -> out = out.replace("{$name}", "%${i + 1}\$s") }
            return out
        }
        fun write(folder: String, strings: Map<String, String>) {
            val file = generatedRes.resolve("$folder/strings.xml")
            file.parentFile.mkdirs()
            file.writeText(
                "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<!-- generated from web/src/locales/*/android.json -->\n<resources>\n" +
                    strings.toSortedMap().entries.joinToString("") { (name, text) ->
                        val names = order[name] ?: emptyList()
                        // without placeholders a % is text, also for aapt
                        val plain = if (names.isEmpty()) " formatted=\"false\"" else ""
                        "    <string name=\"$name\"$plain>${resource(text, names)}</string>\n"
                    } + "</resources>\n",
            )
        }
        generatedRes.deleteRecursively()
        write("values", german + texts("en").filterKeys { it in german })
        locales.listFiles { f -> f.isDirectory && f.resolve("android.json").isFile }!!.forEach { lang ->
            write("values-${lang.name}", texts(lang.name).filterKeys { it in german })
        }
    }
}
android.sourceSets.getByName("main").res.srcDir(generatedRes)
tasks.named("preBuild") { dependsOn(androidTexts) }

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
