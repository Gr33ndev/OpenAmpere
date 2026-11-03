// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
package io.github.gr33ndev.openampere

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import kotlin.concurrent.thread

/**
 * The update helper of the app (#567), in the server's process: what scripts/updater.sh is for the Docker way. The
 * server (updates.py) sees it through `update/updater-alive`, asks it through the request file and shows its
 * `update/status.json`. It downloads the APK of the asked version only from the release of Gr33ndev/OpenAmpere,
 * checks it against the release's SHA256SUMS and its certificate against the installed app's, and hands it to
 * Android's package installer. Android checks the signature once more and installs only an update signed with the
 * same key (docs/android-signing.md).
 */
class Updater(private val context: Context) {
    private val folder = File(context.filesDir, "update")

    // debug builds only (a release build ignores it): a local copy of GitHub for trying an update in the emulator,
    // update/test-github with e.g. http://127.0.0.1:8000 (adb reverse; android/README.md)
    private val github: String
        get() = File(folder, "test-github").takeIf {
            context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0 && it.isFile
        }?.readText()?.trim() ?: ""

    fun start() {
        folder.mkdirs()
        // the sign of life in a thread of its own, as scripts/updater.sh: a long download must not make the server
        // think the updater is gone (updates.UPDATER_ALIVE_S)
        thread(name = "openampere-updater-alive", isDaemon = true) {
            while (true) {
                try {
                    File(folder, "updater-alive").writeText((System.currentTimeMillis() / 1000).toString())
                } catch (e: Exception) {
                    Log.e(TAG, "updater-alive", e)
                }
                Thread.sleep(POLL_MS)
            }
        }
        thread(name = "openampere-updater", isDaemon = true) {
            while (true) {
                try {
                    request()?.let(::update)
                } catch (e: Exception) {
                    Log.e(TAG, "updater", e)
                }
                Thread.sleep(POLL_MS)
            }
        }
    }

    /** The version the server asks for (updates.request_file: the inbox, else the folder), once. */
    private fun request(): String? {
        val file = listOf(File(folder, "inbox/request"), File(folder, "request")).firstOrNull { it.isFile } ?: return null
        val wanted = try {
            JSONObject(file.readText()).optString("to")
        } catch (e: Exception) {
            ""
        }
        file.delete()
        return wanted.takeIf { STABLE.matches(it) }
    }

