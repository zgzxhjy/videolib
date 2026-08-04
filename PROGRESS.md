# VideoLib 开发进度交接文档

> 最后更新：2026-08-04（会话 2 结束）
> 续接方式：新会话开头说「继续开发 D:\videolib 的 VideoLib，先读 PROGRESS.md」

## 1. 项目概览

**D:\videolib** —— PyQt6 桌面视频管理工具，管理硬盘视频文件，支持快捷搜索与播放。

- 语言/框架：Python 3.14 + PyQt6 6.11 + PyAV 18 + SQLite（WAL+FTS5）+ watchdog
- 打包：PyInstaller 6.21 onefile → `dist\VideoLib.exe`（~83MB，免 Python 环境）
- 测试：pytest，25 个用例全绿
- git：9 个提交，工作区干净，分支 master

## 2. 已实现功能（全部可用）

| 模块 | 说明 |
|------|------|
| 索引 | 全量扫描（后台线程）+ watchdog 增量监控（2s 防抖），扫描目录持久化到 settings.json |
| 元数据 | PyAV 提取时长/分辨率/编码，全部入库 |
| 搜索 | FTS5 全文 + LIKE 兜底（支持中文），300ms 防抖搜索栏 |
| 分类 | 层级树（右键增删改、禁止移入自身子树）、批量添加/移除、对话框内可新建分类、移除时只列所属分类 |
| 播放 | QMediaPlayer 播放器，断点续播 + 播放历史（最近播放视图） |
| 收藏 | 收藏夹视图，右键批量收藏 |
| 缩略图 | 纯 PyAV 生成（170x96，16:9 裁切填满），懒生成 + 4 线程池，孤儿清理，失败写日志 |
| 列表 | 虚拟滚动，行内「▶ 播放」按钮列（hover/press 反馈），工具栏播放按钮，Enter/Space 快捷键，双击/右键播放 |
| 批量操作 | 批量收藏、批量加/移分类 |
| 其他 | 打开所在文件夹（explorer /select）、状态栏提示 |

## 3. 目录结构

```
videolib/
├── main.py                  # 入口：faulthandler→crash.log、Repository、MainWindow
├── cli.py                   # CLI：index <目录> / stats（压测用）
├── config.py                # APP_DIR/DB/THUMBS_DIR/扩展名/settings.json 读写
├── build.bat                # 打包命令
├── domain/
│   ├── models.py            # Video/Category/PlayRecord dataclass
│   └── repository.py        # 唯一 SQL 入口（线程安全 RLock，check_same_thread=False）
├── services/
│   ├── scanner.py           # os.walk 收集视频文件
│   ├── metadata.py          # PyAV probe → build_video
│   ├── thumbnailer.py       # THUMB_WIDTH=170 THUMB_HEIGHT=96，filter Graph 裁切，mjpeg 编码
│   └── watcher.py           # watchdog 增量（ready 事件、2s 防抖）
├── ui/
│   ├── main_window.py       # 布局/工具栏/右键菜单/快捷键/扫描与监控编排
│   ├── video_list.py        # VideoTableModel + PlayTableView + PlayButtonDelegate + ThumbRunnable
│   ├── player.py            # 播放窗口（断点续播、closeEvent 记录位置）
│   ├── category_tree.py     # 分类树
│   ├── scan_worker.py       # ScanWorker(QThread)
│   ├── search_bar.py        # 防抖搜索
│   └── dialogs/pick_category.py
└── tests/                   # test_repository / test_scan / test_watcher，25 用例
```

**分层铁律**：UI 只调 services/repository → repository 只碰 SQLite。

## 4. 常用命令

```bat
cd D:\videolib
python main.py                          # 启动 GUI
python cli.py index "E:\zmk"            # 命令行索引
python cli.py stats                     # 库统计
python -m pytest tests -q               # 测试
build.bat                               # 打包 → dist\VideoLib.exe
```

用户数据位置：`~/.videolib/`（videolib.db、thumbs/、settings.json、crash.log、thumbnails.log）。
当前用户库：36 个视频（E:\zmk），测试时注意别污染；用 `VIDEOLIB_HOME` 环境变量可隔离。

## 5. 已解决的坑（新会话必读，防止重踩）

1. **ScanWorker 崩溃（0xC0000409）**：QThread 局部变量被 GC 销毁 → 必须 `parent=self` + `self._scanner` 强引用。WatcherThread/PlayerWindow 同理已存 self。
2. **faulthandler 启动即崩**：`--windowed` 下 `sys.stderr is None`，`faulthandler.enable()` 默认写 stderr 直接抛异常 → 必须 `enable(file=crash_log)` 且 try/except OSError。
3. **exe 里缩略图全静默失败**：`to_image()` 依赖 Pillow 打包缺失，且 `except Exception: return False` 吞错 → 已改为纯 PyAV（reformat + mjpeg 编码），并写 thumbnails.log。
4. **PyAV 18 API 变化**：`VideoFrame.crop()` 已移除、`to_ndarray` 需 numpy（未安装）、模块级 `VideoReformatter` 不存在 → 裁切用 `av.filter.Graph`（crop+scale 滤镜，buffer 滤镜需显式 video_size/pix_fmt/time_base/frame_rate）。
5. **QTableView 缩略图小**：默认 `iconSize` 无效(-1,-1)，图标绘制退化 → 必须 `setIconSize(QSize(170,96))`；**`setColumnWidth` 必须在 `setModel` 之后调用**，否则被丢弃。
6. **测试竞态**：watchdog observer 启动前创建文件会丢事件 → WatcherThread 暴露 `ready` 事件，测试先 wait。
7. **FTS5 中文分词**：unicode61 对中文整段成 token → 搜索实现为 FTS + LIKE 双路（LIKE 转义 `_`/`%`/`\`）。

## 6. 验证手段（可复用）

- 离屏渲染测量缩略图绘制区域：`MainWindow` + `viewport().grab()` + 扫描非背景像素 bbox（当前应 ≈166x96）
- exe 验证流程：清空 thumbs → 启动 exe → 等 40s → 检查 thumbs 目录新 jpg + 用 PyAV 解码确认尺寸

## 7. 待办 / 后续方向

- [ ] 播放内核备选：QMediaPlayer 格式覆盖有限，`ui/player.py` 已预留替换点，可换 mpv（python-mpv）
- [ ] HiDPI：缩略图 2x 生成（340x192）+ QIcon dpr，当前高分屏略糊
- [ ] 中文搜索优化：拼音首字母索引或 jieba 分词
- [ ] 扫描进度 UI：目前仅状态栏文本
- [ ] 删除视频文件功能（当前只有打开所在文件夹）
- [ ] 超大库分页/懒加载（当前 all_videos LIMIT 500）

## 8. 遗留问题（如遇到优先排查）

- 无已知未解决 bug；如新增崩溃，先看 `~/.videolib/crash.log` 和 `thumbnails.log`
