# Windows 桌面单行阅读器

一个支持本地 UTF-8 TXT 与微信读书网页版的 PySide6 悬浮阅读器。窗口无边框、始终置顶，移入鼠标时显示，移开后降到 5% 透明度。

## 安装与启动

建议使用 Python 3.10 或更高版本：

```powershell
pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

如果 `python` 指向不兼容的 MSYS2 解释器，可改用 Windows Python Launcher（本机已用 3.13 验证）：

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 -m playwright install chromium
py -3.13 main.py
```

以上命令都应在项目虚拟环境中执行；不要为 LineRead 修改全局 Python。首次启动会弹出 TXT 文件选择框。之后直接启动会读取 `config.json` 中记录的来源；也可以在命令行指定另一本 TXT：

```powershell
python main.py "D:\Books\example.txt"
```

要直接连接微信读书，可使用：

```powershell
python main.py --weread
```

TXT 必须使用 UTF-8 编码（支持带 BOM 的 UTF-8）。文件不存在、为空、编码错误或配置损坏时，程序会给出提示或回退到安全默认设置。

## 操作

- 滚轮向下：下一条
- 滚轮向上：上一条
- `Ctrl + 滚轮`：字号增减，范围 10–40（默认绑定，可修改）
- `Shift + 滚轮`：鼠标移入时的透明度增减，范围 0.2–1.0（默认绑定，可修改）
- 按住鼠标左键拖动：移动悬浮窗
- 鼠标右键：打开随当前来源变化的菜单
- `Alt + F4`：关闭并保存阅读进度

设置页可以查看完整操作说明、直接修改字号与可见透明度，并为“调整字号”和“调整透明度”分别选择 `Ctrl`、`Shift` 或 `Alt` + 滚轮。两个操作不能使用相同修饰键；保存后立即生效。

TXT 模式只显示“打开文件”“阅读详情”“设置”和“退出”，所有微信读书运行态菜单都会隐藏。使用 `python main.py --weread` 首次进入微信读书模式。新 TXT 成功加载后会从第一条开始，并立即保存新文件路径；取消选择或文件加载失败不会覆盖当前阅读内容。

## 微信读书

LineRead 使用 Playwright 控制微信读书公开网页版。普通章节直接读取已经渲染的 DOM；正式正文以 Canvas 显示时，通过页面自带控件切换到纵向布局，以 2 倍设备像素截取完整章节 Canvas，再按带重叠的纵向切片运行本地 RapidOCR。OCR 会适度扩张文本检测框以保留行尾字形边缘；切片结果按原图坐标合并去重，低置信度文字行会放大后重试，并且只在重试结果置信度更高时替换原文。它不读取浏览器缓存、不拦截私有接口，也不尝试解密微信读书数据，截图和正文不会上传到 OCR 服务。

首次连接时会打开独立的 Chromium 窗口。请扫码登录并手动进入一本书，再回到提示框点击“确定”。LineRead 会从微信读书目录的当前选中项定位章节，一次性提取并缓存正文，之后逐行阅读不依赖网页当前显示状态；读完最后一行会点击目录中的下一项并重新缓存。首次 Canvas OCR 需要加载本地模型，通常比后续章节慢。浏览器必须在 LineRead 运行期间保持开启，但可以置于后台或最小化。

微信读书模式右键菜单为“打开微信读书”“切换书籍”“上一章”“下一章”“阅读详情”“设置”“退出”。切换书籍时，先在恢复的浏览器窗口中手动进入新书，再点击提示框“确定”。LineRead 会按 `book_id/chapter_id/line_index` 恢复每本书的位置；`chapter_id` 根据目录层级和标题生成，不受扉页等动态目录项增删影响。旧版本生成的章节哈希会在下次读取时自动迁移到新格式。

Playwright 的持久浏览器配置默认保存在 `%LOCALAPPDATA%\LineRead\WeReadProfile`，用于尽量保留登录状态。可用 `LINEREAD_WEREAD_PROFILE` 环境变量覆盖路径。微信读书网页结构更新后，章节选择器可能需要同步调整；此时 LineRead 会显示错误，不会改写已有阅读位置。

在 Windows 安全策略拒绝启动 Playwright 下载的有界面 Chromium（`spawn UNKNOWN`），或其可执行文件暂时不存在时，LineRead 会自动回退到本机安装的 Google Chrome，并继续使用上述独立配置目录；不会复用或修改日常 Chrome 用户配置。其他启动错误不会被回退逻辑掩盖，弹窗会显示真实底层原因。

阅读来源、TXT 进度、每本微信读书的章节/行号、窗口位置、宽度、字号、可见透明度和滚轮快捷配置保存在项目目录下的 `config.json`。该文件是仅供本机使用且已被 Git 忽略的运行状态；仓库中的 `config.example.json` 提供不含个人数据的默认配置示例。设置页仍只负责字体、字号、透明度和快捷操作，不包含切书或章节切换。

## 项目结构

- `main.py`：应用启动、文件选择和错误提示
- `reader_window.py`：悬浮窗显示与桌面交互
- `settings_dialog.py`：右键设置页与快捷配置校验
- `text_parser.py`：数据源抽象、TXT 加载和中文标点切分
- `weread_source.py`：Playwright 持久会话、目录章节控制、DOM/Canvas OCR 提取与章节缓存
- `reading_details_dialog.py`：当前来源、书籍/章节和行号的只读详情
- `config.py`：JSON 配置读取、校验和原子保存
- `tests/`：解析、配置和窗口行为测试
