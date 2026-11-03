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

/**
 * The server of OpenAmpere (#567), around the clock: a foreground service with a permanent notification, so Android
 * keeps it running, with the CPU and Wi-Fi kept awake. The Python server runs in a thread of this process; when it
 * stops (a crash, or a restart it asked for, e.g. after a restored backup) the whole process is started again a few
 * seconds later, as Docker restarts the container.
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
        try {
            if (!Python.isStarted()) Python.start(AndroidPlatform(this))
            Python.getInstance().getModule("openampere_app").callAttr("run", filesDir.absolutePath)
            Log.i(TAG, "server stopped, starting it again")
        } catch (e: Throwable) {
            Log.e(TAG, "server failed, starting it again", e)
        }
        restart()
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
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            alarms.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pending)
        } else {
            alarms.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pending)
        }
        Process.killProcess(Process.myPid())
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

    private fun notification(): Notification {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, getString(R.string.channel_server), NotificationManager.IMPORTANCE_LOW),
            )
            Notification.Builder(this, CHANNEL)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this).setPriority(Notification.PRIORITY_LOW)
        }
        return builder
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.notification_running, PORT))
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    companion object {
        const val PORT = 8080 // openampere_app.PORT
        private const val TAG = "OpenAmpere"
        private const val CHANNEL = "server"
        private const val NOTIFICATION_ID = 1
        private const val RESTART_DELAY_MS = 5_000L

        @Volatile
        private var running = false

        fun start(context: Context) {
            val intent = Intent(context, ServerService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
