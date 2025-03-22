import time
import requests
import bs4
import re
import os
import random
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import sys
from collections import OrderedDict

# 全局配置
CONFIG = {
    "max_workers": 5,
    "max_retries": 3,
    "request_timeout": 15,
    "status_file": ".dl_status.json",
    "user_agents": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ]
}

def get_headers(cookie=None):
    """生成随机请求头"""
    return {
        "User-Agent": random.choice(CONFIG["user_agents"]),
        "Cookie": cookie if cookie else get_cookie()
    }

def get_cookie():
    """智能Cookie管理"""
    cookie_path = "cookie.json"
    if os.path.exists(cookie_path):
        try:
            with open(cookie_path, 'r') as f:
                return json.load(f)
        except:
            pass
    
    # 生成新Cookie
    for _ in range(10):
        novel_web_id = random.randint(10**18, 10**19-1)
        cookie = f'novel_web_id={novel_web_id}'
        try:
            resp = requests.get(
                'https://fanqienovel.com',
                headers={"User-Agent": random.choice(CONFIG["user_agents"])},
                cookies={"novel_web_id": str(novel_web_id)},
                timeout=10
            )
            if resp.ok:
                with open(cookie_path, 'w') as f:
                    json.dump(cookie, f)
                return cookie
        except Exception as e:
            print(f"Cookie生成失败: {str(e)}")
            time.sleep(0.5)
    raise Exception("无法获取有效Cookie")

