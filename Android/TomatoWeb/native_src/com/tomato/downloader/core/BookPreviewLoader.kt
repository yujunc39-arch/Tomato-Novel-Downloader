package com.tomato.downloader.core

import java.io.File
import java.nio.charset.Charset
import java.util.zip.ZipFile

/**
 * 书籍预览解析（从原 Compose 版 BookPreviewScreen 抽出的纯逻辑）：
 * TXT 编码检测 + 章节切分；EPUB 解包 + OPF/spine 解析。供 Flutter 桥调用。
 *
 * EPUB 读取优化（v3.0.34）：
 * - 用 ZipFile 随机访问，只解压 container/OPF/spine 章节，图片字体等
 *   不读不存（旧实现整包读进内存，还按原名+小写名存了两份）；
 * - 章节编码读 XML 声明，默认 UTF-8，不再每章整篇解码两次猜编码；
 * - 所有正则提到顶层只编译一次（旧实现每章重新编译约十个）；
 * - href 做路径归一化（./ ../）和 %XX 解码，旧实现对这类书会找不到章节；
 * - 最近解析成功的一本书做单条缓存（路径+修改时间+大小为键），
 *   退出预览再进来不用重解析。
 */

data class PreviewChapter(val title: String, val content: String)

/** 加载状态 */
sealed class PreviewState {
    object Loading : PreviewState()
    data class Success(val chapters: List<PreviewChapter>, val bookTitle: String) : PreviewState()
    data class Error(val message: String) : PreviewState()
}

// ---- 顶层预编译正则（旧实现在函数内每次调用都重新编译） ----

private val TXT_CHAPTER_REGEX = Regex(
    """^\s*(第[零一二三四五六七八九十百千万0-9]+[章节回卷]|Chapter\s+\d+|chapter\s+\d+).*""",
    RegexOption.MULTILINE
)
private val OPF_PATH_REGEX = Regex(
    """<rootfile\b[^>]*\bfull-path\s*=\s*["']([^"']+)["']""",
    RegexOption.IGNORE_CASE
)
private val DC_TITLE_REGEX = Regex(
    """<dc:title[^>]*>([^<]+)</dc:title>""",
    RegexOption.IGNORE_CASE
)
private val MANIFEST_REGEX_ID_FIRST = Regex(
    """<item\b[^>]*\bid\s*=\s*["']([^"']+)["'][^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*/>""",
    RegexOption.IGNORE_CASE
)
private val MANIFEST_REGEX_HREF_FIRST = Regex(
    """<item\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*\bid\s*=\s*["']([^"']+)["'][^>]*/>""",
    RegexOption.IGNORE_CASE
)
private val SPINE_REGEX = Regex(
    """<itemref\b[^>]*\bidref\s*=\s*["']([^"']+)["']""",
    RegexOption.IGNORE_CASE
)
private val XML_ENCODING_REGEX = Regex("""encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""")
private val BODY_REGEX = Regex(
    """<body[^>]*>([\s\S]*?)</body>""",
    RegexOption.IGNORE_CASE
)
private val DROP_BLOCK_REGEX = Regex(
    """<(script|style|head)\b[\s\S]*?</\1\s*>""",
    RegexOption.IGNORE_CASE
)
private val BR_REGEX = Regex("""<br\s*/?>""", RegexOption.IGNORE_CASE)
private val BLOCK_TAG_REGEX = Regex(
    """</?(p|div|h[1-6]|li|tr|blockquote)[^>]*>""",
    RegexOption.IGNORE_CASE
)
private val ANY_TAG_REGEX = Regex("""<[^>]+>""")
private val H_TAG_REGEX = Regex(
    """<h[12][^>]*>([\s\S]*?)</h[12]>""",
    RegexOption.IGNORE_CASE
)
private val TITLE_TAG_REGEX = Regex(
    """<title[^>]*>([\s\S]*?)</title>""",
    RegexOption.IGNORE_CASE
)

