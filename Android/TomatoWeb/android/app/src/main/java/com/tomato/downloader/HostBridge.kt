package com.tomato.downloader

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.content.ContextCompat
import com.tomato.downloader.core.BookStore
import com.tomato.downloader.core.ConsentStore
import com.tomato.downloader.core.CustomWebStore
import com.tomato.downloader.core.PasswordStore
import com.tomato.downloader.core.PortStore
import com.tomato.downloader.core.ServerManager
import com.tomato.downloader.core.loadBook
import com.tomato.downloader.service.WebServerService
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Flutter ↔ 原生桥：把原 Kotlin 版的核心能力（ServerManager / 各 Store / BookStore）
 * 通过 MethodChannel + EventChannel 暴露给 Flutter UI。
 *
 * 通道：
 *  - MethodChannel "tomato/host"：命令式调用（快照、安装、启停、设置、书籍…）
 *  - EventChannel "tomato/logs"：终端日志增量（单行字符串；"\u0000CLEAR" 表示清空）
 *  - EventChannel "tomato/state"：状态快照（与 getSnapshot 同结构），任一相关状态变化即推送
 */
class HostBridge(private val activity: Activity) {

    private val appContext: Context = activity.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private var stateSink: EventChannel.EventSink? = null
    private var logSink: EventChannel.EventSink? = null
    private var lastLogCount = 0

    private var pendingPickResult: MethodChannel.Result? = null
    private var pendingExportResult: MethodChannel.Result? = null
    private var pendingExportPath: String? = null

    companion object {
        private const val REQ_PICK_CUSTOM_WEB = 4101
        private const val REQ_EXPORT_BOOK = 4102
        const val LOG_CLEAR_MARKER = "\u0000CLEAR"
    }

