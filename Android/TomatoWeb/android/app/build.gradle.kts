plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// —— 签名配置：口令不进仓库 ——
// 优先级：keystore.properties > 环境变量 > 无（输出未签名 APK）
val keystorePropsFile = rootProject.file("../../keystore.properties")
val keystoreProps = java.util.Properties().apply {
    if (keystorePropsFile.exists()) keystorePropsFile.inputStream().use { load(it) }
}
fun ks(key: String, env: String): String? =
    (keystoreProps.getProperty(key) ?: System.getenv(env))?.takeIf { it.isNotBlank() }

val ksStoreFile = ks("storeFile", "TOMATO_KS_FILE")
val ksStorePass = ks("storePassword", "TOMATO_KS_PASS")
val ksAlias = ks("keyAlias", "TOMATO_KS_ALIAS")
val ksKeyPass = ks("keyPassword", "TOMATO_KS_KEY_PASS") ?: ksStorePass
val ksFile = ksStoreFile?.let { rootProject.file("../../$it") }
val hasSigning = ksFile?.exists() == true && !ksStorePass.isNullOrBlank() && !ksAlias.isNullOrBlank()

android {
    namespace = "com.tomato.downloader"
    compileSdk = flutter.compileSdkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    // 林九思签名（与 2.x 版同一证书，保证覆盖升级）
    // 证书文件与口令不随仓库分发，见 keystore.properties.example
    signingConfigs {
        if (hasSigning) {
            create("release") {
                storeFile = ksFile
                storePassword = ksStorePass
                keyAlias = ksAlias
                keyPassword = ksKeyPass
                enableV1Signing = true
                enableV2Signing = true
            }
        }
    }

    defaultConfig {
        applicationId = "com.tomato.downloader"
        // Flutter 3.47 引擎的最低支持版本
        minSdk = 24
        // 刻意保持 28：Android 10+ 的 W^X 策略下，targetSdk≥29 的应用无法执行
        // 私有目录里下载的二进制（Web 服务的核心机制），2.x 版即为 28。
        targetSdk = 28
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            if (hasSigning) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    lint {
        // targetSdk 28 是刻意保留的（见 defaultConfig 注释，本应用不走 Google Play 分发），
        // 关掉 Play 上架政策类的致命检查，其余 lint 照常。
        disable.add("ExpiredTargetSdkVersion")
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}

dependencies {
    // 原生层（ServerManager 等）沿用 2.x 的协程实现
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
}
