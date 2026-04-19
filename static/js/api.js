/* CyclingWorkshop API 封装 */

const API = {
  // ── FIT ──────────────────────────────
  async loadFit(path) {
    const resp = await fetch("/api/fit/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "加载失败" }));
      throw new Error(err.error || "FIT 加载失败");
    }
    return resp.json();
  },

  async getFitSummary(fitId) {
    const resp = await fetch(`/api/fit/${encodeURIComponent(fitId)}/summary`);
    if (!resp.ok) throw new Error("获取摘要失败");
    return resp.json();
  },

  async getFitRecords(fitId, start = null, end = null, maxPoints = 2000) {
    let url = `/api/fit/${encodeURIComponent(fitId)}/records?max_points=${maxPoints}`;
    if (start !== null) url += `&start=${start}`;
    if (end !== null) url += `&end=${end}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("获取记录失败");
    return resp.json();
  },

  async getFitTrack(fitId, includePoints = true, maxPoints = null) {
    let url = `/api/fit/${encodeURIComponent(fitId)}/track?include_points=${includePoints}`;
    if (maxPoints !== null) url += `&max_points=${maxPoints}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("获取轨迹失败");
    return resp.json();
  },

  async getFitTrackAspect(fitId) {
    const resp = await fetch(`/api/fit/${encodeURIComponent(fitId)}/track_aspect`);
    if (!resp.ok) throw new Error("获取轨迹宽高比失败");
    return resp.json();
  },

  async getFitOutliers(fitId, fields = null, sigma = 3.0) {
    let url = `/api/fit/${encodeURIComponent(fitId)}/outliers?sigma=${sigma}`;
    if (fields) url += `&fields=${fields.join(",")}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("异常检测失败");
    return resp.json();
  },

  async applyFitFilter(fitId, filterConfig) {
    const resp = await fetch(`/api/fit/${encodeURIComponent(fitId)}/filter`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(filterConfig),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "滤波失败" }));
      throw new Error(err.error || "滤波失败");
    }
    return resp.json();
  },

  async sanitizeFit(fitId, config) {
    const resp = await fetch(`/api/fit/${encodeURIComponent(fitId)}/sanitize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "清洗失败" }));
      throw new Error(err.error || "数据清洗失败");
    }
    return resp.json();
  },

  async smoothFit(fitId, config) {
    const resp = await fetch(`/api/fit/${encodeURIComponent(fitId)}/smooth`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "平滑失败" }));
      throw new Error(err.error || "平滑滤波失败");
    }
    return resp.json();
  },

  async resetFitData(fitId) {
    const resp = await fetch(`/api/fit/${encodeURIComponent(fitId)}/reset`, {
      method: "POST",
    });
    if (!resp.ok) throw new Error("重置失败");
    return resp.json();
  },

  async exportFitGpx(fitId, outputPath = "") {
    const resp = await fetch(`/api/fit/${encodeURIComponent(fitId)}/export_gpx`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_path: outputPath }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "导出失败" }));
      throw new Error(err.error || "GPX 导出失败");
    }
    // 如果有 output_path，返回 JSON；否则返回文件下载
    const ct = resp.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      return resp.json();
    }
    return resp.blob();
  },

  // ── Video ────────────────────────────
  async loadVideo(path) {
    const resp = await fetch("/api/video/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "加载失败" }));
      throw new Error(err.error || "视频加载失败");
    }
    return resp.json();
  },

  async loadVideoBatch(paths) {
    const resp = await fetch("/api/video/load_batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "批量加载失败" }));
      throw new Error(err.error || "批量加载视频失败");
    }
    return resp.json();
  },

  async getVideoInfo(videoId) {
    const resp = await fetch(`/api/video/${encodeURIComponent(videoId)}/info`);
    if (!resp.ok) throw new Error("获取视频信息失败");
    return resp.json();
  },

  getVideoFrame(videoId, t) {
    return `/api/video/${encodeURIComponent(videoId)}/frame?t=${t}`;
  },

  getVideoThumbnail(videoId) {
    return `/api/video/${encodeURIComponent(videoId)}/thumbnail`;
  },

  // ── Overlay ──────────────────────────
  async getTemplates() {
    const resp = await fetch("/api/overlay/templates");
    if (!resp.ok) throw new Error("获取模板失败");
    return resp.json();
  },

  async getTemplate(name, width = 1920, height = 1080) {
    const resp = await fetch(`/api/overlay/template/${name}?width=${width}&height=${height}`);
    if (!resp.ok) throw new Error("获取模板详情失败");
    return resp.json();
  },

  async getWidgetTypes() {
    const resp = await fetch("/api/overlay/widget-types");
    if (!resp.ok) throw new Error("获取组件类型失败");
    return resp.json();
  },

  // ── Render ───────────────────────────
  async startRender(projectData) {
    const resp = await fetch("/api/render/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(projectData),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "启动失败" }));
      throw new Error(err.error || "渲染启动失败");
    }
    return resp.json();
  },

  async getRenderStatus(taskId) {
    const resp = await fetch(`/api/render/${taskId}/status`);
    if (!resp.ok) throw new Error("查询状态失败");
    return resp.json();
  },

  async cancelRender(taskId) {
    const resp = await fetch(`/api/render/${taskId}/cancel`, { method: "POST" });
    if (!resp.ok) throw new Error("取消失败");
    return resp.json();
  },

  async getRenderLogs(taskId, since = 0) {
    const resp = await fetch(`/api/render/${taskId}/logs?since=${since}`);
    if (!resp.ok) throw new Error("获取日志失败");
    return resp.json();
  },

  async getRenderResult(taskId) {
    const resp = await fetch(`/api/render/${taskId}/result`);
    if (!resp.ok) throw new Error("获取结果失败");
    return resp.json();
  },

  async getRenderBatchStatus(batchId) {
    const resp = await fetch(`/api/render/batch/${batchId}/status`);
    if (!resp.ok) throw new Error("获取批量状态失败");
    return resp.json();
  },

  async cancelRenderBatch(batchId) {
    const resp = await fetch(`/api/render/batch/${batchId}/cancel`, { method: "POST" });
    if (!resp.ok) throw new Error("取消批量任务失败");
    return resp.json();
  },

  // ── Project ──────────────────────────
  async listProjects() {
    const resp = await fetch("/api/project/");
    if (!resp.ok) throw new Error("获取项目列表失败");
    return resp.json();
  },

  async createProject(data) {
    const resp = await fetch("/api/project/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!resp.ok) throw new Error("创建项目失败");
    return resp.json();
  },

  async getProject(projectId) {
    const resp = await fetch(`/api/project/${projectId}`);
    if (!resp.ok) throw new Error("获取项目失败");
    return resp.json();
  },

  async updateProject(projectId, data) {
    const resp = await fetch(`/api/project/${projectId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!resp.ok) throw new Error("更新项目失败");
    return resp.json();
  },

  async deleteProject(projectId) {
    const resp = await fetch(`/api/project/${projectId}`, { method: "DELETE" });
    if (!resp.ok) throw new Error("删除项目失败");
    return resp.json();
  },

  // ── Tiles ────────────────────────────
  async getTileStyles() {
    const resp = await fetch("/api/tiles/styles");
    if (!resp.ok) throw new Error("获取瓦片样式失败");
    return resp.json();
  },

  async getTileCacheStats() {
    const resp = await fetch("/api/tiles/cache/stats");
    if (!resp.ok) throw new Error("获取缓存统计失败");
    return resp.json();
  },

  async clearTileCache() {
    const resp = await fetch("/api/tiles/cache/clear", { method: "POST" });
    if (!resp.ok) throw new Error("清除缓存失败");
    return resp.json();
  },

  async getProxyConfig() {
    const resp = await fetch("/api/tiles/proxy");
    if (!resp.ok) throw new Error("获取代理配置失败");
    return resp.json();
  },

  async setProxyConfig(config) {
    const resp = await fetch("/api/tiles/proxy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "设置代理失败" }));
      throw new Error(err.error || "设置代理失败");
    }
    return resp.json();
  },

  async testProxy() {
    const resp = await fetch("/api/tiles/proxy/test", { method: "POST" });
    if (!resp.ok) throw new Error("代理测试请求失败");
    return resp.json();
  },

  async preloadTiles(config) {
    const resp = await fetch("/api/tiles/preload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "预下载失败" }));
      throw new Error(err.error || "瓦片预下载失败");
    }
    return resp.json();
  },

  async preloadRegion(config) {
    const resp = await fetch("/api/tiles/preload/region", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "区域预下载失败" }));
      throw new Error(err.error || "区域瓦片预下载失败");
    }
    return resp.json();
  },

  async getTileProgress(taskId) {
    const resp = await fetch(`/api/tiles/progress/${encodeURIComponent(taskId)}`);
    if (!resp.ok) throw new Error("获取进度失败");
    return resp.json();
  },

  async getAllTileProgress() {
    const resp = await fetch("/api/tiles/progress");
    if (!resp.ok) throw new Error("获取进度列表失败");
    return resp.json();
  },

  async cancelTileProgress(taskId) {
    const resp = await fetch(`/api/tiles/progress/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
    if (!resp.ok) throw new Error("取消任务失败");
    return resp.json();
  },

  // 瓦片代理 URL 构造（供 Leaflet 使用）
  tileProxyUrl(style) {
    return `/api/tiles/map/${style}/{z}/{x}/{y}.png`;
  },

  // 缓存可视化
  async getCacheInventory() {
    const resp = await fetch("/api/tiles/cache/inventory");
    if (!resp.ok) throw new Error("获取缓存清单失败");
    return resp.json();
  },

  async getCacheRegion(minLat, maxLat, minLon, maxLon, zoom) {
    const params = new URLSearchParams({ min_lat: minLat, max_lat: maxLat, min_lon: minLon, max_lon: maxLon, zoom });
    const resp = await fetch(`/api/tiles/cache/region?${params}`);
    if (!resp.ok) throw new Error("查询区域缓存失败");
    return resp.json();
  },

  async downloadRegion(config) {
    const resp = await fetch("/api/tiles/cache/region/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "区域下载失败" }));
      throw new Error(err.error || "区域瓦片下载失败");
    }
    return resp.json();
  },

  // ── Files ────────────────────────────
  async browseDirectory(path, exts = "") {
    let url = "/api/files/browse";
    if (exts) {
      url = "/api/files/browse/filter";
      url += `?path=${encodeURIComponent(path)}&ext=${encodeURIComponent(exts)}`;
    } else if (path) {
      url += `?path=${encodeURIComponent(path)}`;
    }
    const resp = await fetch(url);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: "浏览失败" }));
      throw new Error(err.error || "目录浏览失败");
    }
    return resp.json();
  },
};
