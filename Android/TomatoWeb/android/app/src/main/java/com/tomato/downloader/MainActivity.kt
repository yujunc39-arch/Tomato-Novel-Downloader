package com.tomato.downloader

import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine

/**
 * Flutter 入口 Activity：只负责承载 Flutter 引擎并注册原生桥（HostBridge）。
 * 原生核心逻辑（服务管理/下载/书籍）仍在 core/ 与 service/ 中，与 2.x 版一致。
 */
class MainActivity : FlutterActivity() {

    private var bridge: HostBridge? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        bridge = HostBridge(this).also { it.register(flutterEngine) }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        bridge?.onActivityResult(requestCode, resultCode, data)
    }

    override fun onDestroy() {
        bridge?.dispose()
        bridge = null
        super.onDestroy()
    }
}
