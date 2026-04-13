# CyclingWorkshop 实施计划

## 阶段概览

| 阶段 | 名称 | 预计工期 | 交付物 |
|------|------|---------|--------|
| P0 | 基础框架搭建 | 2-3 天 | Flask 骨架 + 前端页面框架 |
| P1 | FIT 解析 & 数据可视化 | 2-3 天 | FIT 上传/解析/展示 API + 前端 |
| P2 | 视频接入 & 时间同步 | 2 天 | 视频元数据分析 + 时间对齐 UI |
| P3 | 叠加层设计器 | 3-4 天 | Widget 系统 + 拖拽编辑 + 预览 |
| P4 | 渲染管线 & 导出 | 3-4 天 | 逐帧渲染 + ffmpeg pipe + 进度推送 |
| P5 | 优化 & 完善 | 2 天 | 性能优化 + 边界处理 + 文档 |

---

## P0: 基础框架搭建

### 目标
建立可运行的 Flask 应用骨架，前端页面可访问。

### 任务清单

1. **初始化项目**
   - [ ] 创建 `requirements.txt`（flask, flask-socketio, fit_tool, ffmpeg-python, Pillow, requests）
   - [ ] 创建 `config.py`（ffmpeg 路径、目录配置）
   - [ ] 创建 `app.py`（Flask app + Blueprint 注册 + SocketIO 初始化）

2. **后端骨架**
   - [ ] 创建 `api/` Blueprint 骨架（fit, video, overlay, render, project）
   - [ ] 创建 `models/` 数据类（FitData, VideoConfig, OverlayTemplate, WidgetConfig）
   - [ ] 创建目录结构（projects/, output/, static/, templates/）

3. **前端骨架**
   - [ ] `templates/index.html`：单页应用入口，引入 CSS/JS
   - [ ] `static/css/main.css`：深色主题基础样式
   - [ ] `static/js/app.js`：步骤导航 + 路由
   - [ ] `static/js/api.js`：fetch 封装

### 验收标准
- `python app.py` 启动后浏览器访问可见带步骤导航的空页面
- API 端点返回 404/空数据（骨架就绪）

---

## P1: FIT 解析 & 数据可视化

### 目标
用户可通过本地路径加载 FIT 文件，查看解析后的运动数据摘要和轨迹。

### 任务清单

1. **FIT Parser Service**
   - [ ] 实现 `services/fit_parser.py`
   - [ ] 解析 records、sessions、laps
   - [ ] 实现 `get_record_at()` 线性插值查询
   - [ ] 数据字段可用性检测
   - [ ] 单元测试：用项目中的 `Zepp20260404075746.fit` 做验证

2. **FIT API**
   - [ ] `POST /api/fit/load`：通过本地路径加载 `{ "path": "..." }` → 解析 + 返回摘要
   - [ ] `GET /api/fit/<id>/summary`：会话摘要
   - [ ] `GET /api/fit/<id>/records`：时间序列数据（支持范围过滤）
   - [ ] `GET /api/fit/<id>/track`：轨迹 GeoJSON

3. **FIT 前端**
   - [ ] `static/js/fit-viewer.js`：
     - 路径输入框 + 加载按钮
     - 摘要卡片（运动类型、距离、时长、均速、爬升）
     - Leaflet 轨迹地图
     - Chart.js 数据图表（速度、心率、海拔时间序列）
     - 数据字段可用性指示器

### 验收标准
- 输入 `Zepp20260404075746.fit` 的本地路径后显示完整的运动数据
- 地图显示轨迹路线
- 图表可交互（hover 查看数值）

---

## P2: 视频接入 & 时间同步

### 目标
用户可通过本地路径指定视频文件，配置参数，并与 FIT 数据做时间对齐。

### 任务清单

1. **Video Analyzer Service**
   - [ ] 实现 `services/video_analyzer.py`
   - [ ] ffprobe 获取视频元数据
   - [ ] 单帧提取（`ffmpeg -ss ... -frames:v 1 ...`）
   - [ ] 视频缩略图提取

2. **Video API**
   - [ ] `POST /api/video/load`：通过本地路径加载 `{ "path": "..." }` → ffprobe 分析
   - [ ] `GET /api/video/<id>/info`：元数据
   - [ ] `GET /api/video/<id>/frame?t=...`：提取帧预览
   - [ ] `GET /api/video/<id>/thumbnail`：首帧缩略图

3. **Time Sync Model**
   - [ ] 实现 `models/video_config.py`（TimeSyncConfig）
   - [ ] `fit_time_at_video_frame()` 计算
   - [ ] 自动推断：FIT start_time vs 视频文件时间戳

4. **时间同步 UI**
   - [ ] 三种模式：自动推断 / 手动偏移 / 关键帧对齐
   - [ ] 同步预览：视频播放器 + FIT 轨迹联动
   - [ ] 延时倍率配置
   - [ ] 时间偏移微调（± 按钮步进 1s/5s）

### 验收标准
- 输入视频路径后显示正确的元数据
- 手动设置偏移后，同步预览中视频和轨迹对齐
- 延时模式（30x）下 FIT 时间正确缩放

---

## P3: 叠加层设计器

### 目标
用户可选择/自定义叠加模板，拖拽布局 Widget，实时预览效果。

### 任务清单

