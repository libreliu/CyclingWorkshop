# CyclingWorkshop 架构设计

## 1. 项目定位

CyclingWorkshop 是一个基于 Python + Flask 的 **本地 Web UI 工具**，用于将运动手表（FIT 格式）的轨迹/健康数据叠加到运动相机视频上，生成带有数据可视化的骑行/跑步视频。

**核心原则：Web UI 仅作交互界面，所有文件通过本地路径指定，不做上传/下载。** 后端直接读写用户本地文件系统上的 FIT 文件和视频文件。

---

## 2. 系统架构总览

```
┌─────────────────────────────────────────────────────┐
│                    Browser (SPA)                     │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌───────┐ │
│  │ FIT 解析  │ │ 视频配置  │ │ 叠加预览   │ │ 导出  │ │
│  │ & 可视化  │ │ & 时间同步│ │ & 样式编辑 │ │ & 合成│ │
│  └──────────┘ └──────────┘ └───────────┘ └───────┘ │
└──────────────────────┬──────────────────────────────┘
                       │ REST API + WebSocket
                       │ (纯本地，无文件传输)
┌──────────────────────┴──────────────────────────────┐
│                  Flask Backend                        │
│         (直接读写本地文件系统，无 uploads 目录)         │
│  ┌────────────┐ ┌────────────┐ ┌─────────────────┐  │
│  │ FIT Parser │ │ Overlay    │ │ Render Engine   │  │
│  │ Service    │ │ Designer   │ │ (Frame Pipeline)│  │
│  └────────────┘ └────────────┘ └─────────────────┘  │
│  ┌────────────┐ ┌────────────┐ ┌─────────────────┐  │
│  │ Video      │ │ FFmpeg     │ │ Project Store   │  │
│  │ Analyzer   │ │ Service    │ │ (JSON + SQLite) │  │
│  └────────────┘ └────────────┘ └─────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## 3. 后端模块设计

### 3.1 项目结构

```
CyclingWorkshop/
├── app.py                    # Flask 应用入口
├── config.py                 # 配置（ffmpeg 路径、临时目录等）
├── requirements.txt
├── ARCHITECTURE.md           # 本文件
├── UI_SPEC.md                # UI 规范
├── PLAN.md                   # 实施计划
├── static/                   # 前端静态资源
│   ├── css/
│   ├── js/
│   │   ├── app.js            # 主入口
│   │   ├── api.js            # API 客户端封装
│   │   ├── fit-viewer.js     # FIT 数据可视化
│   │   ├── overlay-editor.js # 叠加层编辑器
│   │   └── preview.js        # 预览渲染
│   └── lib/                  # 第三方库（Leaflet、Chart.js 等）
├── templates/
│   └── index.html            # 单页应用入口
├── services/                 # 核心业务逻辑
│   ├── __init__.py
│   ├── fit_parser.py         # FIT 文件解析
│   ├── video_analyzer.py     # 视频元数据分析（ffprobe）
│   ├── overlay_designer.py   # 叠加层布局/样式管理
│   ├── frame_renderer.py     # 逐帧渲染（Pillow/Cairo）
│   ├── render_pipeline.py    # 渲染管线：frame → ffmpeg pipe → video
│   └── ffmpeg_service.py     # FFmpeg 封装（合成、混流）
├── models/                   # 数据模型
│   ├── __init__.py
│   ├── project.py            # 项目模型
│   ├── fit_data.py           # FIT 数据模型
│   ├── video_config.py       # 视频配置模型
│   └── overlay_template.py   # 叠加层模板模型
├── api/                      # Flask Blueprint API 层
│   ├── __init__.py
│   ├── fit.py                # /api/fit/*
│   ├── video.py              # /api/video/*
│   ├── overlay.py            # /api/overlay/*
│   ├── render.py             # /api/render/*
│   └── project.py            # /api/project/*
├── projects/                 # 项目配置存储（JSON）
└── output/                   # 导出视频输出
```

### 3.2 核心服务详解

#### 3.2.1 FIT Parser Service (`services/fit_parser.py`)

**职责**：解析 FIT 文件，提取时间序列数据。

**依赖**：`fit-tool` (Python 包 `fit_tool`)

**输出数据结构**：

```python
@dataclass
class FitRecord:
    timestamp: datetime          # UTC 时间戳
    latitude: float | None       # 纬度（度）
    longitude: float | None      # 经度（度）
    altitude: float | None       # 海拔（米）
    heart_rate: int | None       # 心率（bpm）
    cadence: int | None          # 踏频（rpm）
    speed: float | None          # 速度（m/s）
    distance: float | None       # 累计距离（m）
    power: int | None            # 功率（瓦）
    temperature: float | None    # 温度（℃）