// ---- 最近一本解析成功的缓存（单条，键 = 路径 + 修改时间 + 大小） ----

private data class ParsedCache(
    val path: String,
    val modified: Long,
    val length: Long,
    val state: PreviewState.Success,
)

@Volatile
private var parsedCache: ParsedCache? = null

fun loadBook(book: BookStore.BookInfo): PreviewState {
    val file = book.file
    if (!file.exists() || !file.canRead()) return PreviewState.Error("文件不存在或无法读取")
    val modified = file.lastModified()
    val length = file.length()
    parsedCache?.let { c ->
        if (c.path == file.absolutePath && c.modified == modified && c.length == length) {
            return c.state
        }
    }
    val result = when (book.ext.lowercase()) {
        "txt" -> loadTxt(file, book.name)
        "epub" -> loadEpub(file)
        else -> PreviewState.Error("暂不支持预览 ${book.ext} 格式")
    }
    if (result is PreviewState.Success) {
        parsedCache = ParsedCache(file.absolutePath, modified, length, result)
    }
    return result
}

/**
 * TXT 解析：
 * 1. 自动检测编码（UTF-8 / GBK）
 * 2. 优先按"第X章"等正则切分章节
 * 3. 无章节标记则按 ~2000 字分段
 */
private fun loadTxt(file: File, fallbackTitle: String): PreviewState {
    val rawBytes = file.readBytes()
    val text = detectEncoding(rawBytes).let { String(rawBytes, it) }
    val firstLine = text.lineSequence().firstOrNull { it.isNotBlank() }?.trim()
    val bookTitle = firstLine?.takeIf { it.length <= 50 } ?: fallbackTitle

    val matches = TXT_CHAPTER_REGEX.findAll(text).toList()

    val chapters = if (matches.size >= 2) {
        matches.mapIndexed { index, match ->
            val start = match.range.first
            val end = if (index + 1 < matches.size) matches[index + 1].range.first else text.length
            val chunk = text.substring(start, end).trim()
            val firstNl = chunk.indexOf('\n')
            val title = if (firstNl > 0) chunk.substring(0, firstNl).trim() else chunk.take(30)
            val content = if (firstNl > 0) chunk.substring(firstNl).trim() else ""
            PreviewChapter(title.ifBlank { "第${index + 1}节" }, content.ifBlank { chunk })
        }
    } else {
        // 无章节标记：按段落累积 ~2000 字一段
        val cleanText = text.replace("\r\n", "\n").replace("\r", "\n")
        val paragraphs = cleanText.split("\n").filter { it.isNotBlank() }
        val chunks = mutableListOf<MutableList<String>>()
        var current = mutableListOf<String>()
        var currentLen = 0
        for (para in paragraphs) {
            current.add(para.trim())
            currentLen += para.length
            if (currentLen >= 2000) { chunks.add(current); current = mutableListOf(); currentLen = 0 }
        }
        if (current.isNotEmpty()) chunks.add(current)
        chunks.mapIndexed { index, lines ->
            PreviewChapter("${bookTitle} · 第${index + 1}段", lines.joinToString("\n\n"))
        }
    }

    return if (chapters.isEmpty()) PreviewState.Error("文件内容为空")
    else PreviewState.Success(chapters, bookTitle)
}

/**
 * EPUB 解析（纯标准库）：
 * EPUB = ZIP 包。结构：
 *   META-INF/container.xml → 指向 OPF 文件路径
 *   *.opf → 包含 <manifest>（资源列表）和 <spine>（阅读顺序）
 *   章节 XHTML 文件
 *
 * 用 ZipFile 按需读条目：先建路径索引（不读数据），再只解压
 * container、OPF 和 spine 里的章节文件。
 */
