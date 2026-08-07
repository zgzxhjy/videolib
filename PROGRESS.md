# VideoLib 开发进度交接文档

> 最后更新：2026-08-07（会话 16：循环:关 播完停在最后一帧——移除队列自然结束自动续播，全部循环改 modulo 推进回卷）
> 续接方式：新会话开头说「继续开发 D:\videolib 的 VideoLib，先读 PROGRESS.md」

## 1. 项目概览

**D:\videolib** —— PyQt6 桌面视频管理工具，管理硬盘视频文件，支持快捷搜索与播放。

- 语言/框架：Python 3.14 + PyQt6 6.11 + PyAV 18 + SQLite（WAL+FTS5）+ watchdog
- 打包：PyInstaller 6.21 onefile → `dist\VideoLib.exe`（~83MB，免 Python 环境）
- 测试：pytest，204 个用例全绿
- git：44 个提交，分支 master

## 2. 已实现功能（全部可用）

| 模块 | 说明 |
|------|------|
| 索引 | 全量扫描（后台线程）+ watchdog 增量监控（2s 防抖），扫描目录持久化到 settings.json |
| 多根目录 | 库 = 所有扫描过目录的并集；stale 清理只限当前 root 内，切换目录不删其他目录数据（收藏/分类安全）；`scan_roots` 表记忆历史目录，工具栏「历史目录」下拉一键跳转+增量重扫；**菜单底部「删除历史记录...」→ 删除模式对话框：「仅移除记录」（视频保留）/「删除并清除数据」（删该 root 全部视频，收藏/分类级联清除，二次确认）；删除的是监控目录时自动停 watcher 并清设置** |
| 当前目录视图 | 列表默认绑定当前扫描目录（「当前目录」按钮）；扫描新目录/点历史目录时**先跳转再扫描**（列表立刻显示该目录已知数据，后台增量同步）；工具栏另有「所有目录」并集视图、分类树根节点=当前目录名；**启动默认「所有目录」页** |
| 增量扫描 | `diff_scan` 按 size+mtime 比对，未变化文件跳过 PyAV 探测（重扫已记忆目录秒级完成）；video_id 稳定 → 缩略图复用不重生成；**空目录（无视频文件）不落库：不注册 scan_roots/不动现有数据，跳回上一目录并弹窗提示** |
| 元数据 | PyAV 提取时长/分辨率/编码，全部入库 |
| 搜索 | FTS5 全文 + LIKE 兜底（支持中文），300ms 防抖搜索栏 |
| 分类 | 层级树（右键增删改、禁止移入自身子树），**分类按扫描目录隔离**（`categories.root` 列，切换目录树即切换）；遗留旧分类启动时一次性收养到 watch_root；跨目录分配被拦截 |
| 播放 | mpv.exe 子进程内核（named pipe IPC + child hwnd d3d11 渲染，`services/mpv_session.py` + `ui/video_widget.py`），断点续播（loadfile start=）+ 播放历史（最近播放视图），倍速/音量/静音/循环/全屏/队列不变 |
| 收藏 | 命名收藏夹（`收藏夹_***`，自动补前缀）：工具栏「收藏夹」下拉切换/新建/**删除（菜单底部「删除收藏夹...」→ 删除模式对话框，确认后记录清空、视频文件不受影响）**；右键「添加到收藏夹...」选夹（可内联新建，重名提示）、「从收藏夹移除...」只列含该视频的夹；旧单表收藏自动迁移到「收藏夹_默认」 |
| 缩略图 | 纯 PyAV 生成（170x96，16:9 裁切填满），懒生成 + 4 线程池，孤儿清理，失败写日志 |
| 列表 | 虚拟滚动，行内「▶ 播放」按钮列（hover/press 反馈，**列宽随按钮文字自适应**），工具栏播放按钮，Enter/Space 快捷键，双击/右键播放；**长文件名 wordWrap 换行显示（不省略号），超长标题行高自动增长（TitleWrapDelegate sizeHint 按真实列宽算换行高度，行高 = max(96 缩略图, 换行文本)）** |
| 批量操作 | 批量收藏、批量加/移分类 |
| 清理所有目录 | 工具栏「清理所有目录」：两次确认 → 后台清空库内**全部**视频条目+缩略图（级联清播放历史/收藏关联/分类关联），**源文件不动、历史目录(scan_roots)与备份保留**，进度对话框可取消；`DeleteWorker(root=None)` 复用同一 worker（root=None=清空全部，跳过 remove_scan_root）；被删残留（root 记录已不在的孤儿条目）也能用此功能清掉 |
| 设置 | 工具栏「设置」对话框：主题（跟随系统/浅色/深色，确定即时应用 + 重启记忆）、音量（与播放器共用的记忆值）、备份保留份数（`backup_keep`，默认 5，`_rotate` 动态读取） |
| 其他 | 打开所在文件夹（explorer /select）、状态栏提示 |
| 扫描进度 | 非模态 QProgressDialog（WindowModal）：枚举阶段忙碌态→元数据阶段 i/n 进度+当前文件名，可取消（保留已处理部分、跳过 stale 清理），扫描期间防重入 |

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
│   ├── player.py            # 播放窗口（MpvSession 会话，断点续播、closeEvent 记录位置）
│   ├── video_widget.py      # 视频容器（Win32 child hwnd 承载 mpv d3d11 渲染）
│   ├── category_tree.py     # 分类树
│   ├── scan_worker.py       # ScanWorker(QThread)
│   ├── search_bar.py        # 防抖搜索
│   └── dialogs/pick_category.py、pick_favorite_list.py、pick_scan_root.py
└── tests/                   # test_repository / test_scan / test_watcher / test_favorite_dialog，47 用例
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
8. **跨 root 路径匹配**：`existing_under` 用 `substr(filepath,1,length(?))=?`（前缀=normpath(root)+`\`），避免 `E:\zmk` 误匹配 `E:\zmk2`（LIKE 需转义，substr 无需）。
9. **增量跳过判定**：size+mtime 双条件，mtime 用 1s 容差（NTFS 精度）；老库迁移 `ALTER TABLE ADD COLUMN` 用 `PRAGMA table_info` 判缺列（SCHEMA 的 CREATE IF NOT EXISTS 不会补列）。
10. **目录不存在守卫**：`_start_scan` 先 `os.path.isdir`，防止移动硬盘未挂载时扫出 0 文件把该 root 记录全清。
11. **空目录语义**：扫描目录 0 个视频 → ScanWorker `done("empty")` 提前返回（不 upsert/不删 stale/不注册），MainWindow 跳回 prev_root 弹窗。注意：若目录之前有视频、之后被清空，重扫会因 empty 提前返回而**不清理**旧记录（有意为之）。
12. **收藏夹迁移**：旧 `favorites` 单表在 Repository 初始化时迁入 `收藏夹_默认` 后 DROP；`done` 信号已从 `bool` 改为 `str`（"ok"/"empty"/"cancel"）。
13. **PyQt6 `QListWidget.addItem(str)` 返回 None**：C++ 重载返回 void，拿返回值会 `AttributeError`，且异常逃逸出 slot 时 PyQt6 走 `qFatal` 直接闪退（windowed 无输出）→ 必须用 `self.list.item(self.list.count()-1).setData(...)` 索引模式；slot 里永远不要依赖默认异常处理。
14. **菜单按钮可见性即状态**：菜单每次重建才刷新 → 「删除收藏夹...」仅在存在收藏夹时显示、「删除历史记录...」仅在有历史时显示；对话框按钮用两个显式按钮（「仅移除记录」/「删除并清除数据」）而非选择器弹窗，测试直接用 `_delete_only`/`_delete_with_data` 驱动。
15. **QHeaderView 丢弃 setModel 前的 section 配置**：不只是 `setColumnWidth`（坑 #5），`setSectionResizeMode(Stretch/ResizeToContents)` 在 setModel 之前设置同样全部失效 → 标题列曾静默退化为默认 100px 宽、长标题挤成碎片。铁律：**任何列宽/伸缩模式配置必须在 setModel 之后**。
16. **纵向 ResizeToContents 会查询所有列**：行高计算时 QHeaderView 对所有列 delegate 的 sizeHint 取 max（实测第 1 列的 TitleWrapDelegate 会被调用，且 sizeHint 的 option.rect.width() 是真实列宽）→ 行高自适应只需给目标列挂 sizeHint-aware delegate + 纵向 ResizeToContents + 横向列宽变化后防抖 resizeSections。
17. **Qt 换行断词规则**：TextWordWrap 只按可断点换行——CJK 每字可断（中文文件名正常换行），纯拉丁长串（无空格）视为不可断词不换行 → 测试换行必须用中文文本，`'x'*300` 测不出来（返回单行高）。
18. **ResizeToContents 对纯自绘列塌缩**：播放列只有自绘 delegate、无 DisplayRole 文本 → 默认 sizeHint 为空 → 列宽塌缩到 28px 按钮被挤扁（列宽配置真正生效后才暴露）。铁律：**自绘 delegate（无文本/图标）必须覆盖 sizeHint**（返回按钮文字宽 + 内边距），横向表头会按列 delegate 的 sizeHint 定宽。
19. **PyQt6.11 `sortIndicatorOrder()` 返回枚举实例**：`int(...)` 抛 `TypeError`，且发生在 `closeEvent` 槽内 → qFatal 闪退（0xC0000409 无输出）。必须 `.value`。铁律：**closeEvent/槽内新增代码里对 Qt 枚举做 `int()` 前先验证 PyQt6.11 行为**（`sortIndicatorSection()` 是普通 int，`sortIndicatorOrder()` 是枚举）。
20. **成员属性必须在 `__init__` 初始化**：`_cleanup_thread` 只在 0ms 定时器触发后才赋值；窗口未经过任何事件循环就 close → `closeEvent` 里 `self._cleanup_thread is not None` 抛 `AttributeError` → 槽内异常 → qFatal。铁律：**closeEvent 会引用的属性一律在 `__init__` 置 None**。
21. **parented 0ms 定时器 + 引用环 → 已关闭窗口的定时器在后续事件循环里触发**：`QTimer(self)` 持父引用、窗口属性又持 QTimer → 形成引用环，窗口不随引用计数销毁（靠 GC 才回收）；窗口存活期内无事件循环 → 0ms 定时器一直未触发 → 下一次 `processEvents()` 会调到已关闭窗口的 `_start_orphan_cleanup`，用已 close 的旧 repo 起清理线程 → 线程内 sqlite 异常 → QThread 未捕获异常 → qFatal。修复：**closeEvent 里 `_cleanup_timer.stop()` + 等待运行中的清理线程（对齐 `_stop_watcher`）**。测试表现特征：同一测试函数内建第二个窗口不崩、跨测试必崩（上一测试的窗口毒害下一测试的 `processEvents`）。
22. **Qt 枚举驼峰拼写错误 → 事件过滤器内 AttributeError → qFatal**：`QEvent.Type.MouseButtonDBlClick` 不存在（正确拼写 `MouseButtonDblClick`），且发生在 eventFilter 回调内（同 slot）→ 0xC0000409 无输出。铁律：**事件过滤器/槽内新增代码里的 Qt 枚举属性名，先 `dir(QEvent.Type)` 验证拼写**。
23. **Python 3.14 pathlib 的 `str(WindowsPath)` 返回反斜杠**：`str(Path('C:/x'))` → `'C:\\x'`（3.14 pathlib 重构）；而 Qt `QUrl.fromLocalFile().toLocalFile()` 往返**保留正斜杠**。测试断言跨这两者时必须显式统一分隔符（`replace('\\','/')`），不能直接比较。
24. **测试必须隔离 SETTINGS_PATH**：播放器音量记忆后 `closeEvent` 写真实 `~/.videolib/settings.json`（player 测试曾把用户音量改成 75）。铁律：**任何会写 settings 的新代码，相关测试 fixture 必须 monkeypatch `config.SETTINGS_PATH` 到 tmp**（对照 main_window 的 app_env）。
25. **ctypes WNDPROC cast 后对象被 GC → CreateWindowExW 回调悬垂 → access violation（0xC0000409）**：`wc.lpfnWndProc = ctypes.cast(WNDPROC(wndproc), c_void_p)` 只拷贝裸指针，函数返回后 WNDPROC 对象无引用即回收。修复：**wndproc 提为模块级函数 + WNDPROC 实例模块级持有 + 窗口类只注册一次**。症状：构造 PlayerWindow 时崩、独立 MpvSession 冒烟却偶尔不崩（GC 时序敏感）。
26. **named pipe 客户端必须轮询 connect**：`WaitNamedPipeW` 在 **pipe 不存在时立即返回失败（不等待）**，而 mpv 冷启动（d3d11 init）需 1-3 秒才建 pipe → 一次调用必然 FileNotFoundError。修复：循环 WaitNamedPipeW(250)/CreateFileW + 150ms 退避至 deadline。
27. **同步 ReadFile 阻塞会锁死同句柄另一线程的 WriteFile**：读线程先阻塞 `ReadFile`（等 mpv 事件）后，主线程 `WriteFile` 永远不返回（mpv 日志停在 "Client connected"，命令根本没送达）。修复：**读侧永不阻塞**——`PeekNamedPipe` 探测有数据才 ReadFile，无数据 sleep 50ms 轮询；ReadFile 一次读入的多行 JSON 要按行拆分逐条消费（time-pos 事件 100ms 一个会堆积）。
28. **mpv 0.38+ `loadfile` 签名变化（断点续播失效）**：签名变为 `loadfile <url> [flags [index [options]]]`——options 是第 4 参，**必须用 `-1` 占位 index**；旧格式 `["loadfile", path, "replace", {"start": 5.0}]` 把 dict 顶到 index 位 → 报 `Command loadfile: argument index has incompatible type` → **加载被拒、黑屏且无任何 UI 报错**（44 个会话日志中 13 个命中，恰是带断点的视频，~30% 概率复现）。修复：`resume>0` 时发 `["loadfile", path, "replace", -1, {"start": f"{round(resume,3)}"}]`（options 为 map 可接受，但**值必须是字符串**，`{"start": 5}` 同样 invalid）。铁律：**loadfile 带 options 必须显式 `-1` 占位 + 值字符串化**。
29. **Win32 子窗口尺寸必须物理像素**：`CreateWindowExW`/`SetWindowPos` 用物理坐标，而 Qt 的 `resize()`/geometry 是逻辑像素。125% 缩放屏（mpv 日志 `DPI detected from the new API: 120`）上直接用逻辑尺寸建 child → child 只有容器的 80%（956x496 vs 应有 1195x620）→ **画面贴左上角、右边/下方大段空白**（mpv 渲染区 = child 物理尺寸，跟随 resize 正常，问题只在建窗尺寸）。修复：`ensure_child`/`resizeEvent` 尺寸乘 `devicePixelRatio()`。
30. **named pipe 字节流会被 4096 块切半行**：按行拆分时若 `\n` 恰在块边界之后，半行残留会导致后续消息**永久错位**（事件静默丢失，且首条错误对不上号）。修复：read 侧留 `_read_buf` 拼半行，`split(b"\n")` 前先把 buf 与残留拼接，末尾无 `\n` 的残段留到下轮。
31. **删除历史目录（删除并清除数据）曾是 UI 线程同步执行**：整库备份 + 删行 + 逐文件删缩略图，大目录冻结几十秒无反馈；中途退出会留下 FTS 索引残留（行已删但搜索仍显示幽灵条目）且无入口再清理。修复：**DeleteWorker(QThread) + 进度对话框（同扫描样式，可取消）**，scan_root 记录**最后一步才删**——root 记录是「可重试标记」，中断/取消后重新选择即可幂等清完。配套两个自愈：①启动时 `_heal_fts`（videos 与 videos_fts COUNT 不一致则重建，杜绝搜索幽灵条目）；②孤儿缩略图清理原有逻辑兜底。
32. **PyQt6 信号有类型强校验**：`pyqtSignal(int, int, str)` 上 emit `Path`（不是 str）直接抛 TypeError，且若在 worker 的 `except Exception` 里被吞掉 → 任务「成功结束」但阶段没跑完，无任何迹象（实测 root_removed 变 False、进度条无更新）。铁律：**emit 前显式 `str()`**；worker 的 except 分支必须发独立 `error` 信号让 UI 可见，不能只用 message。
33. **换机兼容性两个坑**：①`restoreGeometry` 会把别的分辨率/显示器布局下保存的几何原样恢复——整窗开在屏幕外且无把手拖回（player 的 `_fit_size` 有 clamp，主窗口没有）。修复：恢复后若 `frameGeometry` 与任何屏幕的 `availableGeometry` 无交集 → 移到所在屏中央。②扫描根是绝对路径（含盘符），换机/换盘后全部失效；之前只有 watcher 静默跳过。修复：启动 0ms 定时器检查 `_missing_scan_roots()`，非空弹非模态警告（`QMessageBox.open()` 而非静态 `warning()`——后者嵌套 exec 会卡住测试，且打开即被其他测试的 `processEvents` 触发）。测试注意：`register_scan` 会 normpath（正斜杠变反斜杠），断言必须 `os.path.normpath`。
34. **主题切换测试不能对 session QApplication 调 `setStyleSheet`**：全量跑测试时前面的用例留下大量未销毁 widget（引用环/延迟 GC），`apply_theme` → `app.setStyleSheet` 会重刷整棵 widget 树，碰到半拆除对象 → access violation（单独跑 test_settings_dialog 不崩、全量必崩）。修复：测试 patch 掉 `apply_theme` 只验证「被调用 + 设置已写」，settings→qss 的映射交给 test_theme 纯函数覆盖。生产代码正常（应用内 widget 树健康）。另：主题三态 `system`/`light`/`dark`，`app_qss(app, scheme=None)` 显式参数 > 设置 > 系统检测；`backup_keep` 设置化后 `_rotate` 默认参数从常量改为读 settings（默认 5 不变）。
35. **keep-open 下的 EOF 信号跟 mpv 版本走（实测 mpv v0.41.0-744）**：`--keep-open=yes` 时自然播完**不发 `end-file(reason=eof)`**，只发 `pause=True` + `eof-reached=True` 两个属性变更（`end-file stop` 只在被 loadfile 打断时发）——原代码只认 end-file(eof)，导致 `endOfMedia` 永不触发：单曲/全部循环、队列自动续播全部失效，视频停最后一帧。另发现第二个坑：**loadfile 继承当前 pause 状态**（keep-open 在 EOF 置了 pause=yes，新加载的视频以暂停态启动 → 「卡住」，连点两下暂停是按钮文案与异步属性变更不同步的假象）。修复三件套：①file-loaded 时 `observe_property 4 eof-reached`，`data is True` → emit endOfMedia（False/None 忽略——新文件加载时 eof-reached 会短暂变 None）；②`MpvSession.load` 末尾显式 `set pause no`，约定「load 即以播放态启动」（实测：set pause no + loadfile 正常播放）；③`PlayerWindow` 连接 stateChanged 同步播放按钮文案。用真实 mpv + named pipe 实证（lavfi:// 协议没编译进构建，改用 pytest 残留的 1s 测试视频），另：测试视频残留 `%TEMP%\pytest-of-Administrator\**\v0.mp4`。


## 6. 验证手段（可复用）

- 离屏渲染测量缩略图绘制区域：`MainWindow` + `viewport().grab()` + 扫描非背景像素 bbox（当前应 ≈166x96）
- exe 验证流程：清空 thumbs → 启动 exe → 等 40s → 检查 thumbs 目录新 jpg + 用 PyAV 解码确认尺寸

## 7. 待办 / 后续方向

- [x] 播放内核迁移 mpv（会话 8/9）：QMediaPlayer 换 mpv.exe 子进程（`vendor/mpv/mpv.exe` 117MB 静态单文件，named pipe IPC，Win32 child hwnd 承载 d3d11 渲染；Qt top-level hwnd 直连会 0xC0000409，必须自建 child）；打包已 `--add-data` 收集 mpv.exe
- [ ] HiDPI：缩略图 2x 生成（340x192）+ QIcon dpr，当前高分屏略糊
- [ ] 中文搜索优化：拼音首字母索引或 jieba 分词
- [x] 扫描进度 UI：QProgressDialog + 取消（会话 3），ScanWorker 增加 `progress(done,total,fp)` / `done(bool)` / `cancel()`
- [x] 多根目录 + 增量扫描 + 分类按目录隔离（会话 3）：`scan_roots` 表、`file_mtime` 列、`categories.root` 列，老库自动迁移；历史目录下拉重扫
- [x] 列表绑定当前目录 + 先跳转再扫描（会话 3）：「当前目录/最近播放/收藏夹/所有目录」四视图，分类树根=当前目录名
- [x] 交互优化（会话 4）：长文件名换行、空目录不落库跳回弹窗、启动默认所有目录页、命名收藏夹（收藏夹_*** 下拉切换+选夹添加+旧数据迁移）
- [x] 删除功能（会话 4）：收藏夹删除（菜单底部入口+删除模式对话框）；历史目录删除（「仅移除记录」/「删除并清除数据」双选项对话框；`remove_scan_root`+`remove_videos_under`；删监控目录自动停 watcher）
- [x] 长标题换行修复（会话 4）：根因 = 列宽配置在 setModel 前被 QHeaderView 丢弃，标题列只有 100px；列宽配置移入 setModel 之后 + TitleWrapDelegate 行高自适应 + 50 用例
- [x] 播放列宽度修复（会话 4）：ResizeToContents 对纯自绘列塌缩到 28px，按钮被挤扁 → PlayButtonDelegate.sizeHint 按文字自适应（73px）+ 52 用例
- [ ] 多目录同时监控：当前 watcher 只监控最后扫描的目录，其他目录的变更需手动重扫
- [x] 删除视频文件功能（会话 4 已做「删除并清除数据」的历史目录级删除；单个文件级删除仍缺，当前只有打开所在文件夹）
- [x] 窗口/列宽记忆（会话 5）：c清Event 保存 window_geometry + column_widths（仅恢复 Interactive 列）+ 排序状态
- [x] 排序（会话 5）：_natkey 自然键（v2 < v10）+ 表头点击 + 持久化；sort() 用 layoutChanged 重映射（reset 会递归崩）
- [x] 断点标记（会话 5）：last_positions 批量查询 + 时长列 ⏵ 前缀（5s < pos < 90% 时长）+ tooltip 续播位置
- [x] 播放队列（会话 5）：PlayerWindow(queue=...) + ⏮⎭ 按钮（循环:全部 播完续播回卷，_closing 防误触发；会话 16 起循环:关 播完停在最后一帧不再自动续播）
- [x] 最近播放去重（会话 6）：play_history 每视频一行（video_id UNIQUE），record_play 改 UPDATE-then-INSERT（rowid 自增式 bump 保持同秒排序），老库自动去重迁移（保留每视频 MAX(id) 行=最新一次播放）；last_position/last_positions 简化（恒一行）
- [x] 清空播放历史（会话 6）：工具栏「清空播放历史」按钮（紧挨「最近播放」）+ QMessageBox.question 二次确认；`Repository.clear_play_history()`（DELETE 全表，断点续播位置一并清除，不经 FTS）；`_refresh_all` 后 RECENT 视图即时变空、⏵ 标记消失；测试 88 用例
- [x] 播放器增强（会话 7）：倍速按钮（0.5x/1x/1.25x/1.5x/2x 循环，`setPlaybackRate`，R 快捷键，切视频不重置）；全屏（F 键/双击视频区，eventFilter 实现，Esc 先退全屏再关闭）；音量记忆（settings.json "volume" 键，closeEvent 保存，默认 80）
- [x] 右键菜单（会话 7）：「标记为已看完」（`clear_play_position` UPDATE position=0 保留最近条目）、「复制路径」「复制文件名」（多选换行分隔，QApplication.clipboard）
- [x] 库统计面板（会话 7）：`Repository.stats()`（总数/总时长/总大小/各 root 计数/分类计数）+ 工具栏「统计」按钮 + QMessageBox.information
- [x] 拖拽（会话 7）：主窗口 setAcceptDrops（目录→_start_scan 扫描、视频文件→Library.apply_sync 单文件入库）；列表→分类树拖拽归类（model.mimeData 带 MIME_VIDEO_IDS 逗号串 + CategoryTree dropEvent → assign_batch）；测试 99 用例
- [x] 超大库性能（会话 7 核实）：搜索 SEARCH_LIMIT=500 已生效；列表 all_videos 全量加载（虚拟滚动+固定行高），待办已移除

## 8. 遗留问题（如遇到优先排查）

- 无已知未解决 bug；如新增崩溃，先看 `~/.videolib/crash.log` 和 `thumbnails.log`