class NovelDownloader:
    def __init__(self, book_id, save_dir):
        self.book_id = book_id
        self.save_dir = os.path.abspath(save_dir)
        self.status_file = os.path.join(self.save_dir, CONFIG["status_file"])
        self.downloaded = self._load_status()
        self.book_info = None
        self._init_book_info()

    def _init_book_info(self):
        """初始化书籍元数据"""
        for _ in range(CONFIG["max_retries"]):
            try:
                resp = requests.get(
                    f'https://fanqienovel.com/page/{self.book_id}',
                    headers=get_headers(),
                    timeout=CONFIG["request_timeout"]
                )
                if resp.status_code == 404:
                    raise Exception("小说ID不存在")
                soup = bs4.BeautifulSoup(resp.text, 'lxml')
                
                # 提取元数据
                self.book_info = {
                    "title": soup.find('h1').get_text(strip=True),
                    "author": self._parse_author(soup),
                    "desc": self._parse_description(soup),
                    "chapters": self._parse_chapters(soup)
                }
                return
            except Exception as e:
                print(f"元数据获取失败: {str(e)}")
                time.sleep(2)
        raise Exception("无法获取书籍信息")

    def _parse_author(self, soup):
        """解析作者信息"""
        author_div = soup.find('div', class_='author-name')
        return author_div.find('span', class_='author-name-text').text if author_div else "未知作者"

    def _parse_description(self, soup):
        """解析书籍简介"""
        desc_div = soup.find('div', class_='page-abstract-content')
        return desc_div.find('p').text if desc_div else "暂无简介"

    def _parse_chapters(self, soup):
        """解析章节列表（保留番外等特殊编号）"""
        chapters = []
        for idx, item in enumerate(soup.select('div.chapter-item')):
            a_tag = item.find('a')
            if not a_tag:
                continue
            
            raw_title = a_tag.get_text(strip=True)
            
            # 智能识别特殊章节
            if re.match(r'^(番外|特别篇|if线)\s*', raw_title):
                final_title = raw_title
            else:
                clean_title = re.sub(
                    r'^第[一二三四五六七八九十百千\d]+章\s*',
                    '', 
                    raw_title
                ).strip()
                final_title = f"第{idx+1}章 {clean_title}"
            
            chapters.append({
                "id": a_tag['href'].split('/')[-1],
                "title": final_title,
                "url": f"https://fanqienovel.com{a_tag['href']}",
                "index": idx
            })
        return chapters

    def _load_status(self):
        """加载下载状态"""
        if os.path.exists(self.status_file):
            try:
                with open(self.status_file, 'r') as f:
                    return set(json.load(f))
            except:
                pass
        return set()

    def _save_status(self):
        """保存下载状态"""
        with open(self.status_file, 'w') as f:
            json.dump(list(self.downloaded), f)

    def _download_chapter(self, chapter):
        """下载单个章节"""
        for retry in range(CONFIG["max_retries"]):
            try:
                api_url = f"http://fan.jingluo.love/content?item_id={chapter['id']}"
                resp = requests.get(
                    api_url,
                    headers=get_headers(),
                    timeout=CONFIG["request_timeout"]
                )
                data = resp.json()
                
                if data.get("code") == 0:
                    return (chapter['index'], self._clean_content(data["data"]["content"]))
                return (chapter['index'], None)
            except Exception as e:
                print(f"章节 [{chapter['title']}] 下载失败: {str(e)}")
                time.sleep(1 * (retry + 1))
        return (chapter['index'], None)

    @staticmethod
    def _clean_content(raw_html):
        """内容净化（保留段落结构）"""
        text = re.sub(r'<header>.*?</header>', '', raw_html, flags=re.DOTALL)
        text = re.sub(r'<footer>.*?</footer>', '', text, flags=re.DOTALL)
        text = re.sub(r'</?article>', '', text)
        text = re.sub(r'<p\s+idx="\d+">', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _write_to_file(self, chapters_content):
        """按顺序写入文件"""
        file_path = os.path.join(self.save_dir, f"{self.book_info['title']}.txt")
        
        # 首次写入书籍信息
        if not os.path.exists(file_path):
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(f"书名：{self.book_info['title']}\n")
                f.write(f"作者：{self.book_info['author']}\n")
                f.write("简介：\n" + '\n'.join(['    ' + line for line in self.book_info['desc'].split('\n')]) + '\n\n')

        # 按索引排序后追加
        sorted_chapters = sorted(chapters_content.items(), key=lambda x: x[0])
        with open(file_path, 'a', encoding='utf-8') as f:
            for index, (chapter, content) in sorted_chapters:
                if content:
                    f.write(f"{chapter['title']}\n")
                    f.write('\n'.join(['    ' + line for line in content.split('\n')]) + '\n\n')

    def run(self):
        """执行下载任务"""
        todo_chapters = [ch for ch in self.book_info["chapters"] if ch["id"] not in self.downloaded]
        if not todo_chapters:
            print("所有章节已是最新，无需下载")
            return

        print(f"总章节数: {len(self.book_info['chapters'])}, 待下载: {len(todo_chapters)}")
        os.makedirs(self.save_dir, exist_ok=True)

        # 多线程下载并缓存内容
        content_cache = OrderedDict()
        success_count = 0
        with ThreadPoolExecutor(max_workers=CONFIG["max_workers"]) as executor:
            futures = {executor.submit(self._download_chapter, ch): ch for ch in todo_chapters}
            
            with tqdm(total=len(todo_chapters), desc="下载进度", unit="章") as pbar:
                for future in as_completed(futures):
                    chapter = futures[future]
                    try:
                        index, content = future.result()
                        if content:
                            content_cache[index] = (chapter, content)
                            success_count += 1
                            self.downloaded.add(chapter["id"])
                    except Exception as e:
                        print(f"章节 [{chapter['title']}] 处理失败: {str(e)}")
                    finally:
                        pbar.update(1)

        # 按顺序写入文件
        if content_cache:
            self._write_to_file(content_cache)
            self._save_status()

        print(f"下载完成！成功: {success_count}, 失败: {len(todo_chapters)-success_count}")

def main():
    print("""番茄小说下载器精简版-微优化版
作者：Dlmos（Dlmily）
改动者：Mach ft. DeepSeek
Github：https://github.com/Dlmily/Tomato-Novel-Downloader-Lite
参考代码：https://github.com/ying-ck/fanqienovel-downloader/blob/main/src/ref_main.py
------------------------------------------""")
    
    book_id = input("请输入小说ID：").strip()
    save_dir = input("保存路径（留空为当前目录）：").strip() or os.getcwd()

    try:
        downloader = NovelDownloader(book_id, save_dir)
        downloader.run()
    except Exception as e:
        print(f"运行错误: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
