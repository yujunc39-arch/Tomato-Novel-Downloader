import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// ============================================================
// 签名材料不随仓库分发。
// 提供方式（二选一）：
//   1) 环境变量：TOMATO_KS_FILE / TOMATO_KS_PASS / TOMATO_KS_ALIAS
//   2) 本地文件：Android/TomatoWeb/keystore.properties（已在 .gitignore 中）
//      storeFile=../release.jks
//      storePassword=...
//      keyAlias=...
//      keyPassword=...
// 未配置时 release 产物为「未签名」APK（适合 CI 只做编译检查）。
// ============================================================
val ksProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
fun ksProp(key: String, env: String): String? = System.getenv(env) ?: ksProps.getProperty(key)
val ksStorePath: String? = ksProp("storeFile", "TOMATO_KS_FILE")
val ksFile: java.io.File? = ksStorePath?.let { rootProject.file(it) }
val hasKeystore: Boolean = ksFile != null && ksFile.exists()

android {
    namespace = "com.tomato.downloader"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.tomato.downloader"
        minSdk = 23
        // 关键：targetSdk=28 获得 Android W^X 豁免，filesDir 可执行下载的二进制
        // 这是 Termux 能运行下载二进制的核心原理（参见 termux-app/gradle.properties）
        // 代价：失去 scoped storage 等新特性，但我们不需要，且通过 sideload 安装
        targetSdk = 28
        versionCode = 7795
        versionName = "2.1.8"
    }

    // 签名配置：V1 + V2 双签名（Android 7- 走 JAR 校验，7+ 走 APK 校验）。
    // keystore 路径与口令来自环境变量或本地 keystore.properties，不写死在仓库里。
    signingConfigs {
        if (hasKeystore) {
            create("release") {
                storeFile = ksFile
                storePassword = ksProp("storePassword", "TOMATO_KS_PASS")
                keyAlias = ksProp("keyAlias", "TOMATO_KS_ALIAS")
                keyPassword = ksProp("keyPassword", "TOMATO_KS_PASS")
                // AGP 8.x 已弃用 enableV1Signing/enableV2Signing，改用 isV1SigningEnabled/isV2SigningEnabled
                isV1SigningEnabled = true
                isV2SigningEnabled = true
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            if (hasKeystore) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    // 跳过 release 的 lint vital 检查（在低内存环境下避免 OOM，
    // 且本项目不依赖 lint 保证正确性，proguard 也未开启）
    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.10.01")
    implementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.navigation:navigation-compose:2.8.4")

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    debugImplementation("androidx.compose.ui:ui-tooling")
}