private fun loadEpub(file: File): PreviewState {
    ZipFile(file).use { zip ->
        // 路径索引：小写全路径 → 条目名；小写文件名 → 条目名（兜底查找）。
        val byPath = HashMap<String, String>()
        val byName = HashMap<String, String>()
        val enumeration = zip.entries()
        while (enumeration.hasMoreElements()) {
            val entry = enumeration.nextElement()
            if (entry.isDirectory) continue
            val lower = entry.name.lowercase()
            byPath.putIfAbsent(lower, entry.name)
            byName.putIfAbsent(lower.substringAfterLast('/'), entry.name)
        }
        if (byPath.isEmpty()) return PreviewState.Error("EPUB 文件为空")

        fun readEntry(path: String): ByteArray? {
            val normalized = normalizeZipPath(path)
            val name = byPath[normalized.lowercase()]
                ?: byName[normalized.lowercase().substringAfterLast('/')]
                ?: return null
            val entry = zip.getEntry(name) ?: return null
            return zip.getInputStream(entry).use { it.readBytes() }
        }

        // 1. 找 OPF 路径
        val containerXml = readEntry("META-INF/container.xml")
            ?: return PreviewState.Error("找不到 container.xml")
        val opfPath = extractOpfPath(String(containerXml, Charsets.UTF_8))
            ?: return PreviewState.Error("无法解析 container.xml")
        val opfDir = opfPath.substringBeforeLast('/', "")

        // 2. 解析 OPF
        val opfData = readEntry(opfPath)
            ?: return PreviewState.Error("找不到 OPF 文件: $opfPath")
        val opfText = String(opfData, epubCharset(opfData))

        // 书名
        val bookTitle = DC_TITLE_REGEX.find(opfText)?.groupValues?.get(1)?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: file.nameWithoutExtension

        // manifest: id → href
        val manifest = mutableMapOf<String, String>()
        MANIFEST_REGEX_ID_FIRST.findAll(opfText).forEach { m ->
            manifest[m.groupValues[1]] = m.groupValues[2]
        }
        // 也匹配 href 在 id 前的情况
        MANIFEST_REGEX_HREF_FIRST.findAll(opfText).forEach { m ->
            val href = m.groupValues[1]
            val id = m.groupValues[2]
            if (id !in manifest) manifest[id] = href
        }

        // spine: 有序的 idref 列表
        val spineIds = SPINE_REGEX.findAll(opfText).map { it.groupValues[1] }.toList()
        if (spineIds.isEmpty()) return PreviewState.Error("EPUB spine 为空")

        // 3. 按 spine 顺序读取 XHTML
        val chapters = mutableListOf<PreviewChapter>()
        spineIds.forEachIndexed { index, id ->
            val href = manifest[id] ?: return@forEachIndexed
            val fullPath = if (opfDir.isNotEmpty()) "$opfDir/$href" else href
            val data = readEntry(fullPath) ?: return@forEachIndexed
            val html = String(data, epubCharset(data))
            val text = htmlToText(html)
            if (text.isNotBlank()) {
                val title = extractTitleFromHtml(html) ?: "第${index + 1}节"
                chapters.add(PreviewChapter(title, text))
            }
        }

        return if (chapters.isEmpty()) PreviewState.Error("EPUB 解析后无可用内容")
        else PreviewState.Success(chapters, bookTitle)
    }
}

/** 从 container.xml 中提取 OPF 文件路径 */
private fun extractOpfPath(containerXml: String): String? {
    return OPF_PATH_REGEX.find(containerXml)?.groupValues?.get(1)
}

/** ZIP 内路径归一化：%XX 解码 + 处理 ./ 与 ../ 段。 */
private fun normalizeZipPath(path: String): String {
    val decoded = percentDecode(path)
    val out = mutableListOf<String>()
    for (seg in decoded.split('/')) {
        when (seg) {
            "", "." -> Unit
            ".." -> if (out.isNotEmpty()) out.removeAt(out.lastIndex)
            else -> out.add(seg)
        }
    }
    return out.joinToString("/")
}

