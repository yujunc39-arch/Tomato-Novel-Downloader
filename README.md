# 番茄小说下载器精简版
如你所见，这个程序只有不到12kb的python文件，但这不影响它的功能！这个程序简单易操作，可以满足你的小说下载需求。
## 我该如何使用？
你可以通过输入书籍id以及需要保存的路径来进行下载
你还需要依次输入以下的命令来保证程序的运行：
```bash
sed -i 's@^\(.*deb.*stable main\)$@#\1\ndeb https://mirrors.tuna.tsinghua.edu.cn/termux/apt/termux-main stable main@' $PREFIX/etc/apt/sources.list
apt update && apt upgrade
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pip install requests beautifulsoup4 lxml tqdm
```
## 常见问题
1.`之前就已经有了一个下载器，为什么还要再做一个？`

本程序的初衷就是极致简化番茄小说下载器的代码，使程序更加易于操作与运行，并且更加稳定和快速！

2.`为什么我在安装lxml库的时候始终安装不了？`

按照以下步骤解决：
```bash
apt install clang 
apt install libxml2
apt install libxslt 
pip install cython 
CFLAGS="-O0" pip install lxml
```

3.`手机端可以正常运行吗？`

可以正常运行，为了防止有些零基础的小白下载到了此程序，我们为您准备了一些教程：

*下载termux(链接:https://github.com/termux/termux-app/releases/tag/v0.118.1 )，用文件管理器，找到自己下载的源代码(2.py)，复制当前的目录，返回termux，输入cd+空格+复制的目录，然后回车，最后输入`python 2.py`

再次回车即可。

## 注意事项
由于使用的是api，所以未来不知道有哪一天突然失效，如果真的出现了，请立即在“Issues”页面中回复！

## 赞助/了解新产品
https://afdian.com/a/dlbaokanluntanos

## 免责声明
  本程序仅供 Python 网络爬虫技术、网页数据处理及相关研究的学习用途。请勿将其用于任何违反法律法规或侵犯他人权益的活动。
  
  使用本程序的用户需自行承担由此引发的任何法律责任和风险。程序的作者及项目贡献者不对因使用本程序所造成的任何损失、损害或法律后果负责。
  
  在使用本程序之前，请确保您遵守适用的法律法规以及目标网站的使用政策。如有任何疑问或顾虑，请咨询专业法律顾问。
