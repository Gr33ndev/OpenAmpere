// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
package io.github.gr33ndev.openampere

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.util.Locale

/**
 * The server of OpenAmpere (#567), around the clock: a foreground service with a permanent notification, so Android
 * keeps it running, with the CPU and Wi-Fi kept awake. It runs in a process of its own (":server" in the manifest),
 * the Python server in a thread of it. When the server stops (a crash, or a restart it asked for, e.g. after a
 * restored backup) only this process is started again a few seconds later, as Docker restarts the container; the
 * WebView keeps waiting for it. A server that cannot start at all (another app on its port, a database of a newer
 * version) is not started again and again: after a few quick failures in a row it stops and says why.
 */
class ServerService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var multicastLock: WifiManager.MulticastLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val notification = notification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        keepAwake()
        if (!running) {
            running = true
            Thread(::serve, "openampere").start()
            Updater(this).start()
        }
    }

    // after Android stopped the process (low memory), it creates the service again
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        wakeLock?.takeIf { it.isHeld }?.release()
        wifiLock?.takeIf { it.isHeld }?.release()
        multicastLock?.takeIf { it.isHeld }?.release()
        super.onDestroy()
    }

    private fun serve() {
        val started = SystemClock.elapsedRealtime()
        // running for a while counts as a good start: earlier failures no longer add up with later ones
        Thread({
            Thread.sleep(STABLE_MS)
            failuresFile(this).writeText("0")
        }, "openampere-stable").apply { isDaemon = true }.start()
        val reason: String? = try {
            if (!Python.isStarted()) Python.start(AndroidPlatform(this))
            Python.getInstance().getModule("openampere_app")
                .callAttr("run", filesDir.absolutePath, Locale.getDefault().language)?.toString()
        } catch (e: Throwable) {
            Log.e(TAG, "server failed", e)
            e.message ?: e.toString()
        }
        // a failure soon after the start counts; a server that ran for a while and then stopped starts with a clean
        // slate, as does one that stopped as asked
        val quick = SystemClock.elapsedRealtime() - started < STABLE_MS
        val failures = if (reason != null && quick) failures(this) + 1 else 0
        failuresFile(this).writeText(failures.toString())
        if (reason != null && failures >= MAX_FAILURES) {
            Log.e(TAG, "server failed $failures times in a row, not starting it again: $reason")
            giveUp(reason)
        } else {
            Log.i(TAG, "server stopped (${reason ?: "as asked"}), starting it again")
            restart()
        }
    }

    /** A new process in a few seconds: a Python interpreter cannot be started a second time in this one. */
    private fun restart() {
        val intent = Intent(this, ServerService::class.java)
        val pending = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            PendingIntent.getForegroundService(this, 0, intent, PendingIntent.FLAG_IMMUTABLE)
        } else {
            PendingIntent.getService(this, 0, intent, PendingIntent.FLAG_IMMUTABLE)
        }
        val alarms = getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val at = SystemClock.elapsedRealtime() + RESTART_DELAY_MS
        // an exact alarm: from Android 12, only an exact one may start a foreground service from the background
        // (besides an app the owner took out of the battery optimisation), and Doze would delay an inexact one
        val exact = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarms.canScheduleExactAlarms()
        when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && exact ->
                alarms.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pending)
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ->
                alarms.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pending)
            else -> alarms.setExact(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pending)
        }
        end()
    }

    /** Ends this process. Stopped first: otherwise Android would start the sticky service again at once, besides the
     *  alarm (or after giving up). A crash still gets that restart from Android. */
    private fun end() {
        stopSelf()
        Process.killProcess(Process.myPid())
    }

    /** Stops for good until the app is opened again or the tablet starts: the reason in a notification and on the
     *  starting page of the app. */
    private fun giveUp(reason: String) {
        stoppedFile(this).writeText(reason)
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val text = "$reason\n\n${getString(R.string.shell_stoppedHint)}"
        manager.notify(
            STOPPED_ID,
            builder(manager)
                .setContentTitle(getString(R.string.shell_stopped))
                .setContentText(reason)
                .setStyle(Notification.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .build(),
        )
        end()
    }

    private fun keepAwake() {
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "OpenAmpere:server").apply {
            setReferenceCounted(false)
            acquire()
        }
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION") // the replacement (low latency) needs Android 10 and the screen on
        wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "OpenAmpere:server").apply {
            setReferenceCounted(false)
            acquire()
        }
        multicastLock = wifi.createMulticastLock("OpenAmpere:search").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun builder(manager: NotificationManager): Notification.Builder {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, getString(R.string.shell_channelServer), NotificationManager.IMPORTANCE_LOW),
            )
            Notification.Builder(this, CHANNEL)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this).setPriority(Notification.PRIORITY_LOW)
        }
        return builder.setSmallIcon(R.drawable.ic_notification).setContentIntent(open)
    }

    private fun notification(): Notification {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.cancel(STOPPED_ID)
        return builder(manager)
            .setContentTitle(APP_NAME)
            .setContentText(getString(R.string.shell_running, PORT.toString()))
            .setOngoing(true)
            .build()
    }

    companion object {
        const val PORT = 8080 // openampere_app.PORT
        const val APP_NAME = "OpenAmpere"
        private const val TAG = "OpenAmpere"
        private const val CHANNEL = "server"
        private const val NOTIFICATION_ID = 1
        private const val STOPPED_ID = 2
        private const val RESTART_DELAY_MS = 5_000L
        private const val STABLE_MS = 60_000L // ran this long: a later stop is not a failure to start
        private const val MAX_FAILURES = 3

        @Volatile
        private var running = false

        private fun failuresFile(context: Context) = File(context.filesDir, "server-failures")
        private fun stoppedFile(context: Context) = File(context.filesDir, "server-stopped")

        private fun failures(context: Context): Int = failuresFile(context).takeIf { it.isFile }?.readText()?.trim()
            ?.toIntOrNull() ?: 0

        /** Why the server gave up, or null while it runs or tries to. */
        fun stopReason(context: Context): String? = stoppedFile(context).takeIf { it.isFile }?.readText()

        /** Starts the server; one that gave up gets a new attempt (the app was opened, the tablet started). */
        fun start(context: Context) {
            stoppedFile(context).delete()
            failuresFile(context).delete()
            val intent = Intent(context, ServerService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
