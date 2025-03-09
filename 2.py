import time
import requests
import bs4
import re
import os
import random
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from lxml import etree
from tqdm import tqdm

# 全局变量
cookie_path = "cookie.json"

# 获取随机User-Agent
def get_random_user_agent():
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:91.0) Gecko/20100101 Firefox/91.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36 Edg/93.0.961.47",
    ]
    return random.choice(user_agents)

# 获取或加载Cookie
def get_cookie():
    bas = 1000000000000000000
    for i in range(random.randint(bas * 6, bas * 8), bas * 9):
        time.sleep(random.randint(50, 150) / 1000)
        cookie = 'novel_web_id=' + str(i)
        headers = {
            'User-Agent': get_random_user_agent(),
            'cookie': cookie
        }
        try:
            response = requests.get('https://fanqienovel.com', headers=headers, timeout=10)
            if response.status_code == 200 and len(response.text) > 200:
                with open(cookie_path, 'w', encoding='utf-8') as f:
                    json.dump(cookie, f)
                print(f"cookie已生成: {cookie}")
                return cookie
        except Exception as e:
            print(f"请求失败: {e}")
    return None

# 模拟浏览器请求
def get_headers():
    return {
        "User-Agent": get_random_user_agent(),
        "Cookie": get_cookie()
    }

# 加密内容解析
CODE = [[58344, 58715], [58345, 58716]]
charset = json.loads(
    '[["D","在","主","特","家","军","然","表","场","4","要","只","v","和","?","6","别","还","g","现","儿","岁","?","?","此","象","月","3","出","战","工","相","o","男","直","失","世","F","都","平","文","什","V","O","将","真","T","那","当","?","会","立","些","u","是","十","张","学","气","大","爱","两","命","全","后","东","性","通","被","1","它","乐","接","而","感","车","山","公","了","常","以","何","可","话","先","p","i","叫","轻","M","士","w","着","变","尔","快","l","个","说","少","色","里","安","花","远","7","难","师","放","t","报","认","面","道","S","?","克","地","度","I","好","机","U","民","写","把","万","同","水","新","没","书","电","吃","像","斯","5","为","y","白","几","日","教","看","但","第","加","候","作","上","拉","住","有","法","r","事","应","位","利","你","声","身","国","问","马","女","他","Y","比","父","x","A","H","N","s","X","边","美","对","所","金","活","回","意","到","z","从","j","知","又","内","因","点","Q","三","定","8","R","b","正","或","夫","向","德","听","更","?","得","告","并","本","q","过","记","L","让","打","f","人","就","者","去","原","满","体","做","经","K","走","如","孩","c","G","给","使","物","?","最","笑","部","?","员","等","受","k","行","一","条","果","动","光","门","头","见","往","自","解","成","处","天","能","于","名","其","发","总","母","的","死","手","入","路","进","心","来","h","时","力","多","开","已","许","d","至","由","很","界","n","小","与","Z","想","代","么","分","生","口","再","妈","望","次","西","风","种","带","J","?","实","情","才","这","?","E","我","神","格","长","觉","间","年","眼","无","不","亲","关","结","0","友","信","下","却","重","己","老","2","音","字","m","呢","明","之","前","高","P","B","目","太","e","9","起","稜","她","也","W","用","方","子","英","每","理","便","四","数","期","中","C","外","样","a","海","们","任"],["s","?","作","口","在","他","能","并","B","士","4","U","克","才","正","们","字","声","高","全","尔","活","者","动","其","主","报","多","望","放","h","w","次","年","?","中","3","特","于","十","入","要","男","同","G","面","分","方","K","什","再","教","本","己","结","1","等","世","N","?","说","g","u","期","Z","外","美","M","行","给","9","文","将","两","许","张","友","0","英","应","向","像","此","白","安","少","何","打","气","常","定","间","花","见","孩","它","直","风","数","使","道","第","水","已","女","山","解","d","P","的","通","关","性","叫","儿","L","妈","问","回","神","来","S","","四","望","前","国","些","O","v","l","A","心","平","自","无","军","光","代","是","好","却","c","得","种","就","意","先","立","z","子","过","Y","j","表","","么","所","接","了","名","金","受","J","满","眼","没","部","那","m","每","车","度","可","R","斯","经","现","门","明","V","如","走","命","y","6","E","战","很","上","f","月","西","7","长","夫","想","话","变","海","机","x","到","W","一","成","生","信","笑","但","父","开","内","东","马","日","小","而","后","带","以","三","几","为","认","X","死","员","目","位","之","学","远","人","音","呢","我","q","乐","象","重","对","个","被","别","F","也","书","稜","D","写","还","因","家","发","时","i","或","住","德","当","o","l","比","觉","然","吃","去","公","a","老","亲","情","体","太","b","万","C","电","理","?","失","力","更","拉","物","着","原","她","工","实","色","感","记","看","出","相","路","大","你","候","2","和","?","与","p","样","新","只","便","最","不","进","T","r","做","格","母","总","爱","身","师","轻","知","往","加","从","?","天","e","H","?","听","场","由","快","边","让","把","任","8","条","头","事","至","起","点","真","手","这","难","都","界","用","法","n","处","下","又","Q","告","地","5","k","t","岁","有","会","果","利","民"]]'
)