    private fun update(version: String) {
        if (!newer(version)) return status("failed", text(R.string.updates_notNewer, version))
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !context.packageManager.canRequestPackageInstalls()) {
            // without notifications (Android 13 lets the owner decline them) the text names the setting itself
            if (!notificationsAllowed(context)) return status("failed", text(R.string.updates_allowNoNotifications))
            notify(
                ALLOW_ID, text(R.string.updates_allowTitle), text(R.string.updates_allowText),
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}")),
            )
            return status("failed", text(R.string.updates_notAllowed))
        }
        status("pulling", text(R.string.updates_downloading, version))
        val apkName = "openampere-$version.apk"
        val apk = File(context.cacheDir, apkName)
        val test = github
        val api = if (test.isEmpty()) API else "$test/repos/Gr33ndev/OpenAmpere"
        val downloads = if (test.isEmpty()) DOWNLOADS else "$test/Gr33ndev/OpenAmpere/releases/download/"
        try {
            val release = JSONObject(String(fetch("$api/releases/tags/v$version", MAX_RELEASE_BYTES)))
            val assets = release.getJSONArray("assets")
            val urls = (0 until assets.length()).associate { i ->
                assets.getJSONObject(i).let { it.getString("name") to it.getString("browser_download_url") }
            }.filterValues { it.startsWith(downloads) } // never another repository or site
            val apkUrl = urls[apkName] ?: return status("failed", text(R.string.updates_noFile, version))
            val sums = String(fetch(urls["SHA256SUMS"] ?: return status("failed", text(R.string.updates_noFile, version)),
                MAX_RELEASE_BYTES))
            val expected = sums.lines().map { it.trim().split(Regex("\\s+\\*?")) }
                .firstOrNull { it.size == 2 && it[1] == apkName }?.get(0)?.lowercase()
                ?: return status("failed", text(R.string.updates_noFile, version))
            val found = download(apkUrl, apk)
            status("verifying", text(R.string.updates_verifying, version))
            if (found != expected) return status("failed", text(R.string.updates_checksumWrong))
        } catch (e: Exception) {
            Log.w(TAG, "download of $version failed", e)
            apk.delete()
            return status("failed", text(R.string.updates_downloadFailed))
        }
        if (!sameSigner(apk)) {
            apk.delete()
            return status("failed", text(R.string.updates_signatureWrong))
        }
        status("restarting", text(R.string.updates_installing, version))
        File(folder, "installing").writeText(version) // BootReceiver says "done" after the new version started
        try {
            install(apk, version)
        } catch (e: Exception) { // e.g. installing apps was switched off meanwhile, or no space left
            Log.w(TAG, "install of $version failed", e)
            apk.delete()
            failed(context, text(R.string.updates_installFailed, e.message ?: e.javaClass.simpleName))
        }
    }

    /** Compared by Android's version number, as Android does (android/app/build.gradle.kts): a final version is
     *  newer than its release candidates. [version] is a final version (STABLE). */
    private fun newer(version: String): Boolean {
        val (major, minor, patch) = version.split(".").map { it.toLong() }
        val code = (major * 1_000_000 + minor * 1_000 + patch) * 10 + 9
        val info = context.packageManager.getPackageInfo(context.packageName, 0)
        @Suppress("DEPRECATION")
        val installed = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) info.longVersionCode else info.versionCode.toLong()
        return code > installed
    }

    /** The downloaded app is signed with the installed app's key, or with a key it was rotated to from that one
     *  (Android 9 and later, docs/android-signing.md): anything else Android would refuse anyway, but with a message
     *  nobody understands. */
    @Suppress("DEPRECATION")
    private fun sameSigner(apk: File): Boolean {
        val pm = context.packageManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            val flags = PackageManager.GET_SIGNING_CERTIFICATES
            val installed = pm.getPackageInfo(context.packageName, flags).signingInfo?.apkContentsSigners.orEmpty()
                .map { it.toCharsString() }.toSet()
            val downloaded = pm.getPackageArchiveInfo(apk.path, flags)
            val signing = downloaded?.signingInfo ?: return false
            if (downloaded.packageName != context.packageName || installed.isEmpty()) return false
            val current = signing.apkContentsSigners.map { it.toCharsString() }.toSet()
            val history = if (signing.hasPastSigningCertificates()) {
                signing.signingCertificateHistory.map { it.toCharsString() }.toSet()
            } else {
                emptySet()
            }
            return current == installed || (installed.size == 1 && installed.first() in history)
        }
        val flags = PackageManager.GET_SIGNATURES
        val installed = pm.getPackageInfo(context.packageName, flags).signatures.orEmpty().map { it.toCharsString() }.toSet()
        val downloaded = pm.getPackageArchiveInfo(apk.path, flags)
        return downloaded?.packageName == context.packageName && installed.isNotEmpty() &&
            downloaded.signatures.orEmpty().map { it.toCharsString() }.toSet() == installed
    }

    private fun install(apk: File, version: String) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            // from Android 12 an app may update itself without asking, once it installed the app before
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            apk.inputStream().use { input ->
                session.openWrite("openampere.apk", 0, apk.length()).use { output ->
                    input.copyTo(output)
                    session.fsync(output)
                }
            }
            val result = Intent(context, InstallReceiver::class.java).putExtra(InstallReceiver.VERSION, version)
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
            session.commit(PendingIntent.getBroadcast(context, id, result, flags).intentSender)
        }
        apk.delete()
    }

    private fun fetch(url: String, limit: Int): ByteArray = connect(url).inputStream.use { input ->
        val bytes = input.readNBytesCompat(limit + 1)
        if (bytes.size > limit) throw IllegalStateException("answer too large")
        bytes
    }

    /** Downloads into [target] and returns its SHA-256. */
    private fun download(url: String, target: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        var size = 0L
        connect(url).inputStream.use { input ->
            target.outputStream().use { output ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val n = input.read(buffer)
                    if (n < 0) break
                    size += n
                    if (size > MAX_APK_BYTES) throw IllegalStateException("file too large")
                    digest.update(buffer, 0, n)
                    output.write(buffer, 0, n)
                }
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun connect(url: String): HttpURLConnection = (URL(url).openConnection() as HttpURLConnection).apply {
        connectTimeout = 15_000
        readTimeout = 60_000
        setRequestProperty("User-Agent", "OpenAmpere")
        setRequestProperty("Accept", "application/vnd.github+json, application/octet-stream")
        if (responseCode !in 200..299) throw IllegalStateException("HTTP $responseCode for $url")
    }

    private fun status(state: String, message: String) = writeStatus(context, state, message)

    private fun text(id: Int, vararg args: String) = context.getString(id, *args)

    private fun notify(id: Int, title: String, body: String, intent: Intent) = Companion.notify(context, id, title, body, intent)

    companion object {
        private const val TAG = "OpenAmpere"
        private const val API = "https://api.github.com/repos/Gr33ndev/OpenAmpere"
        private const val DOWNLOADS = "https://github.com/Gr33ndev/OpenAmpere/releases/download/"
        private val STABLE = Regex("""\d{1,6}\.\d{1,6}\.\d{1,6}""") // as updates.parse_version: no pre-releases
        private const val POLL_MS = 5_000L // updates.UPDATER_ALIVE_S is 60
        private const val MAX_RELEASE_BYTES = 2 * 1024 * 1024
        private const val MAX_APK_BYTES = 300L * 1024 * 1024
        private const val CHANNEL = "updates"
        const val CONFIRM_ID = 3
        private const val ALLOW_ID = 4

        /** update/status.json, as scripts/updater.sh writes it: the app's update dialog shows its message. */
        fun writeStatus(context: Context, state: String, message: String) {
            val folder = File(context.filesDir, "update").apply { mkdirs() }
            val json = JSONObject().put("ts", System.currentTimeMillis() / 1000).put("state", state).put("message", message)
            File(folder, "status.json.tmp").apply { writeText(json.toString()) }.renameTo(File(folder, "status.json"))
        }

        /** The update failed: said in the dialog, and no "done" for it after a later update by hand. */
        fun failed(context: Context, message: String) {
            File(context.filesDir, "update/installing").delete()
            writeStatus(context, "failed", message)
        }

        fun installedVersion(context: Context): String =
            context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: ""

        fun notificationsAllowed(context: Context) =
            (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).areNotificationsEnabled()

        /** The new version started (BootReceiver, MY_PACKAGE_REPLACED): the update the app asked for is done.
         *  Returns whether there was one. */
        fun finished(context: Context): Boolean {
            val marker = File(context.filesDir, "update/installing")
            if (!marker.isFile) return false
            val version = marker.readText().trim()
            marker.delete()
            writeStatus(context, "done", context.getString(R.string.updates_done, version))
            (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(CONFIRM_ID)
            return true
        }

        fun notify(context: Context, id: Int, title: String, body: String, intent: Intent) {
            val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val open = PendingIntent.getActivity(
                context, id, intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                manager.createNotificationChannel(NotificationChannel(
                    CHANNEL, context.getString(R.string.updates_channel), NotificationManager.IMPORTANCE_HIGH,
                ))
                Notification.Builder(context, CHANNEL)
            } else {
                @Suppress("DEPRECATION")
                Notification.Builder(context).setPriority(Notification.PRIORITY_HIGH)
            }
            manager.notify(id, builder.setSmallIcon(R.drawable.ic_notification).setContentTitle(title)
                .setContentText(body).setStyle(Notification.BigTextStyle().bigText(body))
                .setContentIntent(open).setAutoCancel(true).build())
        }
    }
}

/** InputStream.readNBytes exists only from Android 13. */
private fun java.io.InputStream.readNBytesCompat(limit: Int): ByteArray {
    val out = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(16 * 1024)
    while (out.size() < limit) {
        val n = read(buffer, 0, minOf(buffer.size, limit - out.size()))
        if (n < 0) break
        out.write(buffer, 0, n)
    }
    return out.toByteArray()
}