@dataclass
class FitSession:
    sport: str                   # 运动类型
    start_time: datetime
    total_distance: float
    total_elapsed_time: float
    total_timer_time: float
    total_ascent: float
    total_descent: float
    avg_heart_rate: int | None
    max_heart_rate: int | None
    avg_speed: float | None
    max_speed: float | None
    avg_power: float | None
    max_power: int | None
    records: list[FitRecord]     # 逐秒记录

@dataclass
class FitData:
    sessions: list[FitSession]
    lap_markers: list[datetime]  # 圈标记时间
```

**关键方法**：
- `parse(file_path) -> FitData`：完整解析
- `get_record_at(datetime) -> FitRecord`：按时间查询单条记录（线性插值）
- `get_records_range(start, end) -> list[FitRecord]`：按时间范围查询
- `get_track_coords() -> list[(lat, lon)]`：提取轨迹坐标序列

#### 3.2.2 Video Analyzer Service (`services/video_analyzer.py`)

**职责**：分析视频文件元数据，获取时间/编码信息。

**依赖**：`ffmpeg-python`（调用 ffprobe）

**输出数据结构**：

```python
@dataclass
class VideoInfo:
    file_path: str
    duration: float              # 秒
    width: int
    height: int
    fps: float
    codec: str
    bitrate: int
    start_time: datetime | None  # 文件创建/修改时间（用于时间同步推断）
    frame_count: int
```

**关键方法**：
- `analyze(file_path) -> VideoInfo`
- `extract_frame(file_path, timestamp_ms) -> bytes`：提取单帧为 PNG
- `extract_frames_batch(file_path, timestamps_ms) -> list[bytes]`：批量提取

#### 3.2.3 Overlay Designer Service (`services/overlay_designer.py`)

**职责**：管理叠加层元素的布局、样式、数据绑定。

**设计理念**：叠加层由多个独立的 **Widget** 组成，每个 Widget 有自己的位置、大小、样式和数据源。用户可以通过拖拽调整位置。

**Widget 类型**：

| Widget | 描述 | 数据源 |
|--------|------|--------|
| `MapTrack` | 轨迹地图（带当前位置标记和已走路径高亮） | lat/lon |
| `SpeedGauge` | 速度表（数字+圆弧表盘） | speed |
| `HeartRateGauge` | 心率表（数字+色带） | heart_rate |
| `CadenceGauge` | 踏频表 | cadence |
| `PowerGauge` | 功率表 | power |
| `AltitudeChart` | 海拔剖面图（带当前位置标记） | altitude + distance |
| `ElevationGauge` | 当前海拔数字 | altitude |
| `DistanceCounter` | 累计距离 | distance |
| `TimerDisplay` | 运动时间/总时间 | timestamp |
| `GradientIndicator` | 坡度指示 | altitude 差分 |
| `CustomLabel` | 自定义文字标签 | 静态 |

**Widget 数据结构**：

```python
@dataclass
class WidgetConfig:
    widget_type: str             # Widget 类型名
    x: int                       # 左上角 x（px）
    y: int                       # 左上角 y（px）
    width: int                   # 宽度（px）
    height: int                  # 高度（px）
    opacity: float               # 0.0 ~ 1.0
    data_field: str              # 绑定的 FIT 数据字段
    style: dict                  # 类型特定样式参数
    # MapTrack style: { map_provider, zoom, show_breadcrumb, marker_color }
    # Gauge style: { color, font, show_label, min_val, max_val, arc_color }
    # Chart style: { line_color, fill_color, window_seconds, show_grid }
```

**预设模板**（OverlayTemplate）：

```python
@dataclass
class OverlayTemplate:
    name: str
    description: str
    canvas_width: int            # 叠加层画布宽度
    canvas_height: int           # 叠加层画布高度
    widgets: list[WidgetConfig]
