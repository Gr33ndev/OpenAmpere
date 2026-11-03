// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
package io.github.gr33ndev.openampere

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build

/** What Android's package installer did with an update the [Updater] handed over. */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val version = intent.getStringExtra(VERSION) ?: return
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            // Android asks the owner first (the first update after installing from the browser, or before Android
            // 12): from the background only a notification may lead there
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirm = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_INTENT)
                } ?: return
                // while the app is in front (the owner pressed "update" on the tablet) Android lets it open its
                // dialog right away; otherwise this does nothing and the notification leads there
                try {
                    context.startActivity(Intent(confirm).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                } catch (e: Exception) {
                    // not allowed from the background: the notification is the way
                }
                if (Updater.notificationsAllowed(context)) {
                    Updater.notify(
                        context, Updater.CONFIRM_ID, context.getString(R.string.updates_confirmTitle, version),
                        context.getString(R.string.updates_confirmText), confirm,
                    )
                    Updater.writeStatus(context, "confirm", context.getString(R.string.updates_confirm, version))
                } else {
                    Updater.writeStatus(context, "confirm", context.getString(R.string.updates_confirmNoNotifications))
                }
            }
            PackageInstaller.STATUS_SUCCESS -> Updater.finished(context)
            else -> {
                val reason = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "?"
                Updater.failed(context, context.getString(R.string.updates_installFailed, reason))
            }
        }
    }

    companion object {
        const val VERSION = "version"
    }
}
