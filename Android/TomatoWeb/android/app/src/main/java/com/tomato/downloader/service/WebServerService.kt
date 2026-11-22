package com.tomato.downloader.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.tomato.downloader.R
import com.tomato.downloader.core.ServerManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * 前台服务：保持 Web 服务进程在后台持续运行。
 *
 * targetSdk=28 下使用最简单的 startForeground（无需 foregroundServiceType）。
 */
class WebServerService : Service() {

    companion object {
        const val ACTION_START = "com.tomato.downloader.ACTION_START"
        const val ACTION_STOP = "com.tomato.downloader.ACTION_STOP"
        private const val CHANNEL_ID = "web_server_channel"
        private const val NOTIF_ID = 1001
    }

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    // 跟随 ServerManager.localUrl：端口自动切换（onPortChanged 更新 localUrl）
    // 后，前台通知里的旧地址也会同步刷新，不再停留在启动那一刻的端口。
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var urlJob: Job? = null

    private fun buildNotification(url: String) =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(url)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

    private fun startUrlWatcher() {
        urlJob?.cancel()
        urlJob = serviceScope.launch {
            ServerManager.localUrl.collect { url ->
                val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                nm.notify(NOTIF_ID, buildNotification(url))
            }
        }
    }

    private fun stopUrlWatcher() {
        urlJob?.cancel()
        urlJob = null
    }

    private fun createChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW
        ).apply { description = getString(R.string.notification_channel_name) }
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(channel)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopUrlWatcher()
                ServerManager.stop()
                @Suppress("DEPRECATION")
                stopForeground(true)
                stopSelf()
            }
            else -> {
                // Bug1 修复：让 startServer 返回 Boolean，只有真正启动成功才保持前台通知
                val started = ServerManager.startServer(this)
                if (!started) {
                    // startForegroundService 要求 5s 内必须调用 startForeground，先创建一个临时通知再立即移除
                    val tempNotif = NotificationCompat.Builder(this, CHANNEL_ID)
                        .setSmallIcon(R.drawable.ic_launcher_foreground)
                        .setContentTitle(getString(R.string.notification_title))
                        .setContentText("启动失败：请先下载 Web 二进制")
                        .setPriority(NotificationCompat.PRIORITY_LOW)
                        .build()
                    @Suppress("DEPRECATION")
                    startForeground(NOTIF_ID, tempNotif)
                    // 立刻移除并停止服务
                    @Suppress("DEPRECATION")
                    stopForeground(true)
                    stopSelf()
                } else {
                    @Suppress("DEPRECATION")
                    startForeground(NOTIF_ID, buildNotification(ServerManager.localUrl.value))
                    startUrlWatcher()
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        stopUrlWatcher()
        serviceScope.cancel()
        ServerManager.stop()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
