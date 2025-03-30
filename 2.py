import time
import requests
import bs4
import re
import os
import random
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from collections import OrderedDict

# 全局配置
CONFIG = {
    "max_workers": 4,
    "max_retries": 3,
    "request_timeout": 15,
    "status_file": "chapter.json",
    "user_agents": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ],
    "api_endpoints": [
        "https://lsjk.zyii.xyz:3666/content?item_id={chapter_id}",
        "http://api.jingluo.love/content?item_id={chapter_id}",
        "http://fan.jingluo.love/content?item_id={chapter_id}",
        "http://apifq.jingluo.love/content?item_id={chapter_id}",
        "http://rehaofan.jingluo.love/content?item_id={chapter_id}",
        "http://yuefanqie.jingluo.love/content?item_id={chapter_id}"
    ]
}

def get_headers(cookie=None):
    """生成随机请求头"""
    headers = {
        "User-Agent": random.choice(CONFIG["user_agents"]),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive"
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers

def get_cookie():
    """生成或加载Cookie"""
    cookie_path = "cookie.json"
    if os.path.exists(cookie_path):
        try:
            with open(cookie_path, 'r') as f:
                return f.read().strip()
        except:
            pass
    
    # 生成新Cookie
    for _ in range(3):
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
                    f.write(cookie)
                return cookie
        except Exception as e:
            print(f"Cookie生成失败: {str(e)}")
            time.sleep(1)
    raise Exception("无法获取有效Cookie")

def down_text(chapter_id, headers):
    """下载章节内容"""
    max_retries = CONFIG["max_retries"]
    content = ""
    chapter_title = ""
    
    # 记录API端点状态
    if not hasattr(down_text, "api_status"):
        down_text.api_status = {endpoint: {"failures": 0, "last_success": 0} 
                              for endpoint in CONFIG["api_endpoints"]}
    
    while True:
        # 端点状态排序
        sorted_endpoints = sorted(
            CONFIG["api_endpoints"],
            key=lambda x: (
                -down_text.api_status[x]["last_success"],
                down_text.api_status[x]["failures"]
            )
        )
        
        for api_endpoint in sorted_endpoints:
            current_endpoint = api_endpoint.format(chapter_id=chapter_id)
            retry_count = 0
            
            if down_text.api_status[api_endpoint]["failures"] >= max_retries:
                continue
                
            while retry_count < max_retries:
                try:
                    # 添加随机延迟
                    time.sleep(random.uniform(0.2, 1))
                    
                    response = requests.get(
                        current_endpoint, 
                        headers=headers, 
                        timeout=CONFIG["request_timeout"]
                    )
                    data = response.json()
                    
                    if data.get("code") == 0:
                        content = data.get("data", {}).get("content", "")
                        chapter_title = data.get("data", {}).get("title", "")
                        
                        # 处理标题
                        if not chapter_title and "<div class=\"tt-title\">" in content:
                            chapter_title = re.search(r'<div class="tt-title">(.*?)</div>', content).group(1)
                        
                        if chapter_title and re.match(r'^第[0-9]+章', chapter_title):
                            chapter_title = re.sub(r'^第[0-9]+章\s*', '', chapter_title)
                        
                        if content:
                            down_text.api_status[api_endpoint]["last_success"] = time.time()
                            down_text.api_status[api_endpoint]["failures"] = 0
                            
                            # 提取内容
                            if "lsjk.zyii.xyz" in api_endpoint:
                                paragraphs = re.findall(r'<p idx="\d+">(.*?)</p>', content)
                            else:
                                paragraphs = re.findall(r'<p>(.*?)</p>', content)
                            
                            cleaned_content = "\n".join([p.strip() for p in paragraphs if p.strip()])
                            formatted_content = '\n'.join(['    ' + line if line.strip() else line 
                                                         for line in cleaned_content.split('\n')])
                            return chapter_title, formatted_content
                    else:
                        raise Exception(f"API返回错误代码: {data.get('code')}")
                    
                except requests.exceptions.RequestException as e:
                    retry_count += 1
                    down_text.api_status[api_endpoint]["failures"] += 1
                    print(f"API端点 {api_endpoint} 请求失败，正在重试({retry_count}/{max_retries}): {str(e)}")
                    time.sleep(2 * retry_count)
                    
                except Exception as e:
                    retry_count += 1
                    down_text.api_status[api_endpoint]["failures"] += 1
                    print(f"API端点 {api_endpoint} 处理出错，正在重试({retry_count}/{max_retries}): {str(e)}")
                    time.sleep(1 * retry_count)
        
        print("所有API端点尝试失败，将重新尝试...请等待三秒。")
        time.sleep(3)

def get_chapters_from_api(book_id, headers):
    """从API获取章节列表"""
    url = f"https://fanqienovel.com/api/reader/directory/detail?bookId={book_id}"
    try:
        response = requests.get(url, headers=headers, timeout=CONFIG["request_timeout"])
        if response.status_code != 200:
            print(f"获取章节列表失败，状态码: {response.status_code}")
            return None

        data = response.json()
        if data.get("code") != 0:
            print(f"API返回错误: {data.get('message', '未知错误')}")
            return None

        chapters = []
        chapter_ids = data.get("data", {}).get("allItemIds", [])
        
        # 创建章节列表
        for idx, chapter_id in enumerate(chapter_ids):
            if not chapter_id:
                continue
                
            final_title = f"第{idx+1}章"
            
            chapters.append({
                "id": chapter_id,
                "title": final_title,
                "index": idx
            })
        
        return chapters
    except Exception as e:
        print(f"从API获取章节列表失败: {str(e)}")
        return None
        
def download_chapter(chapter, headers, save_path, book_name, downloaded):
    """下载单个章节"""
    if chapter["id"] in downloaded:
        return None
    
    chapter_title, content = down_text(chapter["id"], headers)
    if content:
        output_file_path = os.path.join(save_path, f"{book_name}.txt")
        try:
            with open(output_file_path, 'a', encoding='utf-8') as f:
                if chapter_title:
                    f.write(f'{chapter["title"]} {chapter_title}\n')
                else:
                    f.write(f'{chapter["title"]}\n')
                f.write(content + '\n\n')
            downloaded.add(chapter["id"])
            return chapter["index"], content
        except Exception as e:
            print(f"写入文件失败: {str(e)}")
    return None

def get_book_info(book_id, headers):
    """获取书名、作者、简介"""
    url = f'https://fanqienovel.com/page/{book_id}'
    try:
        response = requests.get(url, headers=headers, timeout=CONFIG["request_timeout"])
        if response.status_code != 200:
            print(f"网络请求失败，状态码: {response.status_code}")
            return None, None, None

        soup = bs4.BeautifulSoup(response.text, 'html.parser')
        
        # 获取书名
        name_element = soup.find('h1')
        name = name_element.text if name_element else "未知书名"
        
        # 获取作者
        author_name = "未知作者"
        author_name_element = soup.find('div', class_='author-name')
        if author_name_element:
            author_name_span = author_name_element.find('span', class_='author-name-text')
            if author_name_span:
                author_name = author_name_span.text
        
        # 获取简介
        description = "无简介"
        description_element = soup.find('div', class_='page-abstract-content')
        if description_element:
            description_p = description_element.find('p')
            if description_p:
                description = description_p.text
        
        return name, author_name, description
    except Exception as e:
        print(f"获取书籍信息失败: {str(e)}")
        return None, None, None

def load_status(save_path):
    """加载下载状态"""
    status_file = os.path.join(save_path, CONFIG["status_file"])
    if os.path.exists(status_file):
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
                return set()
        except:
            pass
    return set()

def save_status(save_path, downloaded):
    """保存下载状态"""
    status_file = os.path.join(save_path, CONFIG["status_file"])
    with open(status_file, 'w', encoding='utf-8') as f:
        json.dump(list(downloaded), f, ensure_ascii=False, indent=2)

def Run(book_id, save_path):
    """运行下载"""
    try:
        headers = get_headers(get_cookie())
        
        # 获取章节列表
        chapters = get_chapters_from_api(book_id, headers)
        if not chapters:
            print("未找到任何章节，请检查小说ID是否正确。")
            return
            
        # 获取书籍信息
        name, author_name, description = get_book_info(book_id, headers)
        if not name:
            print("无法获取书籍信息，将使用默认名称")
            name = f"未知小说_{book_id}"
            author_name = "未知作者"
            description = "无简介"

        downloaded = load_status(save_path)
        if downloaded:
            print(f"检测到您曾经下载过小说《{name}》。")
            user_input = input("是否需要再次下载？如果需要请输入1并回车，如果不需要请直接回车即可返回主程序：")
            if user_input != "1":
                print("已取消下载，返回主程序。")
                return

        # 准备下载队列
        todo_chapters = [ch for ch in chapters if ch["id"] not in downloaded]
        if not todo_chapters:
            print("所有章节已是最新，无需下载")
            return

        print(f"开始下载：《{name}》, 总章节数: {len(chapters)}, 待下载: {len(todo_chapters)}")
        os.makedirs(save_path, exist_ok=True)

        # 写入书籍信息
        output_file_path = os.path.join(save_path, f"{name}.txt")
        if not os.path.exists(output_file_path):
            with open(output_file_path, 'w', encoding='utf-8') as f:
                f.write(f"小说名: {name}\n作者: {author_name}\n内容简介: {description}\n\n")

        # 多线程变量
        success_count = 0
        failed_chapters = []
        chapter_results = {}
        lock = threading.Lock()
        
        def download_task(chapter):
            """多线程下载任务"""
            nonlocal success_count
            try:
                chapter_title, content = down_text(chapter["id"], headers)
                if content:
                    with lock:
                        chapter_results[chapter["index"]] = {
                            "base_title": chapter["title"],
                            "api_title": chapter_title,
                            "content": content
                        }
                        success_count += 1
            except Exception as e:
                with lock:
                    failed_chapters.append(chapter["title"])
        
        # 多线程下载
        with ThreadPoolExecutor(max_workers=CONFIG["max_workers"]) as executor:
            futures = [executor.submit(download_task, ch) for ch in todo_chapters]
            
            # 显示进度条
            with tqdm(total=len(todo_chapters), desc="下载进度") as pbar:
                for future in as_completed(futures):
                    pbar.update(1)
        
        # 按章节顺序写入文件
        with open(output_file_path, 'a', encoding='utf-8') as f:
            for index in sorted(chapter_results.keys()):
                result = chapter_results[index]
                if result["api_title"]:
                    title = f'{result["base_title"]} {result["api_title"]}'
                else:
                    title = result["base_title"]
                f.write(f"{title}\n")
                f.write(result["content"] + '\n\n')
                downloaded.add(todo_chapters[index]["id"])

        # 保存下载状态
        save_status(save_path, downloaded)

        if failed_chapters:
            print(f"\n以下章节下载失败: {', '.join(failed_chapters)}")
            with open(os.path.join(save_path, "failed_chapters.txt"), 'w', encoding='utf-8') as f:
                f.write("\n".join(failed_chapters))

        print(f"下载完成！成功: {success_count}, 失败: {len(todo_chapters)-success_count}")

    except Exception as e:
        print(f"运行过程中发生错误: {str(e)}")

def main():
    print("""欢迎使用番茄小说下载器精简版！
作者：Dlmos（Dlmily）
Github：https://github.com/Dlmily/Tomato-Novel-Downloader-Lite
赞助/了解新产品：https://afdian.com/a/dlbaokanluntanos
*使用前须知*：开始下载之后，您可能会过于着急而查看下载文件的位置，这是徒劳的，请耐心等待小说下载完成再查看！另外如果你要下载之前已经下载过的小说(在此之前已经删除了原txt文件)，那么你有可能会遇到"所有章节已是最新，无需下载"的情况，这时就请删除掉chapter.json，然后再次运行程序。
------------------------------------------""")
    
    while True:
        book_id = input("请输入小说ID（输入q退出）：").strip()
        if book_id.lower() == 'q':
            break
            
        save_path = input("保存路径（留空为当前目录）：").strip() or os.getcwd()
        
        try:
            Run(book_id, save_path)
        except Exception as e:
            print(f"运行错误: {str(e)}")
        
        print("\n" + "="*50 + "\n")

if __name__ == "__main__":
    main()