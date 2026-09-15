package com.example

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Color as AndroidColor
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.webkit.ConsoleMessage
import android.webkit.RenderProcessGoneDetail
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import com.example.data.OsintRepository
import com.example.ui.theme.MyApplicationTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MyApplicationTheme {
                Scaffold(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color(0xFF070A0F))
                ) { innerPadding ->
                    OsintAppScreen(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(innerPadding)
                    )
                }
            }
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun OsintAppScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val repository = remember { OsintRepository(context) }
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    var filePathCallback by remember { mutableStateOf<ValueCallback<Array<Uri>>?>(null) }

    val fileChooserLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.GetMultipleContents()
    ) { uris ->
        filePathCallback?.onReceiveValue(uris.toTypedArray())
        filePathCallback = null
    }

    // Background periodic worker: preloads data, scrapes immediately, then refreshes every 1 hour (3600 seconds)
    LaunchedEffect(Unit) {
        withContext(Dispatchers.IO) {
            repository.preloadBaselineIncidents()
        }
        webViewRef?.post {
            webViewRef?.evaluateJavascript(
                "if(window.onAndroidDataUpdated) window.onAndroidDataUpdated();",
                null
            )
        }

        withContext(Dispatchers.IO) {
            try {
                repository.refreshRssFeeds()
            } catch (_: Exception) {}
        }
        webViewRef?.post {
            webViewRef?.evaluateJavascript(
                "if(window.onAndroidDataUpdated) window.onAndroidDataUpdated();",
                null
            )
        }

        while (isActive) {
            delay(3600_000L) // Refresh every 1 hour
            withContext(Dispatchers.IO) {
                try {
                    repository.refreshRssFeeds()
                } catch (_: Exception) {}
            }
            webViewRef?.post {
                webViewRef?.evaluateJavascript(
                    "if(window.onAndroidDataUpdated) window.onAndroidDataUpdated();",
                    null
                )
            }
        }
    }

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(Color(0xFF070A0F))
    ) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { ctx ->
                WebView(ctx).apply {
                    layoutParams = ViewGroup.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT
                    )
                    setBackgroundColor(AndroidColor.parseColor("#070A0F"))
                    setLayerType(View.LAYER_TYPE_SOFTWARE, null)
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.allowFileAccess = true
                    settings.allowContentAccess = true
                    settings.useWideViewPort = true
                    settings.loadWithOverviewMode = true

                    addJavascriptInterface(
                        OsintJsInterface(ctx, repository) {
                            post {
                                evaluateJavascript(
                                    "if(window.onAndroidDataUpdated) window.onAndroidDataUpdated();",
                                    null
                                )
                            }
                        },
                        "AndroidOSINT"
                    )

                    webChromeClient = object : WebChromeClient() {
                        override fun onConsoleMessage(consoleMessage: ConsoleMessage?): Boolean {
                            Log.d("OSINT_LOG", "${consoleMessage?.message()} [${consoleMessage?.sourceId()}:${consoleMessage?.lineNumber()}]")
                            return true
                        }

                        override fun onShowFileChooser(
                            webView: WebView?,
                            filePathCallbackParam: ValueCallback<Array<Uri>>?,
                            fileChooserParams: FileChooserParams?
                        ): Boolean {
                            filePathCallback?.onReceiveValue(null)
                            filePathCallback = filePathCallbackParam
                            fileChooserLauncher.launch("image/*")
                            return true
                        }
                    }

                    webViewClient = object : WebViewClient() {
                        override fun onRenderProcessGone(
                            view: WebView?,
                            detail: RenderProcessGoneDetail?
                        ): Boolean {
                            Log.w("OSINT_WEBVIEW", "Render process gone; recovery handled. Crashed: ${detail?.didCrash()}")
                            view?.destroy()
                            return true
                        }

                        override fun shouldOverrideUrlLoading(
                            view: WebView?,
                            request: WebResourceRequest?
                        ): Boolean {
                            val url = request?.url?.toString() ?: return false
                            if (url.startsWith("file:") || url.contains("android_asset")) {
                                return false
                            }
                            return try {
                                val intent = Intent(Intent.ACTION_VIEW, request.url)
                                ctx.startActivity(intent)
                                true
                            } catch (e: Exception) {
                                false
                            }
                        }
                    }

                    loadUrl("file:///android_asset/osint/index.html")
                    webViewRef = this
                }
            },
            update = {
                webViewRef = it
            }
        )
    }

    DisposableEffect(Unit) {
        onDispose {
            webViewRef?.destroy()
        }
    }
}

@Composable
fun Greeting(name: String, modifier: Modifier = Modifier) {
    androidx.compose.material3.Text(text = "Hello $name!", modifier = modifier)
}