```

内置模板：
- **骑行经典**：左下角轨迹地图 + 右下角速度/心率/踏频三表盘 + 顶部海拔条
- **骑行极简**：左下角小地图 + 右下角速度数字
- **跑步基础**：中心底部速度 + 心率 + 距离
- **越野爬坡**：右上角大海拔剖面 + 坡度指示 + 速度

#### 3.2.4 Frame Renderer Service (`services/frame_renderer.py`)

**职责**：根据 Widget 配置和 FIT 数据，逐帧生成叠加层图像。

**依赖**：Pillow（图像绘制）+ `requests`（地图瓦片）

**渲染管线**：

```
对于每一帧 t:
  1. 根据 t 和视频配置计算 FIT 时间点 fit_time
  2. 从 FitData 查询 fit_time 对应的数据记录
  3. 对每个 Widget:
     a. 查询绑定的数据字段值
     b. 在 Widget 区域内绘制：
        - MapTrack: 渲染离线瓦片地图 + 轨迹路径 + 当前位置
        - Gauge: 绘制表盘背景 + 指针/弧线 + 数字
        - Chart: 绘制曲线 + 当前位置线
  4. 合成所有 Widget 到一张 RGBA 叠加层
  5. 输出 RGBA raw bytes
```

**地图渲染策略**：
- 使用预下载的瓦片地图（离线模式），支持 OpenStreetMap 瓦片格式
- 首次加载时根据轨迹范围预下载所需瓦片，缓存到本地
- 渲染时将瓦片拼接为地图区域，叠加轨迹线和当前位置标记

**性能考虑**：
- 地图瓦片缓存：避免每帧重复下载
- Chart 数据预计算：海拔剖面数据预归一化
- 使用 PIL Image 的 `alpha_composite` 而非逐像素操作
- 可选：使用 `multiprocessing` 并行渲染帧

#### 3.2.5 Render Pipeline Service (`services/render_pipeline.py`)

**职责**：将叠加层帧流与原始视频合成，输出最终视频。

**管线设计**：

```
方案 A（推荐）—— ffmpeg pipe 叠加：
  1. 启动 ffmpeg 子进程，通过 pipe 输入叠加层帧流
  2. ffmpeg 命令使用 overlay 滤镜将叠加层合成到原始视频
  3. 管线：原始视频 ──→ ffmpeg ──→ 输出
                 ↑ overlay 滤镜
            叠加帧流 ──→ /dev/stdin

方案 B —— 两步法：
  1. Python 渲染叠加层帧 → 输出为透明 PNG 序列
  2. ffmpeg -i 原始视频 -i overlay_%05d.png -filter_complex overlay 输出
```

**推荐方案 A 的 ffmpeg 命令**：

```bash
ffmpeg -i input.mp4 \
  -f rawvideo -pix_fmt rgba -s WxH -r FPS \
  -i pipe:0 \
  -filter_complex "[0:v][1:v]overlay=0:0:format=auto" \
  -c:v libx264 -preset medium -crf 20 \
  -c:a copy \
  output.mp4
```

**帧时间对齐**：
- 叠加层帧率必须与原始视频帧率匹配
- 对于延时摄影（如 30x），FIT 时间步进 = 视频帧步进 × 加速倍率
- 通过 VideoConfig 中的 `time_scale` 参数控制

#### 3.2.6 FFmpeg Service (`services/ffmpeg_service.py`)

**职责**：封装 FFmpeg 命令行操作。

**关键方法**：
- `get_video_info(path) -> VideoInfo`
- `extract_frame(path, timestamp) -> bytes`
- `render_with_overlay(video_path, overlay_frames_gen, output_path, config)`
- `concat_videos(input_list, output_path)`
- `mux_audio(video_path, audio_path, output_path)`

---

## 4. 数据流

### 4.1 核心工作流

```
用户指定 FIT 本地路径 ──→ FIT Parser ──→ FitData（内存 + JSON 缓存）
                                                          │
用户配置视频参数 ──→ VideoConfig ──────────────────────→ │
   （本地路径、起始时间、fps、time_scale）                  │
                                                          ▼
用户选择/编辑叠加模板 ──→ OverlayTemplate ──→ Overlay Designer
                                                          │
                                                          ▼
                                              Frame Renderer（逐帧）
                                              ↑ FitData 查询 + Widget 配置
                                                          │
                                                          ▼
                                      叠加层 RGBA 帧流 → ffmpeg pipe
                                                          │
                                                          ▼
                                              最终合成视频 → 本地输出路径
