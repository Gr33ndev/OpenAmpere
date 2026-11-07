// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
package io.github.gr33ndev.openampere

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

/** Starts the server after the tablet was switched on, and after the app was updated, without opening the app. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // an update replaced the app and closed it. If it was in front (a tablet on the wall, MainActivity notes that),
        // it comes back by itself where Android allows that (before Android 10); from Android 10 Android keeps an app
        // from coming to the front by itself, so a notification leads back
        if (intent.action == Intent.ACTION_MY_PACKAGE_REPLACED && Updater.finished(context) && MainActivity.wasInFront(context)) {
            val open = Intent(context, MainActivity::class.java)
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
                context.startActivity(Intent(open).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            } else {
                Updater.notify(
                    context, Updater.REOPEN_ID, context.getString(R.string.updates_reopenTitle, Updater.installedVersion(context)),
                    context.getString(R.string.updates_reopenText), open,
                )
            }
        }
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            ServerService.start(context)
        }
    }
}
