# Windows 桌面单行阅读器

一个专注本地 UTF-8 TXT 的 PySide6 悬浮阅读 MVP。窗口无边框、始终置顶，移入鼠标时显示，移开后降到 5% 透明度。

## 安装与启动

建议使用 Python 3.10 或更高版本：

```powershell
pip install -r requirements.txt
python main.py
```

如果 `python` 指向不兼容的 MSYS2 解释器，可改用 Windows Python Launcher（本机已用 3.13 验证）：

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 main.py
```

首次启动会弹出 TXT 文件选择框。之后直接启动会读取 `config.json` 中记录的文件；也可以在命令行指定另一本书：

```powershell
python main.py "D:\Books\example.txt"
```

TXT 必须使用 UTF-8 编码（支持带 BOM 的 UTF-8）。文件不存在、为空、编码错误或配置损坏时，程序会给出提示或回退到安全默认设置。

## 操作

- 滚轮向下：下一条
- 滚轮向上：上一条
- `Ctrl + 滚轮`：字号增减，范围 10–40（默认绑定，可修改）
- `Shift + 滚轮`：鼠标移入时的透明度增减，范围 0.2–1.0（默认绑定，可修改）
- 按住鼠标左键拖动：移动悬浮窗
- 鼠标右键：打开菜单，可更换 TXT、进入设置页或退出
- `Alt + F4`：关闭并保存阅读进度

设置页可以查看完整操作说明、直接修改字号与可见透明度，并为“调整字号”和“调整透明度”分别选择 `Ctrl`、`Shift` 或 `Alt` + 滚轮。两个操作不能使用相同修饰键；保存后立即生效。

在右键菜单中选择“打开 TXT…”即可更换阅读文本。新文件成功加载后会从第一条开始，并立即保存新文件路径；取消选择或文件加载失败不会覆盖当前阅读内容。

阅读进度、当前文件、窗口位置、宽度、字号、可见透明度和滚轮快捷配置保存在项目目录下的 `config.json`。

## 项目结构

- `main.py`：应用启动、文件选择和错误提示
- `reader_window.py`：悬浮窗显示与桌面交互
- `settings_dialog.py`：右键设置页与快捷配置校验
- `text_parser.py`：数据源抽象、TXT 加载和中文标点切分
- `config.py`：JSON 配置读取、校验和原子保存
- `tests/`：解析、配置和窗口行为测试
