/* 瓦片底图管理器 */
const TileManager = {
  state: {
    proxyConfig: null,
    cacheStats: null,
    tasks: {},
    pollingTimer: null,
    proxyTestResult: null,
    // 缓存可视化地图
    cacheMap: null,
    cacheTileLayer: null,
    cacheTrackLayer: null,
    regionSelectRect: null,
    regionSelectBounds: null,
  },

  // ── 初始化 ────────────────────────────
  async init() {
    await this.loadProxyConfig();
    await this.loadCacheStats();
    this.renderProxyForm();
    this.renderCacheInfo();
    this.renderPreloadForm();
    this.startPolling();
    // 初始化缓存可视化地图（延迟一帧以确保 DOM 可见）
    requestAnimationFrame(() => this.initCacheMap());
  },

  destroy() {
    if (this.state.pollingTimer) {
      clearInterval(this.state.pollingTimer);
      this.state.pollingTimer = null;
    }
    if (this.state.cacheMap) {
      this.state.cacheMap.remove();
      this.state.cacheMap = null;
    }
  },

  // ── 代理配置 ──────────────────────────
  async loadProxyConfig() {
    try {
      this.state.proxyConfig = await API.getProxyConfig();
    } catch (e) {
      console.error("加载代理配置失败:", e);
      this.state.proxyConfig = { enabled: false, type: "http", host: "", port: 0, username: "", password: "" };
    }
  },

  async saveProxy() {
    const cfg = {
      enabled: document.getElementById("proxyEnabled").checked,
      type: document.getElementById("proxyType").value,
      host: document.getElementById("proxyHost").value.trim(),
      port: parseInt(document.getElementById("proxyPort").value) || 0,
      username: document.getElementById("proxyUser").value.trim(),
      password: document.getElementById("proxyPass").value,
    };
    try {
      await API.setProxyConfig(cfg);
      this.state.proxyConfig = await API.getProxyConfig();
      App.toast("代理配置已保存", "success");
    } catch (e) {
      App.toast("保存代理失败: " + e.message, "error");
    }
  },

  async testProxy() {
    const btn = document.getElementById("proxyTestBtn");
    btn.disabled = true;
    btn.textContent = "测试中…";
    this.state.proxyTestResult = null;
    this.renderProxyTestResult();

    try {
      const result = await API.testProxy();
      this.state.proxyTestResult = result;
    } catch (e) {
      this.state.proxyTestResult = { ok: false, error: e.message };
    }
    this.renderProxyTestResult();
    btn.disabled = false;
    btn.textContent = "测试连接";
  },

  renderProxyForm() {
    const cfg = this.state.proxyConfig || {};
    const container = document.getElementById("proxyFormContainer");
    if (!container) return;

    container.innerHTML = `
      <div class="proxy-form">
        <div class="proxy-row">
          <label class="field-check">
            <input type="checkbox" id="proxyEnabled" ${cfg.enabled ? 'checked' : ''}>
            启用代理
          </label>
        </div>
        <div class="proxy-row">
          <label>类型:
            <select id="proxyType">
              <option value="http" ${cfg.type !== 'socks5' ? 'selected' : ''}>HTTP/HTTPS</option>
              <option value="socks5" ${cfg.type === 'socks5' ? 'selected' : ''}>SOCKS5</option>
            </select>
          </label>
        </div>
        <div class="proxy-row">
          <label>主机: <input type="text" id="proxyHost" value="${cfg.host || ''}" placeholder="如 127.0.0.1" spellcheck="false"></label>
          <label>端口: <input type="number" id="proxyPort" value="${cfg.port || ''}" placeholder="如 7890" style="width:80px;"></label>
        </div>
        <div class="proxy-row">
          <label>用户名: <input type="text" id="proxyUser" value="${cfg.username || ''}" placeholder="可选" spellcheck="false"></label>
          <label>密码: <input type="password" id="proxyPass" value="${cfg.password || ''}" placeholder="可选"></label>
        </div>
        <div class="proxy-actions">
          <button onclick="TileManager.saveProxy()" class="btn-primary">保存配置</button>
          <button onclick="TileManager.testProxy()" class="btn-secondary" id="proxyTestBtn">测试连接</button>
        </div>
        <div id="proxyTestResult"></div>
      </div>
    `;
  },

  renderProxyTestResult() {
    const container = document.getElementById("proxyTestResult");
    if (!container) return;
    const result = this.state.proxyTestResult;
    if (!result) {
      container.innerHTML = "";
      return;
    }
    if (result.ok) {
      container.innerHTML = `<div class="proxy-test-ok">✅ 连接成功 (${result.elapsed_ms}ms, ${result.tile_size})</div>`;
    } else {
      container.innerHTML = `<div class="proxy-test-fail">❌ 连接失败: ${this._escHtml(result.error || '未知错误')}</div>`;
    }
  },

  // ── 缓存管理 ──────────────────────────
  async loadCacheStats() {
    try {
      this.state.cacheStats = await API.getTileCacheStats();
    } catch (e) {
      console.error("加载缓存统计失败:", e);
      this.state.cacheStats = { count: 0, size_mb: 0 };
    }
  },

  async clearCache() {
    if (!confirm("确定要清除所有瓦片缓存吗？此操作不可撤销。")) return;
    try {
      const result = await API.clearTileCache();
      App.toast(`已删除 ${result.deleted} 个缓存文件`, "success");
      await this.loadCacheStats();
      this.renderCacheInfo();
    } catch (e) {
      App.toast("清除缓存失败: " + e.message, "error");
    }
  },

  renderCacheInfo() {
    const container = document.getElementById("cacheInfoContainer");
    if (!container) return;
    const stats = this.state.cacheStats || {};
    container.innerHTML = `
      <div class="cache-info">
        <div class="cache-stat">
          <span class="cache-stat-val">${stats.count || 0}</span>
          <span class="cache-stat-label">缓存文件</span>
        </div>
        <div class="cache-stat">
          <span class="cache-stat-val">${stats.size_mb || 0}</span>
          <span class="cache-stat-label">MB</span>
        </div>
        <div class="cache-dir">
          <span class="text-muted">路径: ${this._escHtml(stats.cache_dir || '--')}</span>
        </div>
        <button onclick="TileManager.clearCache()" class="btn-danger btn-sm">清除缓存</button>
      </div>
    `;
  },

  // ── 缓存可视化地图 ─────────────────────
  initCacheMap() {
    const mapEl = document.getElementById("cacheMap");
    if (!mapEl || this.state.cacheMap) return;

    this.state.cacheMap = L.map("cacheMap", {
      center: [30, 120],
      zoom: 10,
      zoomControl: true,
    });

    // 使用后端代理加载瓦片
    this.state.cacheTileLayer = L.tileLayer(API.tileProxyUrl("carto_dark"), {
      attribution: "© CartoDB", maxZoom: 18,
    }).addTo(this.state.cacheMap);

    // 如果有 FIT 数据，叠加轨迹
    if (App.state.fitId) {
      this._addFitTrackToCacheMap();
    }

    // 添加区域选择控件
    this._addRegionSelect();

    // 延迟刷新地图尺寸
    setTimeout(() => {
      if (this.state.cacheMap) this.state.cacheMap.invalidateSize();
    }, 300);
  },

  async _addFitTrackToCacheMap() {
    try {
      const geojson = await API.getFitTrack(App.state.fitId, false);
      if (geojson.features && geojson.features.length) {
        this.state.cacheTrackLayer = L.geoJSON(geojson, {
          style: { color: "#00d4aa", weight: 3, opacity: 0.8 },
          pointToLayer: () => null,  // 不画点
        }).addTo(this.state.cacheMap);
        this.state.cacheMap.fitBounds(this.state.cacheTrackLayer.getBounds());
      }
    } catch (e) {
      console.error("加载轨迹到缓存地图失败:", e);
    }
  },

  _addRegionSelect() {
    const map = this.state.cacheMap;
    if (!map) return;

    // 用 Shift+拖拽 选择区域
    let selecting = false;
    let startLatLng = null;
    let rect = null;

    map.on("mousedown", (e) => {
      if (!e.originalEvent.shiftKey) return;
      selecting = true;
      startLatLng = e.latlng;
      if (rect) { map.removeLayer(rect); rect = null; }
      map.dragging.disable();
    });

    map.on("mousemove", (e) => {
      if (!selecting || !startLatLng) return;
      const bounds = L.latLngBounds(startLatLng, e.latlng);
      if (rect) {
        rect.setBounds(bounds);
      } else {
        rect = L.rectangle(bounds, {
          color: "#00d4aa", weight: 2, fillOpacity: 0.15, dashArray: "6 3",
        }).addTo(map);
      }
    });

    map.on("mouseup", (e) => {
      if (!selecting) return;
      selecting = false;
      map.dragging.enable();
      if (!startLatLng || !rect) return;

      const bounds = rect.getBounds();
      this.state.regionSelectRect = rect;
      this.state.regionSelectBounds = bounds;

      // 更新区域下载表单
      this._updateRegionDownloadForm(bounds);
    });
  },

  _updateRegionDownloadForm(bounds) {
    const el = document.getElementById("regionBounds");
    if (!el) return;
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();
    el.textContent = `${sw.lat.toFixed(4)}, ${sw.lng.toFixed(4)} → ${ne.lat.toFixed(4)}, ${ne.lng.toFixed(4)}`;
    document.getElementById("regionMinLat").value = sw.lat.toFixed(6);
    document.getElementById("regionMaxLat").value = ne.lat.toFixed(6);
    document.getElementById("regionMinLon").value = sw.lng.toFixed(6);
    document.getElementById("regionMaxLon").value = ne.lng.toFixed(6);
  },

  async downloadRegion() {
    const minLat = parseFloat(document.getElementById("regionMinLat").value);
    const maxLat = parseFloat(document.getElementById("regionMaxLat").value);
    const minLon = parseFloat(document.getElementById("regionMinLon").value);
    const maxLon = parseFloat(document.getElementById("regionMaxLon").value);
    const zoom = parseInt(document.getElementById("regionZoom").value) || 0;
    const tileStyle = document.getElementById("regionStyle").value;

    if (isNaN(minLat) || isNaN(maxLat) || isNaN(minLon) || isNaN(maxLon)) {
      App.toast("请先在地图上 Shift+拖拽 选择区域", "warning");
      return;
    }

    try {
      const result = await API.downloadRegion({
        min_lat: minLat, max_lat: maxLat,
        min_lon: minLon, max_lon: maxLon,
        zoom, tile_style: tileStyle,
      });
      App.toast(`开始下载 ${result.total_tiles} 个瓦片 (${result.style_name}, zoom=${result.zoom})`, "success");
      this.state.tasks[result.task_id] = result;
      this.renderTaskList();
    } catch (e) {
      App.toast("区域下载失败: " + e.message, "error");
    }
  },

  clearRegionSelect() {
    if (this.state.regionSelectRect && this.state.cacheMap) {
      this.state.cacheMap.removeLayer(this.state.regionSelectRect);
      this.state.regionSelectRect = null;
      this.state.regionSelectBounds = null;
    }
    const el = document.getElementById("regionBounds");
    if (el) el.textContent = "未选择（在地图上 Shift+拖拽 选择）";
    ["regionMinLat", "regionMaxLat", "regionMinLon", "regionMaxLon"].forEach(id => {
      const input = document.getElementById(id);
      if (input) input.value = "";
    });
  },

  // ── 预下载 ────────────────────────────
  async preloadForFit() {
    const fitId = App.state.fitId;
    if (!fitId) {
      App.toast("请先在数据源步骤加载 FIT 文件", "warning");
      return;
    }

    const tileStyle = document.getElementById("preloadStyle").value;
    const zoom = parseInt(document.getElementById("preloadZoom").value) || 0;

    try {
      const result = await API.preloadTiles({
        fit_id: fitId,
        tile_style: tileStyle,
        zoom: zoom,
        width: 400,
        height: 300,
      });
      App.toast(`开始下载 ${result.total_tiles} 个瓦片 (${result.style_name}, zoom=${result.zoom})`, "success");
      this.state.tasks[result.task_id] = result;
      this.renderTaskList();
    } catch (e) {
      App.toast("预下载失败: " + e.message, "error");
    }
  },

  async preloadAllStyles() {
    const fitId = App.state.fitId;
    if (!fitId) {
      App.toast("请先在数据源步骤加载 FIT 文件", "warning");
      return;
    }

    const zoom = parseInt(document.getElementById("preloadZoom").value) || 0;
    const styles = ["carto_dark", "carto_light", "osm", "stamen_terrain", "esri_satellite"];

    for (const style of styles) {
      try {
        const result = await API.preloadTiles({
          fit_id: fitId,
          tile_style: style,
          zoom: zoom,
          width: 400,
          height: 300,
        });
        App.toast(`${result.style_name}: ${result.total_tiles} 个瓦片 (zoom=${result.zoom})`, "info");
        this.state.tasks[result.task_id] = result;
      } catch (e) {
        App.toast(`${style} 预下载失败: ${e.message}`, "error");
      }
    }
    this.renderTaskList();
  },

  renderPreloadForm() {
    const container = document.getElementById("preloadFormContainer");
    if (!container) return;

    const fitLoaded = !!App.state.fitId;
    const fitInfo = fitLoaded ? "当前已加载 FIT 数据" : "尚未加载 FIT 数据（请先在步骤①加载）";

    container.innerHTML = `
      <div class="preload-form">
        <p class="text-muted" style="margin-bottom:8px;">${fitInfo}</p>
        <div class="preload-row">
          <label>底图样式:
            <select id="preloadStyle">
              <option value="carto_dark">CartoDB 暗色</option>
              <option value="carto_light">CartoDB 亮色</option>
              <option value="osm">OpenStreetMap</option>
              <option value="stamen_terrain">Stamen 地形</option>
              <option value="esri_satellite">ESRI 卫星</option>
            </select>
          </label>
          <label>缩放级别: <input type="number" id="preloadZoom" value="0" min="0" max="18" step="1"> <small class="text-muted">(0=自动)</small></label>
        </div>
        <div class="preload-actions">
          <button onclick="TileManager.preloadForFit()" class="btn-primary" ${!fitLoaded ? 'disabled title="请先加载 FIT 文件"' : ''}>下载选中样式</button>
          <button onclick="TileManager.preloadAllStyles()" class="btn-secondary" ${!fitLoaded ? 'disabled title="请先加载 FIT 文件"' : ''}>下载全部样式</button>
        </div>
      </div>
    `;
  },

  // ── 任务进度轮询 ──────────────────────
  startPolling() {
    if (this.state.pollingTimer) return;
    this.state.pollingTimer = setInterval(() => this.pollProgress(), 2000);
  },

  async pollProgress() {
    try {
      const allTasks = await API.getAllTileProgress();
      for (const [id, task] of Object.entries(allTasks)) {
        this.state.tasks[id] = task;
      }
      this.renderTaskList();
    } catch (e) {
      // 静默失败
    }
  },

  async cancelTask(taskId) {
    try {
      await API.cancelTileProgress(taskId);
      App.toast("任务已取消", "info");
    } catch (e) {
      App.toast("取消失败: " + e.message, "error");
    }
  },

  renderTaskList() {
    const container = document.getElementById("taskListContainer");
    if (!container) return;

    const tasks = Object.values(this.state.tasks);
    if (tasks.length === 0) {
      container.innerHTML = '<p class="text-muted">暂无下载任务</p>';
      return;
    }

    // 按时间倒序
    tasks.sort((a, b) => (b.started_at || 0) - (a.started_at || 0));

    let html = '<div class="task-list">';
    for (const task of tasks) {
      const total = task.total || 0;
      const completed = task.completed || 0;
      const cached = task.cached || 0;
      const failed = task.failed || 0;
      const done = completed + cached + failed;
      const pct = total > 0 ? Math.round(done / total * 100) : 0;

      let statusIcon = "⏳";
      let statusClass = "running";
      if (task.status === "completed") { statusIcon = "✅"; statusClass = "completed"; }
      else if (task.status === "cancelled") { statusIcon = "🚫"; statusClass = "cancelled"; }
      else if (task.status === "failed") { statusIcon = "❌"; statusClass = "failed"; }

      const elapsed = task.updated_at && task.started_at
        ? Math.round(task.updated_at - task.started_at) : 0;

      html += `
        <div class="task-item task-${statusClass}">
          <div class="task-header">
            <span class="task-status">${statusIcon}</span>
            <span class="task-desc">${this._escHtml(task.description || '下载任务')}</span>
            ${task.status === 'running' ? `<button onclick="TileManager.cancelTask('${task.task_id || ''}')" class="btn-danger btn-sm btn-xs">取消</button>` : ''}
          </div>
          <div class="task-progress-bar">
            <div class="task-progress-fill" style="width:${pct}%"></div>
          </div>
          <div class="task-stats">
            <span>进度: ${done}/${total} (${pct}%)</span>
            <span>✅ ${completed}</span>
            <span>💾 ${cached}</span>
            <span>❌ ${failed}</span>
            <span>耗时: ${elapsed}s</span>
          </div>
          ${task.errors && task.errors.length > 0 ? `<div class="task-errors"><small class="text-muted">最近错误: ${this._escHtml(task.errors.slice(-3).join('; '))}</small></div>` : ''}
        </div>
      `;
    }
    html += '</div>';
    container.innerHTML = html;
  },

  // ── 工具方法 ──────────────────────────
  _escHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
  },
};
