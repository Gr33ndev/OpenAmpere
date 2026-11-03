// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
package io.github.gr33ndev.openampere

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.text.Html
import android.util.Base64
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * The web app of the server on this tablet in a WebView. Links to other sites open in the browser; files the web app
 * offers (backup, CSV, diagnostics report) are saved where the owner picks.
 */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private val main = Handler(Looper.getMainLooper())
    private var upload: ValueCallback<Array<Uri>>? = null
    private var pendingSave: ((Uri) -> Unit)? = null
    private var waiting = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ServerService.start(this)
        // debug builds only: the web app can be inspected from Chrome on the computer (chrome://inspect)
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) WebView.setWebContentsDebuggingEnabled(true)
        web = WebView(this)
        setContentView(web)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.addJavascriptInterface(Bridge(), "OpenAmpereAndroid")
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                if (isServer(request.url)) return false
                openOutside(request.url)
                return true
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                // the server is starting (again): wait for it instead of showing the WebView's error page
                if (request.isForMainFrame) waitForServer()
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams,
            ): Boolean {
                upload?.onReceiveValue(null)
                upload = callback
                return try {
                    startActivityForResult(params.createIntent(), REQUEST_UPLOAD)
                    true
                } catch (e: ActivityNotFoundException) {
                    upload = null
                    false
                }
            }
        }
        web.setDownloadListener { url, _, disposition, mime, _ -> download(url, disposition, mime) }
        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) waitForServer()
        askOnce()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else @Suppress("DEPRECATION") super.onBackPressed()
    }

    private fun isServer(url: Uri) = url.host == HOST && url.port == ServerService.PORT

    private fun openOutside(url: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, url))
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, R.string.no_browser, Toast.LENGTH_LONG).show()
        }
    }

    /** Shows "OpenAmpere is starting" until the server answers, then the web app. */
    private fun waitForServer() {
        if (waiting) return
        waiting = true
        val text = Html.escapeHtml(getString(R.string.starting))
        web.loadDataWithBaseURL(null, STARTING_PAGE.replace("TEXT", text), "text/html", "utf-8", null)
        thread(name = "wait-for-server") {
            while (!answers()) Thread.sleep(1_000)
            main.post {
                waiting = false
                web.loadUrl(SERVER)
            }
        }
    }

    private fun answers(): Boolean = try {
        (URL("${SERVER}api/health").openConnection() as HttpURLConnection).run {
            connectTimeout = 2_000
            readTimeout = 2_000
            try {
                responseCode == 200
            } finally {
                disconnect()
            }
        }
    } catch (e: Exception) {
        false
    }

    /** A file from the server is streamed from it with the login cookie; one made in the web app (blob:) comes
     *  through the bridge. */
    private fun download(url: String, disposition: String?, mime: String?) {
        val name = URLUtil.guessFileName(url, disposition, mime)
        if (url.startsWith("blob:")) {
            val script = """
                fetch(${quote(url)}).then(r => r.blob()).then(b => new Promise(done => {
                  const reader = new FileReader();
                  reader.onload = () => done(reader.result);
                  reader.readAsDataURL(b);
                })).then(data => OpenAmpereAndroid.save(data, ${quote(name)}, ${quote(mime ?: "")}));
            """.trimIndent()
            web.evaluateJavascript(script, null)
            return
        }
        val uri = Uri.parse(url)
        if (!isServer(uri)) return openOutside(uri)
        save(name, mime) { target ->
            val cookie = CookieManager.getInstance().getCookie(url)
            thread(name = "download") {
                val ok = try {
                    val connection = URL(url).openConnection() as HttpURLConnection
                    cookie?.let { connection.setRequestProperty("Cookie", it) }
                    connection.inputStream.use { input ->
                        contentResolver.openOutputStream(target)!!.use { input.copyTo(it) }
                    }
                    true
                } catch (e: Exception) {
                    false
                }
                main.post { toast(if (ok) R.string.saved else R.string.save_failed) }
            }
        }
    }

    /** Asks where to save, then writes there. */
    private fun save(name: String, mime: String?, write: (Uri) -> Unit) {
        pendingSave = write
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType(mime?.takeIf { it.isNotEmpty() } ?: "application/octet-stream")
            .putExtra(Intent.EXTRA_TITLE, name)
        try {
            startActivityForResult(intent, REQUEST_SAVE)
        } catch (e: ActivityNotFoundException) {
            pendingSave = null
            toast(R.string.save_failed)
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            REQUEST_UPLOAD -> {
                upload?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
                upload = null
            }
            REQUEST_SAVE -> {
                val target = data?.data
                if (resultCode == RESULT_OK && target != null) pendingSave?.invoke(target)
                pendingSave = null
            }
        }
    }

    private inner class Bridge {
        @JavascriptInterface
        fun save(dataUrl: String, name: String, mime: String) {
            val bytes = Base64.decode(dataUrl.substringAfter(","), Base64.DEFAULT)
            main.post {
                save(name, mime) { target ->
                    val ok = try {
                        contentResolver.openOutputStream(target)!!.use { it.write(bytes) }
                        true
                    } catch (e: Exception) {
                        false
                    }
                    toast(if (ok) R.string.saved else R.string.save_failed)
                }
            }
        }
    }

    /** Once: notifications (Android 13), and to leave OpenAmpere out of the battery optimisation, without which
     *  Android puts the server to sleep. */
    private fun askOnce() {
        val prefs = getSharedPreferences("app", Context.MODE_PRIVATE)
        if (prefs.getBoolean("asked", false)) return
        prefs.edit().putBoolean("asked", true).apply()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQUEST_NOTIFICATIONS)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val power = getSystemService(Context.POWER_SERVICE) as PowerManager
            if (!power.isIgnoringBatteryOptimizations(packageName)) {
                try {
                    @SuppressLint("BatteryLife") // the tablet is the server: it has to run around the clock
                    val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
                    startActivity(intent)
                } catch (e: ActivityNotFoundException) {
                    // some vendors have no such dialog; the guide explains where to switch it off
                }
            }
        }
    }

    private fun toast(text: Int) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()

    private fun quote(text: String) = "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"

    companion object {
        private const val HOST = "127.0.0.1"
        private val SERVER = "http://$HOST:${ServerService.PORT}/"
        private const val REQUEST_UPLOAD = 1
        private const val REQUEST_SAVE = 2
        private const val REQUEST_NOTIFICATIONS = 3
        private const val STARTING_PAGE = """<!doctype html><meta name="viewport" content="width=device-width">
<body style="margin:0;display:flex;align-items:center;justify-content:center;height:100vh;background:#0f172a;
color:#e2e8f0;font:18px sans-serif;text-align:center;padding:24px;box-sizing:border-box">TEXT</body>"""
    }
}