```

### 4.2 时间同步

这是本工具的核心难点。运动相机和运动手表的时钟是独立的，需要手动对齐。

**策略**：
1. **自动推断**：利用 FIT 文件的 `start_time` 和视频文件的创建时间做初步匹配
2. **手动偏移**：用户指定视频第 N 秒对应 FIT 的第 M 秒
3. **关键帧对齐**：用户在视频和 FIT 轨迹上分别标记同一地理位置点，自动计算偏移

**数据模型**：

```python
@dataclass
class TimeSyncConfig:
    video_start_time: datetime   # 视频录制起始时间
    fit_start_time: datetime     # FIT 记录起始时间
    offset_seconds: float        # 手动偏移（秒），正值=FIT 数据提前于视频
    time_scale: float            # 时间缩放（1.0=正常，30.0=30x延时）
    
    def fit_time_at_video_frame(self, frame_index: int, fps: float) -> datetime:
        """计算视频第 frame_index 帧对应的 FIT 时间点"""
        video_elapsed = frame_index / fps
        fit_elapsed = video_elapsed * self.time_scale + self.offset_seconds
        return self.fit_start_time + timedelta(seconds=fit_elapsed)
```

---

## 5. API 设计

### 5.1 REST API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/project/` | POST | 创建项目 |
| `/api/project/<id>` | GET | 获取项目详情 |
| `/api/project/<id>` | PUT | 更新项目配置 |
| `/api/fit/load` | POST | 通过本地路径加载 FIT 文件 `{ "path": "D:/..." }` |
| `/api/fit/<id>/summary` | GET | 获取 FIT 摘要信息 |
| `/api/fit/<id>/records` | GET | 获取 FIT 时间序列数据（支持 `?start=&end=` 过滤） |
| `/api/fit/<id>/track` | GET | 获取轨迹 GeoJSON |
| `/api/video/load` | POST | 通过本地路径加载视频 `{ "path": "D:/..." }` |
| `/api/video/<id>/info` | GET | 获取视频元数据 |
| `/api/video/<id>/frame` | GET | 提取视频帧预览 `?t=秒` |
| `/api/video/<id>/thumbnail` | GET | 获取视频缩略图（首帧） |
| `/api/overlay/templates` | GET | 获取内置叠加模板列表 |
| `/api/overlay/template/<name>` | GET | 获取模板详情 |
| `/api/project/<id>/overlay` | GET/PUT | 获取/更新项目的叠加配置 |
| `/api/render/preview` | POST | 渲染单帧预览（指定时间点） |
| `/api/render/start` | POST | 启动渲染任务 |
| `/api/render/<id>/status` | GET | 查询渲染进度 |
| `/api/render/<id>/cancel` | POST | 取消渲染 |
| `/api/render/<id>/result` | GET | 获取渲染结果（返回本地输出路径） |

### 5.2 WebSocket 事件

| 事件 | 方向 | 描述 |
|------|------|------|
| `render:progress` | Server → Client | 渲染进度更新 `{ frame, total, percent, eta }` |
| `render:complete` | Server → Client | 渲染完成 `{ output_path, duration }` |
| `render:error` | Server → Client | 渲染出错 `{ message }` |

---

## 6. 技术选型

| 类别 | 选择 | 理由 |
|------|------|------|
| 后端框架 | Flask 3.x | 轻量、灵活、Python 生态 |
| FIT 解析 | fit-tool (`fit_tool`) | 成熟的 FIT 文件 Python 解析库 |
| 视频元数据 | ffmpeg-python | Pythonic 的 FFmpeg 封装 |
| 帧渲染 | Pillow (PIL) | 纯 Python 图像绘制，无需 GUI |
| 地图渲染 | 离线 OSM 瓦片 + Pillow 拼接 | 无需浏览器/JS 渲染引擎 |
| 前端框架 | 原生 JS + Leaflet + Chart.js | 轻量、无构建工具依赖 |
| 地图前端 | Leaflet.js | 开源、轻量的 Web 地图库 |
| 图表前端 | Chart.js | 简洁的时间序列图表 |
| 实时通信 | Flask-SocketIO | 渲染进度推送 |
| 数据持久化 | JSON 文件 + SQLite | 项目配置用 JSON，可选 SQLite 做缓存索引 |
| 视频合成 | FFmpeg (pipe) | 高效、工业级 |

---

## 7. 关键设计决策

### 7.1 为什么不做文件上传？

本工具定位为**本地桌面工具**，Flask 仅提供 Web UI 交互界面：
- **大文件问题**：骑行视频动辄数 GB，上传到本地 Flask 再存盘毫无意义，浪费磁盘和等待时间
- **文件系统直读**：后端直接读取用户指定的本地路径，零拷贝、零等待
- **输出也是本地的**：渲染结果直接写到用户指定的本地输出路径，无需下载
- **安全性**：仅在本机 `localhost` 运行，不暴露到网络

