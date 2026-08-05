# VideoLib 领域词汇表 (CONTEXT)

本文件是架构讨论与代码评审的共同语言。术语以代码中的实际命名为准，注释/提交信息应使用这些词。

## 核心概念

| 术语 | 英文 | 含义 | 对应模块 |
|------|------|------|----------|
| 视频 | Video | 一条视频文件的元数据记录（文件名/路径/大小/mtime/时长/分辨率/编码/缩略图） | `domain/models.py` |
| 库 | Library | 所有已索引视频的集合，以及「与文件系统保持同步」这一操作的归属地 | `services/library.py` |
| 同步 | apply_sync | 把一批文件系统事件并入库的操作：探测元数据→upsert→删失效行+缩略图；取消时跳过删除 | `Library.apply_sync` |
| 扫描根 | scan root | 用户索引过的目录（`scan_roots` 表）；stale 清理只限当前 root 内 | `Repository.scan_roots` |
| 增量监控 | watcher | watchdog 对最后扫描目录的 2s 防抖增量同步，事件批 → apply_sync | `services/watcher.py` |
| 视图 | ViewKind | 四视图：当前目录/所有目录/收藏夹/最近播放；视图→查询的映射在模型中 | `ui/video_list.py` |
| 分类 | category | 按扫描根隔离的层级标签树；跨 root 分配被拦截 | `Repository.categories` |
| 收藏夹 | favorite list | 命名收藏夹（`收藏夹_***` 前缀），视频可入多个夹 | `Repository.favorite_lists` |
| 缩略图 | thumbnail | `{id}.jpg`，id 是视频行主键；**删视频必须删缩略图**（id 复用会显示旧图） | `services/thumbnailer.py` |
| 断点续播 | resume position | `play_history` 末条 position；<5s 的关闭不覆盖断点 | `ui/player.py` |

## 架构语言（与 codebase-design 技能共用）

- **模块/接口/实现/深度**：如 `Repository`（深——线程安全+分块+迁移藏在 RLock 之后）、`Library`（深——同步+删除不变式收敛为 3 个方法）
- **Seam**：UI 适配器（ScanWorker/WatcherThread）与库引擎之间；`Library` 无状态、随处构造
- **不变式（invariant）**：① 改 videos 表必须重建 FTS（`_finish_videos_write`）；② 删视频连带删缩略图（`Library.remove_paths/remove_root`）

## 铁律（从 PROGRESS.md 提炼）

- UI 只调 services/repository；repository 只碰 SQLite。
- 任何 videos 写路径末尾必须 `_finish_videos_write()`。
- QThread 局部变量必须 parent + 强引用；`QTimer.singleShot(0, bound_method)` 禁止（窗口销毁后触发 → qFatal）。
- 列宽/伸缩模式配置必须在 `setModel` 之后。