    fun register(engine: FlutterEngine) {
        val messenger = engine.dartExecutor.binaryMessenger

        EventChannel(messenger, "tomato/state").setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                stateSink = events
                emitSnapshot()
            }
            override fun onCancel(arguments: Any?) { stateSink = null }
        })

        EventChannel(messenger, "tomato/logs").setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                logSink = events
                // 新监听者先收到全量历史
                lastLogCount = 0
                pushNewLogs(ServerManager.logLines.value)
            }
            override fun onCancel(arguments: Any?) { logSink = null }
        })

        MethodChannel(messenger, "tomato/host").setMethodCallHandler { call, result ->
            when (call.method) {
                "getSnapshot" -> result.success(snapshot())
                "installWeb" -> {
                    val force = call.argument<Boolean>("force") ?: false
                    scope.launch { ServerManager.install(appContext, force) }
                    result.success(true)
                }
                "showToast" -> {
                    val msg = call.argument<String>("message") ?: ""
                    if (msg.isNotEmpty()) {
                        android.widget.Toast.makeText(
                            appContext, msg, android.widget.Toast.LENGTH_LONG
                        ).show()
                    }
                    result.success(true)
                }
                "checkWebUpdate" -> scope.launch {
                    try {
                        val info = withContext(Dispatchers.IO) {
                            ServerManager.checkUpdate(appContext)
                        }
                        result.success(info)
                    } catch (e: Exception) {
                        result.error("check_failed", e.message, null)
                    }
                }
                "startServer" -> result.success(startServerViaService())
                "stopServer" -> { stopServerViaService(); result.success(true) }
                "setPort" -> {
                    val p = call.argument<Int>("port")
                    if (p == null) { result.success(false) } else {
                        val ok = PortStore.setPort(appContext, p)
                        if (ok) restartIfRunning()
                        result.success(ok)
                    }
                }
                "resetPort" -> { PortStore.resetDefault(appContext); restartIfRunning(); result.success(true) }
                "setPassword" -> {
                    PasswordStore.setPassword(appContext, call.argument<String>("password"))
                    restartIfRunning()
                    result.success(true)
                }
                "markConsent" -> { ConsentStore.markAgreed(appContext); result.success(true) }
                "refreshPublicIp" -> { ServerManager.refreshPublicIp(); result.success(true) }
                "clearLogs" -> { ServerManager.clearLogs(); result.success(true) }
                "pickCustomWeb" -> {
                    if (pendingPickResult != null) { result.success(false) } else {
                        pendingPickResult = result
                        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                            addCategory(Intent.CATEGORY_OPENABLE)
                            type = "*/*"
                        }
                        try { activity.startActivityForResult(intent, REQ_PICK_CUSTOM_WEB) }
                        catch (e: Exception) { pendingPickResult = null; result.success(false) }
                    }
                }
                "setCustomWebEnabled" -> {
                    CustomWebStore.setEnabled(appContext, call.argument<Boolean>("enabled") ?: false)
                    restartIfRunning()
                    result.success(true)
                }
                "clearCustomWeb" -> {
                    CustomWebStore.clearCustomWeb(appContext)
                    restartIfRunning()
                    result.success(true)
                }
                "scanBooks" -> scope.launch {
                    val books = withContext(Dispatchers.IO) { BookStore.scanBooks(appContext) }
                    result.success(books.map { bookMap(it) })
                }
                "deleteBook" -> {
                    val path = call.argument<String>("path")
                    val book = path?.let { findBook(it) }
                    result.success(book != null && BookStore.deleteBook(book))
                }
                "readBook" -> {
                    val path = call.argument<String>("path")
                    val book = path?.let { findBook(it) }
                    if (book == null) { result.success(mapOf("ok" to false, "error" to "文件不存在")) }
                    else scope.launch {
                        val state = withContext(Dispatchers.IO) { loadBook(book) }
                        result.success(
                            when (state) {
                                is com.tomato.downloader.core.PreviewState.Success -> mapOf(
                                    "ok" to true,
                                    "title" to state.bookTitle,
                                    "chapters" to state.chapters.map {
                                        mapOf("title" to it.title, "content" to it.content)
                                    }
                                )
                                is com.tomato.downloader.core.PreviewState.Error ->
                                    mapOf("ok" to false, "error" to state.message)
                                else -> mapOf("ok" to false, "error" to "未知状态")
                            }
                        )
                    }
                }
                "exportBook" -> {
                    val path = call.argument<String>("path")
                    val book = path?.let { findBook(it) }
                    if (book == null || pendingExportResult != null) { result.success(false) } else {
                        pendingExportResult = result
                        pendingExportPath = path
                        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                            addCategory(Intent.CATEGORY_OPENABLE)
                            type = "*/*"
                            putExtra(Intent.EXTRA_TITLE, "${book.name}.${book.ext.lowercase()}")
                        }
                        try { activity.startActivityForResult(intent, REQ_EXPORT_BOOK) }
                        catch (e: Exception) {
                            pendingExportResult = null; pendingExportPath = null
                            result.success(false)
                        }
                    }
                }
                "openUrl" -> {
                    val url = call.argument<String>("url")
                    var ok = false
                    if (url != null) {
                        try {
                            activity.startActivity(
                                Intent(Intent.ACTION_VIEW, Uri.parse(url))
                                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            )
                            ok = true
                        } catch (_: Exception) { }
                    }
                    result.success(ok)
                }
                "exitApp" -> { activity.finishAffinity(); result.success(true) }
                // Flutter 侧的轻量持久化（如主题设置）：独立 prefs 文件存字符串
                "prefsGet" -> {
                    val key = call.argument<String>("key")
                    val prefs = appContext.getSharedPreferences("tomato_flutter_prefs", Context.MODE_PRIVATE)
                    result.success(if (key == null) null else prefs.getString(key, null))
                }
                "prefsPut" -> {
                    val key = call.argument<String>("key")
                    val value = call.argument<String>("value")
                    if (key != null) {
                        val editor = appContext
                            .getSharedPreferences("tomato_flutter_prefs", Context.MODE_PRIVATE).edit()
                        if (value == null) editor.remove(key) else editor.putString(key, value)
                        editor.apply()
                    }
                    result.success(true)
                }
                else -> result.notImplemented()
            }
        }

        startCollectors()
    }

    fun dispose() {
        scope.cancel()
        stateSink = null
        logSink = null
    }

    fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        when (requestCode) {
            REQ_PICK_CUSTOM_WEB -> {
                val res = pendingPickResult ?: return
                pendingPickResult = null
                val uri = data?.data
                if (resultCode != Activity.RESULT_OK || uri == null) { res.success(false); return }
                scope.launch {
                    val ok = withContext(Dispatchers.IO) { CustomWebStore.setCustomWeb(appContext, uri) }
                    if (ok) restartIfRunning()
                    res.success(ok)
                }
            }
            REQ_EXPORT_BOOK -> {
                val res = pendingExportResult ?: return
                val path = pendingExportPath
                pendingExportResult = null
                pendingExportPath = null
                val uri = data?.data
                val book = path?.let { findBook(it) }
                if (resultCode != Activity.RESULT_OK || uri == null || book == null) {
                    res.success(false); return
                }
                scope.launch {
                    val ok = withContext(Dispatchers.IO) { BookStore.copyToUri(appContext, book, uri) }
                    res.success(ok)
                }
            }
        }
    }

    // ———————————— 内部 ————————————

    private fun startCollectors() {
        scope.launch { ServerManager.logLines.collect { pushNewLogs(it) } }
        val flows = listOf(
            ServerManager.state, ServerManager.localUrl, ServerManager.lanUrl,
            ServerManager.publicIp, ServerManager.publicGeo, ServerManager.publicIpLoading,
            PortStore.port, CustomWebStore.enabled, CustomWebStore.name, PasswordStore.password
        )
        flows.forEach { flow ->
            scope.launch { flow.collect { emitSnapshot() } }
        }
    }

    private fun pushNewLogs(lines: List<String>) {
        val sink = logSink ?: run { lastLogCount = lines.size; return }
        if (lines.size < lastLogCount) {
            sink.success(LOG_CLEAR_MARKER)
            lastLogCount = 0
        }
        if (lines.size > lastLogCount) {
            for (i in lastLogCount until lines.size) sink.success(lines[i])
            lastLogCount = lines.size
        }
    }

    private fun emitSnapshot() {
        stateSink?.success(snapshot())
    }

    private fun snapshot(): Map<String, Any?> = mapOf(
        "state" to ServerManager.state.value.name,
        "port" to PortStore.currentPort,
        "localUrl" to ServerManager.localUrl.value,
        "lanUrl" to ServerManager.lanUrl.value,
        "publicIp" to ServerManager.publicIp.value,
        "publicGeo" to ServerManager.publicGeo.value,
        "publicGeoFlag" to ServerManager.publicGeoFlag.value,
        "publicIpLoading" to ServerManager.publicIpLoading.value,
        "installed" to ServerManager.isInstalled(appContext),
        "binaryPath" to ServerManager.binaryPath.value,
        "usingCustomWeb" to ServerManager.isUsingCustomWeb(appContext),
        "customWebEnabled" to CustomWebStore.enabled.value,
        "customWebName" to CustomWebStore.name.value,
        "hasPassword" to PasswordStore.hasPassword(),
        "consentAgreed" to ConsentStore.agreed.value,
        "installSeq" to ServerManager.installSeq
    )

    private fun startServerViaService(): Boolean {
        if (!ServerManager.isInstalled(appContext)) return false
        val intent = Intent(appContext, WebServerService::class.java)
            .setAction(WebServerService.ACTION_START)
        ContextCompat.startForegroundService(appContext, intent)
        return true
    }

    private fun stopServerViaService() {
        ServerManager.stop()
        appContext.stopService(Intent(appContext, WebServerService::class.java))
    }

    /** 设置变更后若服务在运行则重启使新参数生效（与原 UI 的 restartServerIfRunning 一致）。 */
    private fun restartIfRunning() {
        if (ServerManager.state.value != ServerManager.ServerState.RUNNING) return
        scope.launch {
            stopServerViaService()
            delay(300)
            startServerViaService()
        }
    }

    private fun findBook(path: String): BookStore.BookInfo? {
        val f = File(path)
        if (!f.exists() || !f.isFile) return null
        val ext = f.extension.uppercase()
        return BookStore.BookInfo(f, f.nameWithoutExtension, ext, f.length(), f.lastModified())
    }

    private fun bookMap(b: BookStore.BookInfo): Map<String, Any?> = mapOf(
        "name" to b.name,
        "ext" to b.ext,
        "sizeBytes" to b.sizeBytes,
        "sizeText" to b.sizeText,
        "dateText" to b.dateText,
        "path" to b.path
    )
}