def interpreter(cc, mode=0):
    """解析加密内容"""
    bias = cc - CODE[mode][0]
    if 0 <= bias < len(charset[mode]):  # 检查bias是否在charset的有效范围内
        if charset[mode][bias] == '?':
            return chr(cc)
        return charset[mode][bias]
    return chr(cc)

def down_text(it, headers):
    """下载章节内容"""
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            res = requests.get('https://fanqienovel.com/reader/' + str(it), 
                              headers=headers, 
                              timeout=10)
            ele = etree.HTML(res.text)

            # 两种用XPath提取内容
            n = '\n'.join(ele.xpath('//div[@class="muye-reader-content noselect"]//p/text()'))
            if not n:
                n = '\n'.join(ele.xpath('//div[@class="muye-reader-content muye-reader-story-content noselect"]//p/text()'))

            # 使用interpreter函数处理加密内容
            s = ''.join([interpreter(ord(ch), 0) if CODE[0][0] <= ord(ch) <= CODE[0][1] else ch for ch in n])
            return s  
        except Exception as e:
            retry_count += 1
            print(f"下载出错，正在重试({retry_count}/{max_retries}): {str(e)}")
            time.sleep(0.4 * retry_count)
    
    print("达到最大重试次数，下载失败")
    return None

def funLog(text, headers):
    """解析章节内容"""
    content = down_text(text.url.split('/')[-1], headers)
    return content

def extract_chatper_titles(soup):
    """提取章节标题"""
    titles = []
    for item in soup.select('div.chapter-item'):
        title = item.get_text(strip=True)  # 直接从div.chapter-item中提取文本作为标题
        if title:
            titles.append(title)
    return titles

def get_book_info(book_id, headers):
    """获取书名、作者、简介"""
    url = f'https://fanqienovel.com/page/{book_id}'
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"网络请求失败，状态码: {response.status_code}")
        return None, None, None

    soup = bs4.BeautifulSoup(response.text, 'html.parser')
    
    # 获取书名
    name_element = soup.find('h1')
    name = name_element.text if name_element else "未知书名"
    
    # 获取作者
    author_name_element = soup.find('div', class_='author-name')
    author_name = None
    if author_name_element:
        author_name_span = author_name_element.find('span', class_='author-name-text')
        author_name = author_name_span.text if author_name_span else "未知作者"
    
    # 获取简介
    description_element = soup.find('div', class_='page-abstract-content')
    description = None
    if description_element:
        description_p = description_element.find('p')
        description = description_p.text if description_p else "无简介"
    
    return name, author_name, description

def download_chapter(div, headers, save_path, book_id, titles, i, total):
    """下载单个章节"""
    if not div.a:  # 检查是否存在a标签
        print(f"第 {i + 1} 章没有链接，跳过")
        return None
    
    detail_url = f"https://fanqienovel.com{div.a['href']}"
    response = requests.get(detail_url, headers=headers)
    content = funLog(response, headers)

    if content:
        with open(os.path.join(save_path, f"{book_id}.txt"), 'a', encoding='utf-8') as f:
            f.write(f'{titles[i]}\n')
            f.write(content + '\n\n')
        print(f'已下载 {i + 1}/{total}')
    else:
        print(f"第 {i + 1} 章下载失败")

def Run(book_id, save_path):
    """运行下载"""
    headers = get_headers()
    
    # 获取书籍信息
    name, author_name, description = get_book_info(book_id, headers)
    if not name:
        print("无法获取书籍信息，请检查小说ID或网络连接。")
        return

    # 获取章节列表
    url = f'https://fanqienovel.com/page/{book_id}'
    response = requests.get(url, headers=headers)
    soup = bs4.BeautifulSoup(response.text, 'lxml')

    li_list = soup.select("div.chapter-item")
    total = len(li_list)
    titles = extract_chatper_titles(soup)

    os.makedirs(save_path, exist_ok=True)
    with open(os.path.join(save_path, f"{name}.txt"), 'w', encoding='utf-8') as f:
        # 写入书籍信息
        f.write(f'小说名: {name}\n作者: {author_name}\n内容简介: {description}\n\n')

    # 使用多线程下载章节
    with ThreadPoolExecutor(max_workers=2) as executor:  # 设置最大线程数
        futures = []
        for i, div in enumerate(li_list):
            headers = get_headers()  
            futures.append(executor.submit(download_chapter, div, headers, save_path, book_id, titles, i, total))
        
        # 使用进度条
        for _ in tqdm(as_completed(futures), total=total, desc="下载进度"):
            pass  

def main():
    book_id = input("欢迎使用番茄小说下载器精简版！\n作者：Dlmos（Dlmily）\n基于DlmOS驱动\nGithub：https://github.com/Dlmily/Tomato-Novel-Downloader-Lite\n参考代码：https://github.com/ying-ck/fanqienovel-downloader/blob/main/src/ref_main.py\n\n请输入小说 ID：")
    save_path = input("请输入保存路径：")
    
    Run(book_id, save_path)
    print("下载完成！")

if __name__ == "__main__":
    main()