### 7.2 为什么不用 Canvas/HTML 渲染叠加层？

纯 Python (Pillow) 渲染的优势：
- **确定性**：帧帧精确对齐，无浏览器渲染差异
- **性能**：直接输出 RGBA raw bytes，零编解码开销
- **pipe 兼容**：可直接喂给 ffmpeg pipe，无需中间文件
- **无头运行**：可在无 GUI 的服务器上运行

### 7.3 为什么用 REST API 而非 GraphQL？

- 项目数据模型简单，REST 足够
- 渲染类操作是命令式的（start/cancel/status），REST 更自然
- 减少前端复杂度

### 7.4 地图渲染策略

离线瓦片方案：
- 首次加载 FIT 时，根据轨迹 bounding box 预下载 zoom 14-16 的 OSM 瓦片
- 瓦片缓存到 `projects/<id>/tiles/` 目录
- 渲染时：选取合适的 zoom → 计算 viewport → 拼接瓦片 → 叠加轨迹
- 替代方案：若 OSM 瓦片不可用，使用简易矢量轨迹（无底图）

### 7.5 延时摄影支持

通过 `time_scale` 参数统一处理：
- 普通视频：`time_scale = 1.0`
- 30x 延时：`time_scale = 30.0`，即视频 1 秒对应 FIT 30 秒
- 每帧 FIT 步进 = `(1 / fps) * time_scale` 秒

---

## 8. 错误处理与边界情况

- **FIT 与视频时间不重叠**：提示用户调整时间偏移
- **FIT 数据缺失字段**（如无心率）：对应 Widget 灰显或隐藏
- **视频帧率非标准**：ffprobe 精确检测，支持 VFR（可变帧率）
- **文件路径不存在或无权限**：友好提示，引导用户检查路径
- **长时间渲染中断**：支持渲染恢复（记录已渲染帧号）

---

## 9. 加载状态与等待体验

FIT 文件解析和视频分析均可能耗时较长（数秒到数十秒），需在 UI 层面提供明确的等待反馈，避免用户误以为无响应而重复操作。

### 9.1 加载状态设计原则

- **即时反馈**：点击"加载"按钮后立即显示加载状态，不等后端返回
- **禁止重复操作**：加载期间禁用加载按钮和浏览按钮，防止重复请求
- **视觉遮罩**：面板级半透明遮罩 + 居中旋转动画，清晰标识"正在处理"
- **状态文字**：状态栏显示 spinner + 描述文字（如"正在解析 FIT 文件…"）
- **自动恢复**：无论成功或失败，`finally` 块中恢复按钮状态和移除遮罩

### 9.2 加载状态实现

**FIT 加载** (`loadFit()`)：
1. 显示 `#fitLoadingOverlay`（半透明遮罩 + spinner + "正在解析 FIT 文件…"）
2. 加载按钮添加 `.loading` 类（按钮内显示旋转动画，文字隐藏）
3. 禁用加载按钮和浏览按钮
4. 状态栏切换为 `.status-msg.loading`（蓝色 spinner + 文字）
5. 请求完成后在 `finally` 中恢复所有状态

**视频加载** (`loadVideo()`)：
1. 显示 `#videoLoadingOverlay`（半透明遮罩 + spinner + "正在分析视频文件…"）
2. 加载按钮添加 `.loading` 类
3. 禁用加载按钮和浏览按钮
4. 状态栏切换为 `.status-msg.loading`
5. 请求完成后在 `finally` 中恢复所有状态

### 9.3 CSS 组件

| 类名 | 用途 |
|------|------|
| `.loading-overlay` | 面板级遮罩层（absolute 定位，半透明背景 + 居中内容） |
| `.spinner` | 大号旋转加载动画（36px） |
| `.spinner-sm` | 小号旋转加载动画（18px），用于状态栏内嵌 |
| `.loading-text` | 加载描述文字（带呼吸动画） |
| `.btn-primary.loading` | 按钮加载态（文字隐藏，内部显示旋转动画） |
| `.status-msg.loading` | 状态消息加载态（蓝色主题 + spinner） |

### 9.4 扩展建议

后续可考虑为以下耗时操作也添加加载状态：
- Step 2 数据清洗/平滑滤波
- Step 3 视频帧提取预览
- Step 4 叠加层预览渲染
- Step 5 最终渲染导出（已有进度条，但启动阶段可加等待态）