1. **Overlay Designer Service**
   - [ ] 实现 `services/overlay_designer.py`
   - [ ] 内置模板定义（骑行经典、骑行极简、跑步基础、越野爬坡）
   - [ ] Widget 配置序列化/反序列化
   - [ ] 单帧渲染预览接口

2. **Frame Renderer（基础版）**
   - [ ] 实现 `services/frame_renderer.py`
   - [ ] 数字 Widget 渲染（速度、心率、踏频、功率、海拔、距离、时间）
   - [ ] 圆弧表盘渲染
   - [ ] 海拔剖面图渲染
   - [ ] 轨迹地图渲染（先实现无底图版：矢量轨迹线）
   - [ ] 合成所有 Widget 到叠加层

3. **Overlay API**
   - [ ] `GET /api/overlay/templates`：内置模板列表
   - [ ] `GET /api/overlay/template/<name>`：模板详情
   - [ ] `GET/PUT /api/project/<id>/overlay`：项目叠加配置
   - [ ] `POST /api/render/preview`：渲染单帧预览

4. **叠加设计器 UI**
   - [ ] 视频画布区域（按比例缩放显示）
   - [ ] Widget 拖拽移动
   - [ ] Widget 选中 + 编辑弹窗
   - [ ] 组件列表（显隐、删除、添加）
   - [ ] 模板选择下拉
   - [ ] 时间轴滑块 + 实时预览更新
   - [ ] 全局设置（透明度、缩放）

5. **轨迹地图 Widget（进阶）**
   - [ ] OSM 瓦片下载 + 缓存
   - [ ] 瓦片拼接渲染
   - [ ] 轨迹线叠加
   - [ ] 当前位置标记

### 验收标准
- 选择模板后画布上显示 Widget 布局
- 拖拽 Widget 位置实时更新
- 点击「预览」按钮可看到带数据的叠加效果图
- 地图 Widget 显示轨迹和当前位置

---

## P4: 渲染管线 & 导出

### 目标
完整渲染叠加层并合成最终视频。

### 任务清单

1. **Render Pipeline Service**
   - [ ] 实现 `services/render_pipeline.py`
   - [ ] ffmpeg pipe 输入叠加层帧流
   - [ ] 帧时间对齐（按 time_sync 配置）
   - [ ] 渲染进度追踪
   - [ ] 暂停/恢复/取消

2. **FFmpeg Service**
   - [ ] 实现 `services/ffmpeg_service.py`
   - [ ] overlay 合成命令封装
   - [ ] 音频保留/去除
   - [ ] 输出编码参数配置

3. **Render API**
   - [ ] `POST /api/render/start`：启动渲染
   - [ ] `GET /api/render/<id>/status`：进度查询
   - [ ] `POST /api/render/<id>/cancel`：取消
   - [ ] `GET /api/render/<id>/result`：获取渲染结果（返回本地输出路径）

4. **WebSocket 进度推送**
   - [ ] Flask-SocketIO 事件：`render:progress`, `render:complete`, `render:error`

5. **渲染导出 UI**
   - [ ] 输出设置面板（路径、编码、质量）
   - [ ] 渲染范围选择
   - [ ] 进度条 + 实时数据（帧数、速度、ETA）
   - [ ] 暂停/取消按钮
   - [ ] 完成后打开输出目录

### 验收标准
- 完整渲染一段 1 分钟视频，叠加层正确对齐
- 渲染过程中进度条实时更新
- 输出视频可正常播放，叠加层清晰无闪烁
- 30x 延时视频的叠加数据正确缩放

---

## P5: 优化 & 完善

### 任务清单

1. **性能优化**
   - [ ] 地图瓦片预下载 + LRU 缓存
   - [ ] 多进程并行渲染（可选）
   - [ ] FIT 数据预插值（启动时生成逐帧数据表）
   - [ ] Widget 增量渲染（仅重绘变化的 Widget）

2. **边界处理**
   - [ ] FIT 数据缺失字段 → Widget 灰显
   - [ ] FIT 与视频时间不重叠 → 警告提示
   - [ ] VFR 视频支持
   - [ ] 文件路径不存在或无权限 → 友好提示

3. **项目持久化**
   - [ ] 项目配置保存/加载（JSON）
   - [ ] 导入/导出项目配置
   - [ ] 最近项目列表

4. **文档**
   - [ ] README.md（安装、配置、使用说明）
   - [ ] 代码内 docstring 完善

### 验收标准
- 完整的端到端流程可顺利跑通
- 异常情况有合理的提示和处理
- README 足够让新用户上手

---

## 依赖版本建议

```
flask>=3.0
flask-socketio>=5.3
fit-tool>=0.1
ffmpeg-python>=0.2
Pillow>=10.0
requests>=2.31
geopy>=2.4          # 地理计算
```

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `fit_tool` 对 Zepp FIT 兼容性 | 无法解析数据 | 先用 `fit_tool` 测试，备选 `garmin-fit-sdk` |
| Pillow 渲染性能 | 长视频渲染慢 | 先单帧确认正确，再优化；多进程 |
| OSM 瓦片不可用 | 地图无底图 | 降级为纯矢量轨迹；支持自定义瓦片源 |
| ffmpeg pipe 在 Windows 上的兼容性 | 无法实时合成 | 备选方案 B（PNG 序列 + 两步合成） |
| 文件路径含特殊字符（中文/空格） | 读取失败 | 路径规范化 + ffmpeg 引号处理 |