/** 只解 %XX（不把 + 变空格，ZIP 路径里的 + 是字面量）。 */
private fun percentDecode(s: String): String {
    if (!s.contains('%')) return s
    val bytes = java.io.ByteArrayOutputStream()
    var i = 0
    while (i < s.length) {
        val c = s[i]
        if (c == '%' && i + 2 <= s.lastIndex) {
            val hex = s.substring(i + 1, i + 3).toIntOrNull(16)
            if (hex != null) {
                bytes.write(hex)
                i += 3
                continue
            }
        }
        bytes.write(c.toString().toByteArray(Charsets.UTF_8))
        i++
    }
    return String(bytes.toByteArray(), Charsets.UTF_8)
}

/** EPUB 内 XML/XHTML 的编码：读 XML 声明里的 encoding，没有就 UTF-8（规范默认）。 */
private fun epubCharset(bytes: ByteArray): Charset {
    val head = String(bytes, 0, minOf(bytes.size, 256), Charsets.ISO_8859_1)
    val enc = XML_ENCODING_REGEX.find(head)?.groupValues?.get(1)
    if (enc != null) {
        try {
            return Charset.forName(enc)
        } catch (_: Exception) {
            // 落回 UTF-8
        }
    }
    return Charsets.UTF_8
}

/** 检测字节数组的编码：有 BOM → UTF-8/UTF-16；否则尝试 UTF-8，失败回退 GBK（TXT 用） */
private fun detectEncoding(bytes: ByteArray): Charset {
    // BOM 检测
    if (bytes.size >= 3 && bytes[0] == 0xEF.toByte() && bytes[1] == 0xBB.toByte() && bytes[2] == 0xBF.toByte()) {
        return Charset.forName("UTF-8")
    }
    // 尝试 UTF-8 解码：如果无替换字符则认为是 UTF-8
    val utf8Decoded = String(bytes, Charset.forName("UTF-8"))
    if (!utf8Decoded.contains('\uFFFD')) return Charset.forName("UTF-8")
    // 回退 GBK
    return try { Charset.forName("GBK") } catch (_: Exception) { Charset.forName("UTF-8") }
}

/** XHTML → 纯文本：去标签、解码实体、按句号分段 */
private fun htmlToText(html: String): String {
    // 先提取 body 内容（如果有）
    val body = BODY_REGEX.find(html)?.groupValues?.get(1) ?: html

    // 一趟移除 script/style/head
    var cleaned = DROP_BLOCK_REGEX.replace(body, "")
    // <br> → 换行
    cleaned = BR_REGEX.replace(cleaned, "\n")
    // <p>, <div> → 换行
    cleaned = BLOCK_TAG_REGEX.replace(cleaned, "\n")
    // 去除所有剩余标签
    cleaned = ANY_TAG_REGEX.replace(cleaned, "")

    // 解码常见 HTML 实体
    cleaned = cleaned
        .replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&ldquo;", "\u201C")
        .replace("&rdquo;", "\u201D")
        .replace("&hellip;", "\u2026")

    // 按句号、问号、感叹号换行，提升阅读体验
    cleaned = cleaned
        .replace("。", "。\n")
        .replace("！", "！\n")
        .replace("？", "？\n")

    // 清理多余空白
    return cleaned.lineSequence()
        .map { it.trim() }
        .filter { it.isNotEmpty() }
        .joinToString("\n")
        .trim()
}

/** 从 XHTML 中提取标题（h1/h2/title 标签） */
private fun extractTitleFromHtml(html: String): String? {
    // h1/h2
    val hTag = H_TAG_REGEX.find(html)
    if (hTag != null) {
        val title = ANY_TAG_REGEX.replace(hTag.groupValues[1], "").trim()
        if (title.isNotEmpty() && title.length <= 80) return title
    }
    // <title>
    val titleTag = TITLE_TAG_REGEX.find(html)
    if (titleTag != null) {
        val title = titleTag.groupValues[1].trim()
        if (title.isNotEmpty() && title.length <= 80) return title
    }
    return null
}
