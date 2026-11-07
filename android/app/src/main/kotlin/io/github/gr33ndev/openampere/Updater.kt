// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
package io.github.gr33ndev.openampere

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import org.json.JSONObject
import java.io.File

/**
 * What is left of the app's own updater (#567). Up to 0.40 the app downloaded and installed new versions itself; Google
 * Play Protect classifies every app from outside the Play Store that may install packages as harmful (#610), so the
 * app no longer can. New versions come as a download like the first install; the server sees no updater and says so
 * (updates.py). What stays is the end of an update a 0.40 app started: it left the marker `update/installing`, and
 * the new version reports "done" and comes back to the front (BootReceiver).
 */
object Updater {
    const val REOPEN_ID = 3
    private const val CHANNEL = "updates"

    /** The new version started (BootReceiver, MY_PACKAGE_REPLACED) after an update the old app started: returns
     *  whether there was one, and says "done" in the update dialog of the web app. */
    fun finished(context: Context): Boolean {
        val marker = File(context.filesDir, "update/installing")
        if (!marker.isFile) return false
        marker.delete()
        writeStatus(context, "done", context.getString(R.string.updates_done, installedVersion(context)))
        // the old app's "update ready" notification, if Android asked before installing (same id)
        (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(REOPEN_ID)
        return true
    }

    fun installedVersion(context: Context): String =
        context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: ""

    /** update/status.json, as scripts/updater.sh writes it: the app's update dialog shows its message. */
    private fun writeStatus(context: Context, state: String, message: String) {
        val folder = File(context.filesDir, "update").apply { mkdirs() }
        val json = JSONObject().put("ts", System.currentTimeMillis() / 1000).put("state", state).put("message", message)
        File(folder, "status.json.tmp").apply { writeText(json.toString()) }.renameTo(File(folder, "status.json"))
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
