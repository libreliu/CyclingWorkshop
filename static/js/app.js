/* CyclingWorkshop 主应用逻辑 */

const App = {
  // ── 状态 ──────────────────────────────
  state: {
    currentStep: 1,
    projectId: null,
    fitId: null,
    fitData: null,
    videoId: null,
    videoInfo: null,
    widgets: [],
    templateName: "",
    syncMode: "auto",
    keyframes: [],
    fitMap: null,
    syncMap: null,
    validationMap: null,
    validationTileLayer: null,
    validationTrackLayer: null,
    validationMarker: null,
    validationOutlierMarkers: [],
    fitTrackLayer: null,
    syncTrackLayer: null,
    syncMarker: null,
    charts: {},
    previewPlaying: false,
    previewTimer: null,
    renderTaskId: null,
    widgetTypes: [],
    tableData: [],
    tableFiltered: [],
    tablePage: 0,
    tablePageSize: 100,
    tableSearchQuery: "",
    outlierData: null,
    originalGeoJson: null,
    originalTrackLayer: null,
    isFiltered: false,
  },

  // ── 初始化 ────────────────────────────
  init() {
    this.loadTemplates();
    this.loadWidgetTypes();
    document.getElementById("timeScaleSelect").addEventListener("change", (e) => {
      document.getElementById("customTimeScaleLabel").style.display =
        e.target.value === "custom" ? "" : "none";
    });
    document.querySelectorAll('input[name="renderRange"]').forEach(r => {
      r.addEventListener("change", (e) => {
        document.getElementById("renderRangeCustom").style.display =
          e.target.value === "custom" ? "" : "none";
      });
    });
  },

  // ── 步骤导航 ──────────────────────────
  goStep(n) {
    this.state.currentStep = n;
    document.querySelectorAll(".step-panel").forEach(p => p.classList.remove("active"));
    document.querySelectorAll(".step").forEach(s => s.classList.remove("active"));
    document.getElementById(`step${n}`).classList.add("active");
    document.querySelector(`.step[data-step="${n}"]`).classList.add("active");

    // 隐藏瓦片管理页面
    document.getElementById("tileManagerPanel").classList.remove("active");
    if (typeof TileManager !== "undefined") TileManager.destroy();

    if (n === 2) this.initStep2();
    if (n === 3) this.initStep3();
    if (n === 4) this.initStep4();
    if (n === 5) this.initStep5();
  },

  // ── 瓦片管理 ──────────────────────────
  showTileManager() {
    document.querySelectorAll(".step-panel").forEach(p => p.classList.remove("active"));
    document.querySelectorAll(".step").forEach(s => s.classList.remove("active"));
    document.getElementById("tileManagerPanel").classList.add("active");
    if (typeof TileManager !== "undefined") TileManager.init();
  },

  closeTileManager() {
    document.getElementById("tileManagerPanel").classList.remove("active");
    if (typeof TileManager !== "undefined") TileManager.destroy();
    this.goStep(this.state.currentStep);
  },

  // ── FIT 加载 ──────────────────────────
  async loadFit() {
    const path = document.getElementById("fitPathInput").value.trim();
    if (!path) { this.toast("请输入 FIT 文件路径", "error"); return; }

    const status = document.getElementById("fitStatus");
    const overlay = document.getElementById("fitLoadingOverlay");
    const loadBtn = document.querySelector("#fitPanel .btn-primary");
    const browseBtn = document.querySelector("#fitPanel .btn-secondary");

    overlay.style.display = "flex";
    loadBtn.classList.add("loading");
    loadBtn.disabled = true;
    if (browseBtn) browseBtn.disabled = true;
    status.className = "status-msg loading";
    status.innerHTML = '<div class="spinner spinner-sm"></div> 正在解析 FIT 文件…';

    try {
      const data = await API.loadFit(path);
      this.state.fitId = data.id;
      this.state.fitData = data.summary;
      this.showFitSummary(data.summary);
      status.className = "status-msg success";
      status.textContent = "✅ FIT 文件加载成功";
      this.toast("FIT 文件加载成功", "success");
    } catch (e) {
      status.className = "status-msg error";
      status.textContent = `❌ ${e.message}`;
    } finally {
      overlay.style.display = "none";
      loadBtn.classList.remove("loading");
      loadBtn.disabled = false;
      if (browseBtn) browseBtn.disabled = false;
    }
  },

  showFitSummary(summary) {
    document.getElementById("fitSummary").style.display = "";
    const session = summary.sessions && summary.sessions[0];
    if (!session) return;

    document.getElementById("fitSport").textContent = this.sportName(session.sport);
    document.getElementById("fitStartTime").textContent = session.start_time ? this.formatDateTime(session.start_time) : "--";
    document.getElementById("fitDistance").textContent = (session.total_distance / 1000).toFixed(1);
    document.getElementById("fitDuration").textContent = this.formatDuration(session.total_elapsed_time);
    document.getElementById("fitAvgSpeed").textContent = session.avg_speed ? (session.avg_speed * 3.6).toFixed(1) : "--";
    document.getElementById("fitAscent").textContent = session.total_ascent ? Math.round(session.total_ascent) : "--";

    const fields = summary.available_fields || [];
    const allFields = ["latitude", "longitude", "altitude", "heart_rate", "cadence", "speed", "distance", "power", "temperature"];
    const fieldsEl = document.getElementById("fitFields");
    fieldsEl.innerHTML = allFields.map(f => {
      const has = fields.includes(f);
      return `<span class="field-tag ${has ? '' : 'missing'}">${has ? '✅' : '❌'} ${f}</span>`;
    }).join("");
  },

  async toggleFitDetail() {
    const detail = document.getElementById("fitDetail");
    const showing = detail.style.display !== "none";
    detail.style.display = showing ? "none" : "";

    if (!showing && this.state.fitId && !this.state.fitMap) {
      this.initFitMap();
      this.initFitCharts();
    }
  },

  async initFitMap() {
    try {
      const geojson = await API.getFitTrack(this.state.fitId, false);
      this.state.fitMap = L.map("fitMap").setView([30, 120], 10);
      L.tileLayer(this._tileProxyUrl("osm"), {
        attribution: "© OSM", maxZoom: 18,
      }).addTo(this.state.fitMap);

      if (geojson.features && geojson.features.length) {
        this.state.fitTrackLayer = L.geoJSON(geojson, this._trackGeoJsonOpts()).addTo(this.state.fitMap);
        this.state.fitMap.fitBounds(this.state.fitTrackLayer.getBounds());
      }
    } catch (e) {
      console.error("地图加载失败:", e);
    }
  },

  async initFitCharts() {
    try {
      const data = await API.getFitRecords(this.state.fitId, null, null, 1500);
      const records = data.records;
      if (!records.length) return;

      const session = this.state.fitData.sessions[0];
      const startTime = session.start_time ? new Date(session.start_time).getTime() / 1000 : 0;

      const labels = records.map(r => {
        const t = new Date(r.timestamp).getTime() / 1000 - startTime;
        return this.formatSeconds(t);
      });
      const speedData = records.map(r => r.speed ? (r.speed * 3.6).toFixed(1) : null);
      const hrData = records.map(r => r.heart_rate);
      const altData = records.map(r => r.altitude ? r.altitude.toFixed(1) : null);

      const chartOpts = this._zoomableChartOpts();

      this.state.charts.speed = new Chart(document.getElementById("speedChart"), {
        type: "line",
        data: { labels, datasets: [{ data: speedData, borderColor: "#00d4aa", backgroundColor: "rgba(0,212,170,0.1)", fill: true }] },
        options: { ...chartOpts, plugins: { ...chartOpts.plugins, title: { display: true, text: "速度 (km/h) 📏滚轮缩放", color: "#00d4aa" } } },
      });

      this.state.charts.hr = new Chart(document.getElementById("hrChart"), {
        type: "line",
        data: { labels, datasets: [{ data: hrData, borderColor: "#ff4444", backgroundColor: "rgba(255,68,68,0.1)", fill: true }] },
        options: { ...chartOpts, plugins: { ...chartOpts.plugins, title: { display: true, text: "心率 (bpm) 📏滚轮缩放", color: "#ff4444" } } },
      });

      this.state.charts.alt = new Chart(document.getElementById("altChart"), {
        type: "line",
        data: { labels, datasets: [{ data: altData, borderColor: "#aa88ff", backgroundColor: "rgba(170,136,255,0.1)", fill: true }] },
        options: { ...chartOpts, plugins: { ...chartOpts.plugins, title: { display: true, text: "海拔 (m) 📏滚轮缩放", color: "#aa88ff" } } },
      });
    } catch (e) {
      console.error("图表加载失败:", e);
    }
  },

  // ── 可缩放图表通用配置 ──
  _zoomableChartOpts() {
    return {
      responsive: true,
      plugins: {
        legend: { display: false },
        zoom: {
          pan: { enabled: true, mode: "x" },
          zoom: {
            wheel: { enabled: true },
            pinch: { enabled: true },
            mode: "x",
          },
        },
      },
      scales: {
        x: { display: false },
        y: { ticks: { color: "#a0a0b0" }, grid: { color: "#2a2a4a" } },
      },
      elements: { point: { radius: 0 }, line: { tension: 0.3 } },
    };
  },

  // ── 视频加载 ──────────────────────────
  async loadVideo() {
    const path = document.getElementById("videoPathInput").value.trim();
    if (!path) { this.toast("请输入视频文件路径", "error"); return; }

    const status = document.getElementById("videoStatus");
    const overlay = document.getElementById("videoLoadingOverlay");
    const loadBtn = document.querySelector("#videoPanel .btn-primary");
    const browseBtn = document.querySelector("#videoPanel .btn-secondary");

    overlay.style.display = "flex";
    loadBtn.classList.add("loading");
    loadBtn.disabled = true;
    if (browseBtn) browseBtn.disabled = true;
    status.className = "status-msg loading";
    status.innerHTML = '<div class="spinner spinner-sm"></div> 正在分析视频文件…';

    try {
      const data = await API.loadVideo(path);
      this.state.videoId = data.id;
      this.state.videoInfo = data.info;
      this.showVideoSummary(data.info);
      status.className = "status-msg success";
      status.textContent = "✅ 视频加载成功";
      this.toast("视频加载成功", "success");
    } catch (e) {
      status.className = "status-msg error";
      status.textContent = `❌ ${e.message}`;
    } finally {
      overlay.style.display = "none";
      loadBtn.classList.remove("loading");
      loadBtn.disabled = false;
      if (browseBtn) browseBtn.disabled = false;
    }
  },

  showVideoSummary(info) {
    document.getElementById("videoSummary").style.display = "";
    document.getElementById("videoRes").textContent = `${info.width}×${info.height}`;
    document.getElementById("videoFps").textContent = info.fps;
    document.getElementById("videoDur").textContent = this.formatDuration(info.duration);
    document.getElementById("videoCodec").textContent = info.codec.toUpperCase();
    document.getElementById("videoThumb").src = API.getVideoThumbnail(this.state.videoId);
    document.getElementById("videoFpsOverride").placeholder = info.fps;
    if (info.fps > 100) {
      document.getElementById("timeScaleSelect").value = "30";
    }
  },

  // ── Step 2: 数据校验 ──────────────────
  async initStep2() {
    if (!this.state.fitId) return;

    if (!this.state.validationMap) {
      await this.initValidationMap();
    }
    this.initFilterFieldChecks();
    await this.loadTableData();
    this.initValidationCharts();
  },

  async initValidationMap() {
    try {
      const geojson = await API.getFitTrack(this.state.fitId, true, 1500);
      this.state.validationMap = L.map("validationMap").setView([30, 120], 10);
      this.switchMapSource("carto_dark");

      if (geojson.features && geojson.features.length) {
        this.state.validationTrackLayer = L.geoJSON(geojson, this._trackGeoJsonOpts({
          onEachFeature: (feature, layer) => {
            if (feature.geometry.type === "Point" && feature.properties) {
              const p = feature.properties;
              let popup = `<b>#${p.index || '?'}</b>`;
              if (p.timestamp) popup += `<br>⏱ ${new Date(p.timestamp).toLocaleTimeString("zh-CN")}`;
              if (p.speed_kmh != null) popup += `<br>💨 ${p.speed_kmh} km/h`;
              if (p.heart_rate != null) popup += `<br>❤️ ${p.heart_rate} bpm`;
              if (p.altitude != null) popup += `<br>⛰ ${p.altitude.toFixed(1)} m`;
              if (p.cadence != null) popup += `<br>🔄 ${p.cadence} rpm`;
              if (p.power != null) popup += `<br>⚡ ${p.power} W`;
              layer.bindPopup(popup);
            }
          },
        })).addTo(this.state.validationMap);
        this.state.validationMap.fitBounds(
          this.state.validationTrackLayer.getBounds ? this.state.validationTrackLayer.getBounds() :
          [[30, 120], [31, 121]]
        );
      }

      this.state.validationMarker = L.circleMarker([30, 120], {
        radius: 8, color: "#ff4444", fillColor: "#ff4444", fillOpacity: 1,
      }).addTo(this.state.validationMap);
    } catch (e) {
      console.error("校验地图加载失败:", e);
    }
  },

  // 地图源切换
  _trackGeoJsonOpts(extra = {}) {
    return {
      style: { color: "#00d4aa", weight: 3 },
      pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
        radius: 2, color: "#00d4aa", fillColor: "#00d4aa", fillOpacity: 0.6, weight: 1,
      }),
      ...extra,
    };
  },

  _mapSources: {
    osm:            { style: "osm", attr: "© OSM" },
    carto_dark:     { style: "carto_dark", attr: "© CartoDB" },
    carto_light:    { style: "carto_light", attr: "© CartoDB" },
    stamen_terrain: { style: "stamen_terrain", attr: "© Stamen" },
    esri_satellite: { style: "esri_satellite", attr: "© ESRI" },
    none:           null,
  },

  /** 构建后端瓦片代理 URL（供 Leaflet 使用） */
  _tileProxyUrl(style) {
    return `/api/tiles/map/${style}/{z}/{x}/{y}.png`;
  },

  switchMapSource(source) {
    if (!this.state.validationMap) return;
    if (this.state.validationTileLayer) {
      this.state.validationMap.removeLayer(this.state.validationTileLayer);
      this.state.validationTileLayer = null;
    }
    const cfg = this._mapSources[source];
    if (cfg) {
      this.state.validationTileLayer = L.tileLayer(this._tileProxyUrl(cfg.style), {
        attribution: cfg.attr, maxZoom: 18,
      }).addTo(this.state.validationMap);
    }
  },

  // ── 数据表格 ─────────────────────────
  async loadTableData() {
    if (!this.state.fitId) return;
    try {
      const data = await API.getFitRecords(this.state.fitId, null, null, 2000);
      this.state.tableData = data.records;
      this.state.tableFiltered = data.records;
      this.state.tablePage = 0;
      this.renderTable();
      document.getElementById("recordCountLabel").textContent = `(${data.total} 条记录)`;
    } catch (e) {
      console.error("加载表格数据失败:", e);
    }
  },

  renderTable() {
    const data = this.state.tableFiltered;
    const page = this.state.tablePage;
    const size = this.state.tablePageSize;
    const start = page * size;
    const end = Math.min(start + size, data.length);
    const pageData = data.slice(start, end);

    const outlierIndices = new Set(this.state.outlierData?.any_outlier_indices || []);

    const tbody = document.getElementById("dataTableBody");
    tbody.innerHTML = pageData.map((r, idx) => {
      const i = start + idx;
      const isOutlier = outlierIndices.has(i);
      const cls = isOutlier ? " class='outlier-row'" : "";
      const lat = r.latitude != null ? r.latitude.toFixed(6) : "--";
      const lon = r.longitude != null ? r.longitude.toFixed(6) : "--";
      const alt = r.altitude != null ? r.altitude.toFixed(1) : "--";
      const hr = r.heart_rate != null ? r.heart_rate : "--";
      const cad = r.cadence != null ? r.cadence : "--";
      const spd = r.speed != null ? (r.speed * 3.6).toFixed(1) : "--";
      const dist = r.distance != null ? (r.distance / 1000).toFixed(2) : "--";
      const pwr = r.power != null ? r.power : "--";
      const tmp = r.temperature != null ? r.temperature.toFixed(1) : "--";
      const time = r.timestamp ? new Date(r.timestamp).toLocaleTimeString("zh-CN") : "--";

      return `<tr${cls} onclick="App.highlightRecord(${i})" title="点击在地图上定位">
        <td>${i + 1}</td><td>${time}</td><td>${lat}</td><td>${lon}</td>
        <td>${alt}</td><td>${hr}</td><td>${cad}</td><td>${spd}</td>
        <td>${dist}</td><td>${pwr}</td><td>${tmp}</td></tr>`;
    }).join("");

    const totalPages = Math.ceil(data.length / size);
    const pag = document.getElementById("tablePagination");
    if (totalPages <= 1) { pag.innerHTML = ""; return; }
    let html = `<button onclick="App.tableGoPage(0)" ${page === 0 ? 'disabled' : ''}>«</button> `;
    html += `<button onclick="App.tableGoPage(${page - 1})" ${page === 0 ? 'disabled' : ''}>‹</button> `;
    html += `第 ${page + 1}/${totalPages} 页 `;
    html += `<button onclick="App.tableGoPage(${page + 1})" ${page >= totalPages - 1 ? 'disabled' : ''}>›</button> `;
    html += `<button onclick="App.tableGoPage(${totalPages - 1})" ${page >= totalPages - 1 ? 'disabled' : ''}>»</button>`;
    pag.innerHTML = html;
  },

  tableGoPage(p) {
    const totalPages = Math.ceil(this.state.tableFiltered.length / this.state.tablePageSize);
    this.state.tablePage = Math.max(0, Math.min(p, totalPages - 1));
    this.renderTable();
  },

  changeTablePageSize() {
    this.state.tablePageSize = parseInt(document.getElementById("tablePageSize").value);
    this.state.tablePage = 0;
    this.renderTable();
  },

  filterTable() {
    const q = document.getElementById("tableSearch").value.toLowerCase().trim();
    this.state.tableSearchQuery = q;
    if (!q) {
      this.state.tableFiltered = this.state.tableData;
    } else {
      this.state.tableFiltered = this.state.tableData.filter(r => {
        return Object.values(r).some(v => v != null && String(v).toLowerCase().includes(q));
      });
    }
    this.state.tablePage = 0;
    this.renderTable();
  },

  highlightRecord(index) {
    const r = this.state.tableData[index];
    if (!r || !this.state.validationMap) return;

    if (r.latitude && r.longitude) {
      this.state.validationMarker.setLatLng([r.latitude, r.longitude]);
      this.state.validationMap.panTo([r.latitude, r.longitude]);

      const info = `#${index + 1} ${new Date(r.timestamp).toLocaleTimeString()}
速度: ${r.speed ? (r.speed * 3.6).toFixed(1) + ' km/h' : '--'}
心率: ${r.heart_rate || '--'} bpm
海拔: ${r.altitude ? r.altitude.toFixed(1) + ' m' : '--'}`;
      this.state.validationMarker.bindPopup(info).openPopup();
    }
  },

  // ── 数据清洗（Sanitize）──────────────────
  async runSanitize() {
    if (!this.state.fitId) return;
    const config = {
      gps_filter_glitches: document.getElementById("sanGpsGlitch").checked,
      gps_out_of_range: document.getElementById("sanGpsRange").checked,
      gps_max_speed_ms: parseFloat(document.getElementById("sanGpsMaxSpeed").value) || 55,
      hr_range: [parseInt(document.getElementById("sanHrMin").value) || 30,
                 parseInt(document.getElementById("sanHrMax").value) || 250],
      hr_enable_rate_check: document.getElementById("sanHrRate").checked,
      hr_max_rate: parseFloat(document.getElementById("sanHrMaxRate").value) || 30,
      speed_range: [parseFloat(document.getElementById("sanSpeedMin").value) || 0,
                    parseFloat(document.getElementById("sanSpeedMax").value) || 55],
      speed_enable_accel_check: document.getElementById("sanSpeedAccel").checked,
      speed_max_accel: parseFloat(document.getElementById("sanSpeedMaxAccel").value) || 10,
      altitude_range: [parseFloat(document.getElementById("sanAltMin").value) || -500,
                       parseFloat(document.getElementById("sanAltMax").value) || 9000],
    };

    try {
      if (!this.state.originalGeoJson) {
        this.state.originalGeoJson = await API.getFitTrack(this.state.fitId, false);
      }

      const result = await API.sanitizeFit(this.state.fitId, config);
      this.state.fitData = result.summary;
      this.state.isFiltered = true;

      document.getElementById("filterStatusBadge").style.display = "";
      document.getElementById("compareToggleLabel").style.display = "";

      // 显示清洗报告
      const r = result.result;
      let html = `<div class="outlier-summary">
        原始: <strong>${r.original_count}</strong> 条 → 清洗后: <strong>${r.remaining_count}</strong> 条，
        移除: <strong style="color:var(--danger)">${r.removed_count}</strong> 条
      </div>`;
      if (r.details) {
        html += "<table class='outlier-table'><thead><tr><th>规则</th><th>移除数</th></tr></thead><tbody>";
        const labels = {
          gps_glitch: "🛰️ GPS glitch", gps_out_of_range: "🛰️ GPS 坐标范围外",
          hr_range: "❤️ 心率范围外", hr_rate: "❤️ 心率变化率异常",
          speed_range: "💨 速度范围外", speed_accel: "💨 速度加速度异常",
          altitude_range: "⛰ 海拔范围外", cadence_range: "🔄 踏频范围外",
          power_range: "⚡ 功率范围外", temperature_range: "🌡 温度范围外",
        };
        for (const [k, v] of Object.entries(r.details)) {
          if (v > 0) html += `<tr><td>${labels[k] || k}</td><td style="color:var(--danger)">${v}</td></tr>`;
        }
        html += "</tbody></table>";
      }
      document.getElementById("sanitizeResults").innerHTML = html;

      // 刷新
      await this.refreshValidationTrack();
      this.initValidationCharts();
      await this.loadTableData();
      this.toast("数据清洗完成", "success");
    } catch (e) {
      this.toast(`清洗失败: ${e.message}`, "error");
    }
  },

  // ── 平滑滤波（Smooth）──────────────────
  async applySmooth() {
    if (!this.state.fitId) return;
    const fields = this.getCheckedFilterFields();
    if (!fields.length) { this.toast("请选择至少一个平滑字段", "error"); return; }

    const config = {
      enabled: true,
      fields,
      method: document.getElementById("filterMethod").value,
      window_size: parseInt(document.getElementById("filterWindow").value) || 5,
    };

    try {
      if (!this.state.originalGeoJson) {
        this.state.originalGeoJson = await API.getFitTrack(this.state.fitId, false);
      }

      const result = await API.smoothFit(this.state.fitId, config);
      this.state.fitData = result.summary;
      this.state.isFiltered = true;

      document.getElementById("filterStatusBadge").style.display = "";
      document.getElementById("compareToggleLabel").style.display = "";

      await this.refreshValidationTrack();
      this.initValidationCharts();
      await this.loadTableData();
      this.toast("平滑滤波完成", "success");
    } catch (e) {
      this.toast(`平滑失败: ${e.message}`, "error");
    }
  },

  // ── 异常值检测（旧版）──────────────────
  async runOutlierDetection() {
    if (!this.state.fitId) return;
    const sigma = parseFloat(document.getElementById("outlierSigma").value) || 3.0;
    try {
      const result = await API.getFitOutliers(this.state.fitId, null, sigma);
      this.state.outlierData = result;
      this.renderOutlierResults(result);
      this.showOutlierMarkers(result);
      this.renderTable();
    } catch (e) {
      this.toast(`异常检测失败: ${e.message}`, "error");
    }
  },

  renderOutlierResults(result) {
    const el = document.getElementById("outlierResults");
    const total = result.total_records;
    const anyIdx = result.any_outlier_indices || [];

    if (!Object.keys(result.outliers).length && !result.gps_glitch) {
      el.innerHTML = "<p class='text-muted'>没有可检测的字段</p>";
      return;
    }

    let html = `<div class="outlier-summary">
      总记录: <strong>${total}</strong>，异常记录: <strong style="color:var(--danger)">${anyIdx.length}</strong> (${(anyIdx.length / total * 100).toFixed(1)}%)
    </div>`;

    const glitch = result.gps_glitch;
    if (glitch && glitch.glitch_count > 0) {
      html += `<div class="outlier-glitch-summary" style="margin:8px 0;padding:8px 12px;border-radius:6px;background:rgba(255,136,0,0.1);border:1px solid rgba(255,136,0,0.3);">
        <span style="color:var(--warning);font-weight:600;">🛰️ GPS Glitch</span>:
        检测到 <strong style="color:var(--warning)">${glitch.glitch_count}</strong> 个 GPS 异常点
      </div>`;
    }

    html += "<table class='outlier-table'><thead><tr><th>字段</th><th>异常数</th><th>范围外</th><th>Z-score</th><th>均值</th><th>标准差</th></tr></thead><tbody>";
    for (const [field, info] of Object.entries(result.outliers)) {
      const color = info.count > 0 ? "var(--danger)" : "var(--accent)";
      html += `<tr><td>${field}</td><td style="color:${color}">${info.count}</td><td>${info.range_outliers}</td><td>${info.zscore_outliers}</td><td>${info.mean}</td><td>${info.std}</td></tr>`;
    }
    html += "</tbody></table>";
    el.innerHTML = html;
  },

  showOutlierMarkers(result) {
    this.state.validationOutlierMarkers.forEach(m => this.state.validationMap.removeLayer(m));
    this.state.validationOutlierMarkers = [];

    const indices = new Set(result.any_outlier_indices || []);
    if (!indices.size) return;
    const glitchIndices = new Set(result.gps_glitch?.glitch_indices || []);
    const records = this.state.tableData;

    indices.forEach(i => {
      const r = records[i];
      if (r && r.latitude && r.longitude) {
        if (r.latitude < -90 || r.latitude > 90 || r.longitude < -180 || r.longitude > 180) return;
        const isGlitch = glitchIndices.has(i);
        const marker = L.circleMarker([r.latitude, r.longitude], {
          radius: isGlitch ? 6 : 4,
          color: isGlitch ? "#ff8800" : "#ff4444",
          fillColor: isGlitch ? "#ff8800" : "#ff4444",
          fillOpacity: 0.8, weight: 1,
        }).addTo(this.state.validationMap);
        const label = isGlitch ? "GPS glitch" : "异常点";
        marker.bindPopup(`${label} #${i + 1}<br>纬度: ${r.latitude.toFixed(6)}<br>经度: ${r.longitude.toFixed(6)}`);
        this.state.validationOutlierMarkers.push(marker);
      }
    });
  },

  // ── 滤波字段选择 ──
  initFilterFieldChecks() {
    const fields = this.state.fitData?.available_fields || [];
    const checkable = fields.filter(f => !["timestamp", "latitude", "longitude"].includes(f));
    const el = document.getElementById("filterFieldChecks");
    el.innerHTML = checkable.map(f =>
      `<label class="field-check"><input type="checkbox" value="${f}" checked> ${f}</label>`
    ).join("");
  },

  getCheckedFilterFields() {
    const checks = document.querySelectorAll("#filterFieldChecks input:checked");
    return Array.from(checks).map(c => c.value);
  },

  async resetFilter() {
    if (!this.state.fitId) return;
    try {
      const result = await API.resetFitData(this.state.fitId);
      this.state.fitData = result.summary;
      this.state.outlierData = null;
      this.state.isFiltered = false;
      this.state.originalGeoJson = null;
      this.toast("已重置为原始数据", "success");

      document.getElementById("filterStatusBadge").style.display = "none";
      document.getElementById("compareToggleLabel").style.display = "none";
      document.getElementById("compareToggle").checked = false;
      document.getElementById("sanitizeResults").innerHTML = "";

      if (this.state.originalTrackLayer) {
        this.state.validationMap.removeLayer(this.state.originalTrackLayer);
        this.state.originalTrackLayer = null;
      }
      this.state.validationOutlierMarkers.forEach(m => this.state.validationMap.removeLayer(m));
      this.state.validationOutlierMarkers = [];
      document.getElementById("outlierResults").innerHTML = "";

      await this.loadTableData();
      this.initValidationCharts();
      await this.refreshValidationTrack();
    } catch (e) {
      this.toast(`重置失败: ${e.message}`, "error");
    }
  },

  async refreshValidationTrack() {
    if (!this.state.validationMap || !this.state.fitId) return;
    try {
      const geojson = await API.getFitTrack(this.state.fitId, true, 1500);
      if (this.state.validationTrackLayer) {
        this.state.validationMap.removeLayer(this.state.validationTrackLayer);
      }
      if (geojson.features && geojson.features.length) {
        this.state.validationTrackLayer = L.geoJSON(geojson, this._trackGeoJsonOpts({
          onEachFeature: (feature, layer) => {
            if (feature.geometry.type === "Point" && feature.properties) {
              const p = feature.properties;
              let popup = `<b>#${p.index || '?'}</b>`;
              if (p.timestamp) popup += `<br>⏱ ${new Date(p.timestamp).toLocaleTimeString("zh-CN")}`;
              if (p.speed_kmh != null) popup += `<br>💨 ${p.speed_kmh} km/h`;
              if (p.heart_rate != null) popup += `<br>❤️ ${p.heart_rate} bpm`;
              if (p.altitude != null) popup += `<br>⛰ ${p.altitude.toFixed(1)} m`;
              layer.bindPopup(popup);
            }
          },
        })).addTo(this.state.validationMap);
        this.state.validationMap.fitBounds(this.state.validationTrackLayer.getBounds());
      }
    } catch (e) {
      console.error("刷新轨迹失败:", e);
    }
  },

  toggleCompare(showOriginal) {
    if (!this.state.validationMap) return;
    if (showOriginal && this.state.originalGeoJson) {
      if (this.state.originalTrackLayer) this.state.validationMap.removeLayer(this.state.originalTrackLayer);
      this.state.originalTrackLayer = L.geoJSON(this.state.originalGeoJson, {
        style: { color: "#ff6666", weight: 2, dashArray: "6,4", opacity: 0.7 },
      }).addTo(this.state.validationMap);
    } else {
      if (this.state.originalTrackLayer) {
        this.state.validationMap.removeLayer(this.state.originalTrackLayer);
        this.state.originalTrackLayer = null;
      }
    }
  },

  // ── 校验图表（带缩放）──────────────────
  initValidationCharts() {
    if (this.state.charts.valSpeed) this.state.charts.valSpeed.destroy();
    if (this.state.charts.valHr) this.state.charts.valHr.destroy();
    if (this.state.charts.valAlt) this.state.charts.valAlt.destroy();

    const records = this.state.tableData;
    if (!records.length) return;

    const session = this.state.fitData?.sessions?.[0];
    const startTime = session?.start_time ? new Date(session.start_time).getTime() / 1000 : 0;
    const outlierIndices = new Set(this.state.outlierData?.any_outlier_indices || []);

    const labels = records.map(r => {
      const t = new Date(r.timestamp).getTime() / 1000 - startTime;
      return this.formatSeconds(t);
    });

    const speedData = records.map(r => r.speed ? (r.speed * 3.6).toFixed(1) : null);
    const hrData = records.map(r => r.heart_rate);
    const altData = records.map(r => r.altitude ? r.altitude.toFixed(1) : null);

    const chartOpts = this._zoomableChartOpts();

    this.state.charts.valSpeed = new Chart(document.getElementById("valSpeedChart"), {
      type: "line",
      data: { labels, datasets: [{
        data: speedData, borderColor: "#00d4aa",
        backgroundColor: "rgba(0,212,170,0.1)", fill: true,
        segment: {
          borderColor: ctx => {
            const i = ctx.p0DataIndex;
            return outlierIndices.has(i) || outlierIndices.has(i + 1) ? "#ff4444" : "#00d4aa";
          }
        }
      }] },
      options: { ...chartOpts, plugins: { ...chartOpts.plugins, title: { display: true, text: "速度 (km/h) 📏滚轮缩放", color: "#00d4aa" } } },
    });

    this.state.charts.valHr = new Chart(document.getElementById("valHrChart"), {
      type: "line",
      data: { labels, datasets: [{
        data: hrData, borderColor: "#ff4444",
        backgroundColor: "rgba(255,68,68,0.1)", fill: true,
        segment: {
          borderColor: ctx => {
            const i = ctx.p0DataIndex;
            return outlierIndices.has(i) || outlierIndices.has(i + 1) ? "#ffaa00" : "#ff4444";
          }
        }
      }] },
      options: { ...chartOpts, plugins: { ...chartOpts.plugins, title: { display: true, text: "心率 (bpm) 📏滚轮缩放", color: "#ff4444" } } },
    });

    this.state.charts.valAlt = new Chart(document.getElementById("valAltChart"), {
      type: "line",
      data: { labels, datasets: [{
        data: altData, borderColor: "#aa88ff",
        backgroundColor: "rgba(170,136,255,0.1)", fill: true,
        segment: {
          borderColor: ctx => {
            const i = ctx.p0DataIndex;
            return outlierIndices.has(i) || outlierIndices.has(i + 1) ? "#ff4444" : "#aa88ff";
          }
        }
      }] },
      options: { ...chartOpts, plugins: { ...chartOpts.plugins, title: { display: true, text: "海拔 (m) 📏滚轮缩放", color: "#aa88ff" } } },
    });
  },

  // ── GPX 导出 ───────────────────────────
  async exportGpx() {
    if (!this.state.fitId) { this.toast("请先加载 FIT 文件", "error"); return; }
    const fitPath = this.state.fitId;
    const defaultPath = fitPath.replace(/\.fit$/i, ".gpx");
    try {
      const result = await API.exportFitGpx(this.state.fitId, defaultPath);
      this.toast(`GPX 导出成功: ${result.path}`, "success");
    } catch (e) {
      this.toast(`GPX 导出失败: ${e.message}`, "error");
    }
  },

  // ── Step 3: 时间同步 ──────────────────
  initStep3() {
    const session = this.state.fitData?.sessions?.[0];
    if (session) {
      const endStr = session.total_elapsed_time
        ? this.formatDateTime(new Date(new Date(session.start_time).getTime() + session.total_elapsed_time * 1000).toISOString())
        : "--";
      document.getElementById("syncFitRange").textContent =
        `FIT 时间范围: ${session.start_time ? this.formatDateTime(session.start_time) : "--"} ~ ${endStr}`;
    }
    if (this.state.videoInfo) {
      document.getElementById("syncVideoRange").textContent =
        `视频时长: ${this.formatDuration(this.state.videoInfo.duration)}`;
      document.getElementById("videoTimeSlider").max = this.state.videoInfo.duration;
      // 同步 Step 3 的 timeScale 初始值
      const step1Scale = this.getTimeScale();
      const syncTimeScale = document.getElementById("syncTimeScale");
      if (syncTimeScale && step1Scale) syncTimeScale.value = step1Scale;
    }
    // 自动填充视频起始时间
    this._prefillVideoStartTime();
    if (this.state.syncMode === "auto") this.autoSync();
    this.initSyncMap();
  },

  /** 从视频文件名（DJI 格式）或文件修改时间自动推断 video_start_time */
  _inferVideoStartTime() {
    const videoInfo = this.state.videoInfo;
    if (!videoInfo) return null;
    // 1. 尝试从 DJI 文件名解析：DJI_20260404073819_0003_D.MP4
    const fname = (videoInfo.file_path || "").split(/[\\/]/).pop() || "";
    const djiMatch = fname.match(/DJI_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/);
    if (djiMatch) {
      const [, y, mo, d, h, mi, s] = djiMatch;
      return new Date(parseInt(y), parseInt(mo) - 1, parseInt(d), parseInt(h), parseInt(mi), parseInt(s));
    }
    // 2. 回退到文件修改时间 - 视频时长
    if (videoInfo.file_mtime) {
      return new Date(new Date(videoInfo.file_mtime).getTime() - videoInfo.duration * 1000);
    }
    return null;
  },

  /** 预填充视频起始时间输入框 */
  _prefillVideoStartTime() {
    const input = document.getElementById("syncVideoStartTime");
    const hint = document.getElementById("syncVideoStartHint");
    if (!input) return;
    // 如果已经有值，不覆盖
    if (input.value) return;
    const inferred = this._inferVideoStartTime();
    if (inferred) {
      // datetime-local step=0.001 支持 YYYY-MM-DDTHH:mm:ss.SSS 格式
      const pad = n => String(n).padStart(2, "0");
      const pad3 = n => String(n).padStart(3, "0");
      input.value = `${inferred.getFullYear()}-${pad(inferred.getMonth() + 1)}-${pad(inferred.getDate())}T${pad(inferred.getHours())}:${pad(inferred.getMinutes())}:${pad(inferred.getSeconds())}.${pad3(inferred.getMilliseconds())}`;
      if (hint) {
        const source = (this.state.videoInfo?.file_path || "").split(/[\\/]/).pop()?.match(/^DJI_/)
          ? "从文件名推断" : "从文件修改时间推断";
        hint.textContent = `(自动填充: ${source})`;
      }
    }
  },

  async initSyncMap() {
    if (this.state.syncMap) return;
    if (!this.state.fitId) return;
    try {
      const geojson = await API.getFitTrack(this.state.fitId, false);
      this.state.syncMap = L.map("syncMap").setView([30, 120], 10);
      L.tileLayer(this._tileProxyUrl("osm"), {
        attribution: "© OSM", maxZoom: 18,
      }).addTo(this.state.syncMap);
      if (geojson.features && geojson.features.length) {
        this.state.syncTrackLayer = L.geoJSON(geojson, this._trackGeoJsonOpts()).addTo(this.state.syncMap);
        this.state.syncMap.fitBounds(this.state.syncTrackLayer.getBounds());
      }
      this.state.syncMarker = L.circleMarker([30, 120], {
        radius: 8, color: "#ff4444", fillColor: "#ff4444", fillOpacity: 1,
      }).addTo(this.state.syncMap);
    } catch (e) {
      console.error("同步地图加载失败:", e);
    }
  },

  setSyncMode(mode) {
    this.state.syncMode = mode;
    document.getElementById("syncManual").style.display = mode === "manual" ? "" : "none";
    document.getElementById("syncAuto").style.display = mode === "auto" ? "" : "none";
    document.getElementById("syncKeyframe").style.display = mode === "keyframe" ? "" : "none";
    if (mode === "auto") this.autoSync();
    if (mode === "manual") this._updateManualComputed();
  },

  autoSync() {
    const session = this.state.fitData?.sessions?.[0];
    const videoInfo = this.state.videoInfo;
    const result = document.getElementById("syncAutoResult");
    if (!session?.start_time) {
      result.innerHTML = "<p style='color:var(--warning)'>⚠️ 无 FIT 起始时间</p>";
      return;
    }
    const inferred = this._inferVideoStartTime();
    if (!inferred) {
      result.innerHTML = "<p style='color:var(--warning)'>⚠️ 无法自动推断视频起始时间</p>";
      return;
    }
    const fitStart = new Date(session.start_time);
    const diff = (fitStart.getTime() - inferred.getTime()) / 1000;
    const sign = diff >= 0 ? "+" : "";
    result.innerHTML = `
      <p>推断视频起始时间: <strong>${inferred.toLocaleString()}</strong></p>
      <p>FIT 起始时间: <strong>${fitStart.toLocaleString()}</strong></p>
      <p>两者差: <strong>${sign}${diff.toFixed(1)}s</strong> ${Math.abs(diff) < 2 ? "✅ 基本同步" : diff > 0 ? "(视频早于 FIT)" : "(视频晚于 FIT)"}</p>
    `;
  },

  /** 获取视频起始时间（绝对时间） */
  getVideoStartTime() {
    const mode = this.state.syncMode;
    if (mode === "manual") {
      const input = document.getElementById("syncVideoStartTime");
      if (input && input.value) {
        return new Date(input.value);
      }
      return this._inferVideoStartTime();
    }
    // auto / keyframe: 自动推断
    return this._inferVideoStartTime();
  },

  /** 获取微调偏移（秒） */
  getSyncOffset() {
    if (this.state.syncMode === "manual") {
      return parseFloat(document.getElementById("syncManualOffset")?.value || 0);
    }
    return 0;
  },

  /** 构建 time_sync 对象（统一接口） */
  getTimeSync() {
    const videoStartTime = this.getVideoStartTime();
    const session = this.state.fitData?.sessions?.[0];
    return {
      video_start_time: videoStartTime ? videoStartTime.toISOString() : null,
      fit_start_time: session?.start_time || null,
      offset_seconds: this.getSyncOffset(),
      time_scale: this.getTimeScale(),
    };
  },

  /** 手动偏移模式下更新计算结果显示 */
  _updateManualComputed() {
    const el = document.getElementById("syncManualComputed");
    if (!el) return;
    const videoStartTime = this.getVideoStartTime();
    const session = this.state.fitData?.sessions?.[0];
    const offset = this.getSyncOffset();
    if (!videoStartTime || !session?.start_time) {
      el.innerHTML = "<span class='text-muted'>请设置视频起始时间</span>";
      return;
    }
    const fitStart = new Date(session.start_time);
    const diff = (fitStart.getTime() - videoStartTime.getTime()) / 1000 + offset;
    const sign = diff >= 0 ? "+" : "";
    el.innerHTML = `<span class='text-muted'>视频 0s → FIT ${sign}${diff.toFixed(1)}s | FIT 起始: ${fitStart.toLocaleString()} | 视频起始: ${videoStartTime.toLocaleString()}</span>`;
  },

  _videoFrameDebounce: null,
  _syncMarkerDebounce: null,

  async onVideoTimeChange(sec) {
    const s = parseFloat(sec);
    document.getElementById("videoTimeLabel").textContent = this.formatSeconds(s);
    this._updateVideoFrameInfo(s);

    // 防抖：帧预览 300ms，同步标记 500ms
    if (this.state.videoId) {
      clearTimeout(this._videoFrameDebounce);
      const id = this.state.videoId;
      this._videoFrameDebounce = setTimeout(() => {
        document.getElementById("videoFramePreview").src = API.getVideoFrame(id, s);
      }, 300);
    }

    clearTimeout(this._syncMarkerDebounce);
    this._syncMarkerDebounce = setTimeout(() => {
      this.updateSyncMarker(s);
    }, 500);
  },

  /** 单帧步进（slider 是视频绝对时间，步进量 = 1/fps，不受 timeScale 影响） */
  stepVideoFrame(direction) {
    const slider = document.getElementById("videoTimeSlider");
    const fps = this.state.videoInfo?.fps || 29.97;
    const overrideFps = parseFloat(document.getElementById("videoFpsOverride")?.value);
    const effectiveFps = overrideFps > 0 ? overrideFps : fps;
    const frameDuration = 1 / effectiveFps;
    const cur = parseFloat(slider.value);
    const next = Math.max(0, Math.min(parseFloat(slider.max), cur + direction * frameDuration));
    slider.value = next;
    this.onVideoTimeChange(next);
  },

  /** 更新帧号信息显示 */
  _updateVideoFrameInfo(sec) {
    const el = document.getElementById("videoFrameInfo");
    if (!el) return;
    const fps = this.state.videoInfo?.fps || 29.97;
    const overrideFps = parseFloat(document.getElementById("videoFpsOverride")?.value);
    const effectiveFps = overrideFps > 0 ? overrideFps : fps;
    const frameIdx = Math.round(sec * effectiveFps);
    const timeScale = this.getTimeScale() || 1;
    const fitElapsed = sec * timeScale;
    el.textContent = `帧 #${frameIdx} | ${effectiveFps.toFixed(1)}fps | FIT ${this.formatDuration(fitElapsed)}`;
  },

  async updateSyncMarker(videoSec) {
    if (!this.state.syncMap || !this.state.fitId) return;
    const videoStartTime = this.getVideoStartTime();
    if (!videoStartTime) return;
    const timeScale = this.getTimeScale();
    const offset = this.getSyncOffset();
    // 绝对时间 = video_start_time + video_elapsed * time_scale + offset
    const fitTime = new Date(videoStartTime.getTime() + (videoSec * timeScale + offset) * 1000);
    document.getElementById("syncFitTime").textContent = `FIT: ${fitTime.toLocaleTimeString()}`;
    try {
      const data = await API.getFitRecords(this.state.fitId, fitTime.getTime() / 1000 - 5, fitTime.getTime() / 1000 + 5);
      if (data.records.length) {
        const r = data.records.find(r => r.latitude && r.longitude) || data.records[0];
        if (r.latitude && r.longitude) this.state.syncMarker.setLatLng([r.latitude, r.longitude]);
      }
    } catch (e) { /* ignore */ }
  },

  getTimeScale() {
    // Step 3 手动偏移模式下的 timeScale 优先
    const syncTimeScale = document.getElementById("syncTimeScale");
    if (this.state.syncMode === "manual" && syncTimeScale && syncTimeScale.value) {
      return parseFloat(syncTimeScale.value) || 1;
    }
    // 否则用 Step 1 视频面板中的延时倍率
    const sel = document.getElementById("timeScaleSelect").value;
    if (sel === "custom") return parseFloat(document.getElementById("customTimeScale").value || 1);
    return parseFloat(sel || 1);
  },

  addKeyframe() {
    const idx = this.state.keyframes.length + 1;
    this.state.keyframes.push({ video_sec: 0, fit_time: null });
    const tbody = document.querySelector("#keyframeTable tbody");
    const tr = document.createElement("tr");
    const session = this.state.fitData?.sessions?.[0];
    const fitStartVal = session?.start_time
      ? new Date(session.start_time).toISOString().slice(0, 23)
      : "";
    tr.innerHTML = `<td>${idx}</td><td><input type="number" value="0" step="0.001" style="width:100px;"></td><td><input type="datetime-local" value="${fitStartVal}" step="0.001"></td><td><button onclick="this.closest('tr').remove()" class="btn-danger" style="padding:2px 8px">删</button></td>`;
    tbody.appendChild(tr);
  },

  // ── Step 4: 叠加设计 ──────────────────
  async initStep4() {
    await this.loadTemplates();
    this.initCanvas();
    if (this.state.videoInfo) {
      document.getElementById("overlayTimeSlider").max = this.state.videoInfo.duration;
      document.getElementById("overlayTimeSlider").step = 1 / (this.state.videoInfo.fps || 29.97);
    }
  },

  async loadTemplates() {
    try {
      const data = await API.getTemplates();
      const sel = document.getElementById("templateSelect");
      if (sel.options.length <= 1) {
        data.templates.forEach(t => {
          const opt = document.createElement("option");
          opt.value = t.name;
          opt.textContent = t.display_name;
          sel.appendChild(opt);
        });
      }
    } catch (e) { console.error(e); }
  },

  async loadWidgetTypes() {
    try {
      const data = await API.getWidgetTypes();
      this.state.widgetTypes = data.types;
    } catch (e) { console.error(e); }
  },

  initCanvas() {
    const videoInfo = this.state.videoInfo;
    const w = videoInfo?.width || 1920;
    const h = videoInfo?.height || 1080;
    const canvas = document.getElementById("previewCanvas");
    canvas.width = w;
    canvas.height = h;
    if (this.state.videoId) this.requestPreview(0);
  },

  _previewDebounce: null,
  requestPreview(sec) {
    clearTimeout(this._previewDebounce);
    this._previewDebounce = setTimeout(() => this._doPreview(sec), 300);
  },

  async _doPreview(sec) {
    if (!this.state.fitId) return;
    const canvas = document.getElementById("previewCanvas");
    const ctx = canvas.getContext("2d");
    const videoInfo = this.state.videoInfo || {};
    const session = this.state.fitData?.sessions?.[0];

    try {
      const resp = await fetch("/api/render/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fit_path: this.state.fitId,
          video_path: this.state.videoId || "",
          video_time_sec: sec,
          widgets: this.state.widgets,
          time_sync: this.getTimeSync(),
          include_background: !!(this.state.videoId),
          canvas_width: videoInfo.width || 1920,
          canvas_height: videoInfo.height || 1080,
        }),
      });

      if (!resp.ok) {
        this.drawFallbackPreview(sec);
        return;
      }

      const blob = await resp.blob();
      const img = new Image();
      img.onload = () => {
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        this.drawWidgetOverlays(ctx);
      };
      img.src = URL.createObjectURL(blob);
    } catch (e) {
      this.drawFallbackPreview(sec);
    }
  },

  drawFallbackPreview(sec) {
    const canvas = document.getElementById("previewCanvas");
    const ctx = canvas.getContext("2d");
    if (this.state.videoId) {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.src = API.getVideoFrame(this.state.videoId, sec);
      img.onload = () => {
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        this.drawWidgetOverlays(ctx);
      };
    } else {
      ctx.fillStyle = "#1a1a2e";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      this.drawWidgetOverlays(ctx);
    }
  },

  drawWidgetOverlays(ctx) {
    this.state.widgets.forEach(w => {
      if (!w.visible) return;
      ctx.save();
      ctx.strokeStyle = "rgba(0,212,170,0.6)";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(w.x, w.y, w.width, w.height);
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(0,0,0,0.4)";
      ctx.font = "12px monospace";
      ctx.fillText(this.widgetLabel(w.widget_type), w.x + 4, w.y + 14);
      ctx.restore();
    });
  },

  async selectTemplate(name) {
    if (!name) return;
    this.state.templateName = name;
    const videoInfo = this.state.videoInfo;
    const w = videoInfo?.width || 1920;
    const h = videoInfo?.height || 1080;
    try {
      const data = await API.getTemplate(name, w, h);
      this.state.widgets = data.widgets;
      this.renderWidgetList();
      this.renderWidgetOverlay();
      // 自动调整所有 MapTrack Widget 的宽高比（仅概览模式）
      if (this.state.fitId) {
        const mapTrackIndices = this.state.widgets
          .map((w, i) => w.widget_type === "MapTrack" && w.style?.auto_aspect && (w.style?.map_mode || "overview") === "overview" ? i : -1)
          .filter(i => i >= 0);
        for (const idx of mapTrackIndices) {
          await this._autoAdjustMapTrackAspect(idx);
        }
      }
      this.requestPreview(0);
      this.toast(`已应用模板: ${name}`, "success");
    } catch (e) {
      this.toast(`模板加载失败: ${e.message}`, "error");
    }
  },

  renderWidgetList() {
    const list = document.getElementById("widgetList");
    list.innerHTML = this.state.widgets.map((w, i) => `
      <div class="widget-item ${w.visible ? '' : 'hidden-widget'}" data-index="${i}">
        <span class="widget-icon">${this.widgetIcon(w.widget_type)}</span>
        <span class="widget-info">
          ${this.widgetLabel(w.widget_type)}<br>
          <small>位置: (${w.x}, ${w.y}) ${w.width}×${w.height}</small>
        </span>
        <span class="widget-actions">
          <button onclick="App.toggleWidget(${i})" title="${w.visible ? '隐藏' : '显示'}">${w.visible ? '👁' : '👁‍🗨'}</button>
          <button onclick="App.editWidget(${i})" title="编辑">✏️</button>
          <button onclick="App.removeWidget(${i})" title="删除">🗑️</button>
        </span>
      </div>
    `).join("");
  },

  renderWidgetOverlay() {
    const overlay = document.getElementById("widgetOverlay");
    const videoInfo = this.state.videoInfo;
    const cw = videoInfo?.width || 1920;
    const ch = videoInfo?.height || 1080;
    overlay.innerHTML = this.state.widgets.map((w, i) => {
      if (!w.visible) return "";
      const el = overlay.parentElement;
      const scale = el.offsetWidth / cw;
      return `<div class="widget-el" data-index="${i}"
        style="left:${w.x * scale}px; top:${w.y * scale}px; width:${w.width * scale}px; height:${w.height * scale}px;"
        onmousedown="App.startDrag(event, ${i})">
        <span class="widget-label">${this.widgetLabel(w.widget_type)}</span>
        <div class="resize-handle resize-nw" data-dir="nw"></div>
        <div class="resize-handle resize-n" data-dir="n"></div>
        <div class="resize-handle resize-ne" data-dir="ne"></div>
        <div class="resize-handle resize-e" data-dir="e"></div>
        <div class="resize-handle resize-se" data-dir="se"></div>
        <div class="resize-handle resize-s" data-dir="s"></div>
        <div class="resize-handle resize-sw" data-dir="sw"></div>
        <div class="resize-handle resize-w" data-dir="w"></div>
      </div>`;
    }).join("");
  },

  startDrag(e, index) {
    e.preventDefault();
    e.stopPropagation();

    const el = e.currentTarget;
    const overlay = document.getElementById("widgetOverlay");
    const cw = (this.state.videoInfo?.width || 1920);
    const ch = (this.state.videoInfo?.height || 1080);
    const scale = overlay.offsetWidth / cw;
    const startX = e.clientX;
    const startY = e.clientY;
    const origLeft = parseInt(el.style.left);
    const origTop = parseInt(el.style.top);
    const origWidth = parseInt(el.style.width);
    const origHeight = parseInt(el.style.height);

    // 判断是否点击了 resize 手柄
    const target = e.target;
    const isResizeHandle = target.classList.contains("resize-handle");
    const dir = isResizeHandle ? target.dataset.dir : null;

    // MapTrack 概览模式 + auto_aspect 时锁定宽高比
    const widget = this.state.widgets[index];
    const isMapTrackOverview = widget.widget_type === "MapTrack" && (widget.style?.map_mode || "overview") === "overview" && widget.style?.auto_aspect !== false;
    const lockAspect = isMapTrackOverview;
    const aspectRatio = widget.width / widget.height;

    if (isResizeHandle && dir) {
      // ── Resize 模式 ──
      const minSize = 30 * scale;  // 最小尺寸 30px（视频像素）

      const onMove = (ev) => {
        const dx = ev.clientX - startX;
        const dy = ev.clientY - startY;
        let newLeft = origLeft, newTop = origTop;
        let newWidth = origWidth, newHeight = origHeight;

        // 根据方向调整
        if (dir.includes("e")) newWidth = Math.max(minSize, origWidth + dx);
        if (dir.includes("w")) { newWidth = Math.max(minSize, origWidth - dx); newLeft = origLeft + origWidth - newWidth; }
        if (dir.includes("s")) newHeight = Math.max(minSize, origHeight + dy);
        if (dir.includes("n")) { newHeight = Math.max(minSize, origHeight - dy); newTop = origTop + origHeight - newHeight; }

        // 锁定宽高比
        if (lockAspect) {
          if (dir === "e" || dir === "w") {
            newHeight = newWidth / aspectRatio;
          } else if (dir === "n" || dir === "s") {
            newWidth = newHeight * aspectRatio;
          } else {
            // 角落拖拽：以宽度为主
            newHeight = newWidth / aspectRatio;
            if (dir.includes("n")) {
              newTop = origTop + origHeight - newHeight;
            }
          }
          // w 方向调整 left
          if (dir.includes("w")) {
            newLeft = origLeft + origWidth - newWidth;
          }
        }

        el.style.left = newLeft + "px";
        el.style.top = newTop + "px";
        el.style.width = newWidth + "px";
        el.style.height = newHeight + "px";
      };

      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        widget.x = Math.max(0, Math.round(parseInt(el.style.left) / scale));
        widget.y = Math.max(0, Math.round(parseInt(el.style.top) / scale));
        widget.width = Math.max(30, Math.round(parseInt(el.style.width) / scale));
        widget.height = Math.max(30, Math.round(parseInt(el.style.height) / scale));
        this.renderWidgetList();
        this.requestPreview(parseFloat(document.getElementById("overlayTimeSlider").value));
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);

    } else {
      // ── 拖拽移动模式 ──
      const onMove = (ev) => {
        el.style.left = (origLeft + ev.clientX - startX) + "px";
        el.style.top = (origTop + ev.clientY - startY) + "px";
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        this.state.widgets[index].x = Math.max(0, Math.round(parseInt(el.style.left) / scale));
        this.state.widgets[index].y = Math.max(0, Math.round(parseInt(el.style.top) / scale));
        this.renderWidgetList();
        this.requestPreview(parseFloat(document.getElementById("overlayTimeSlider").value));
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    }
  },

  toggleWidget(i) {
    this.state.widgets[i].visible = !this.state.widgets[i].visible;
    this.renderWidgetList();
    this.renderWidgetOverlay();
    this.requestPreview(parseFloat(document.getElementById("overlayTimeSlider").value));
  },

  removeWidget(i) {
    this.state.widgets.splice(i, 1);
    this.renderWidgetList();
    this.renderWidgetOverlay();
    this.requestPreview(parseFloat(document.getElementById("overlayTimeSlider").value));
  },

  async addWidget() {
    const types = this.state.widgetTypes;
    if (!types.length) {
      try { const data = await API.getWidgetTypes(); this.state.widgetTypes = data.types; }
      catch (e) { return; }
    }
    const body = document.getElementById("addWidgetBody");
    body.innerHTML = this.state.widgetTypes.map(t => `
      <div class="widget-type-card" onclick="App.doAddWidget('${t.type}')">
        <span class="wt-icon">${this.widgetIcon(t.type)}</span>
        <span class="wt-name">${t.label}</span>
        <span class="wt-desc">${t.description}</span>
      </div>
    `).join("");
    document.getElementById("addWidgetModal").style.display = "";
  },

  doAddWidget(type) {
    const w = this.state.videoInfo?.width || 1920;
    const h = this.state.videoInfo?.height || 1080;
    const defaults = {
      MapTrack: { width: 300, height: 225, style: { map_style: "dark", track_color: "#00d4aa", marker_color: "#ff4444", marker_size: 6, auto_aspect: true } },
      SpeedGauge: { width: 150, height: 80, style: { color: "#00d4aa", font_size: 32, unit: "km/h", format: "arc", min_val: 0, max_val: 80 } },
      HeartRateGauge: { width: 150, height: 80, style: { color: "#ff4444", font_size: 32, unit: "bpm", format: "arc", min_val: 40, max_val: 200 } },
      CadenceGauge: { width: 150, height: 80, style: { color: "#4488ff", font_size: 32, unit: "rpm", format: "arc", min_val: 0, max_val: 150 } },
      PowerGauge: { width: 150, height: 80, style: { color: "#ffaa00", font_size: 32, unit: "W", format: "arc", min_val: 0, max_val: 400 } },
      ElevationGauge: { width: 130, height: 50, style: { color: "#aa88ff", font_size: 24, unit: "m" } },
      AltitudeChart: { width: 300, height: 80, style: { line_color: "#aa88ff", fill_color: "#aa88ff30" } },
      DistanceCounter: { width: 130, height: 50, style: { color: "#ffffff", font_size: 24, unit: "km" } },
      TimerDisplay: { width: 130, height: 40, style: { color: "#ffffff", font_size: 22 } },
      GradientIndicator: { width: 100, height: 40, style: { color: "#ffaa00", font_size: 24, unit: "%" } },
      CustomLabel: { width: 150, height: 30, style: { color: "#ffffff", font_size: 16, text: "Label" } },
    };
    const def = defaults[type] || { width: 120, height: 60, style: {} };
    const widget = {
      widget_type: type, x: 50, y: 50, width: def.width, height: def.height,
      opacity: 1.0, data_field: "", visible: true, style: { ...def.style },
    };
    this.state.widgets.push(widget);
    this.closeAddWidget();
    this.renderWidgetList();
    this.renderWidgetOverlay();

    // MapTrack 概览模式自动获取轨迹宽高比（跟随模式不需要）
    if (type === "MapTrack" && this.state.fitId && (style.map_mode || "overview") === "overview") {
      this._autoAdjustMapTrackAspect(this.state.widgets.length - 1);
    }

    this.requestPreview(parseFloat(document.getElementById("overlayTimeSlider").value));
  },

  /** 自动调整 MapTrack Widget 的宽高比，使其匹配真实轨迹 */
  async _autoAdjustMapTrackAspect(widgetIndex) {
    if (!this.state.fitId) return;
    try {
      const data = await API.getFitTrackAspect(this.state.fitId);
      const aspectRatio = data.aspect_ratio;
      if (aspectRatio && aspectRatio > 0) {
        const w = this.state.widgets[widgetIndex];
        if (!w) return;
        // 保持当前宽度，根据宽高比调整高度
        // 限制最大宽高
        const maxWidth = Math.min(w.width, (this.state.videoInfo?.width || 1920) - w.x - 20);
        const maxHeight = Math.min((this.state.videoInfo?.height || 1080) - w.y - 20);
        let newWidth = w.width;
        let newHeight = Math.round(newWidth / aspectRatio);
        // 如果高度超出，则以高度为准
        if (newHeight > maxHeight) {
          newHeight = maxHeight;
          newWidth = Math.round(newHeight * aspectRatio);
        }
        if (newWidth > maxWidth) {
          newWidth = maxWidth;
          newHeight = Math.round(newWidth / aspectRatio);
        }
        w.width = Math.max(60, newWidth);
        w.height = Math.max(60, newHeight);
        this.renderWidgetList();
        this.renderWidgetOverlay();
      }
    } catch (e) {
      console.warn("获取轨迹宽高比失败:", e);
    }
  },

  closeAddWidget() { document.getElementById("addWidgetModal").style.display = "none"; },

  editWidget(i) {
    const w = this.state.widgets[i];
    document.getElementById("widgetEditTitle").textContent = `编辑: ${this.widgetLabel(w.widget_type)}`;
    let html = `
      <div class="edit-grid">
        <label>X: <input type="number" id="we_x" value="${w.x}"></label>
        <label>Y: <input type="number" id="we_y" value="${w.y}"></label>
        <label>宽: <input type="number" id="we_w" value="${w.width}"></label>
        <label>高: <input type="number" id="we_h" value="${w.height}"></label>
      </div>
      <label>透明度: <input type="range" id="we_opacity" min="0" max="1" step="0.05" value="${w.opacity}"></label>
      <label>文字颜色: <input type="color" id="we_color" value="${(w.style.color || '#00d4aa').slice(0,7)}"></label>
      <label>字体大小: <input type="number" id="we_fontSize" value="${w.style.font_size || 28}"></label>
      <label>单位: <input type="text" id="we_unit" value="${w.style.unit || ''}"></label>
    `;
    if (["SpeedGauge", "HeartRateGauge", "CadenceGauge", "PowerGauge"].includes(w.widget_type)) {
      html += `
        <label>显示样式: <select id="we_format"><option value="number" ${w.style.format !== 'arc' ? 'selected' : ''}>数字</option><option value="arc" ${w.style.format === 'arc' ? 'selected' : ''}>圆弧</option></select></label>
        <div class="edit-grid"><label>最小值: <input type="number" id="we_minVal" value="${w.style.min_val || 0}"></label><label>最大值: <input type="number" id="we_maxVal" value="${w.style.max_val || 100}"></label></div>
      `;
    }
    if (w.widget_type === "MapTrack") {
      html += `
        <label>轨迹颜色: <input type="color" id="we_trackColor" value="${(w.style.track_color || '#00d4aa').slice(0,7)}"></label>
        <label>标记颜色: <input type="color" id="we_markerColor" value="${(w.style.marker_color || '#ff4444').slice(0,7)}"></label>
        <label>地图模式:
          <select id="we_mapMode" onchange="App._onMapModeChange(this.value)">
            <option value="overview" ${(w.style.map_mode || 'overview') === 'overview' ? 'selected' : ''}>轨迹全览</option>
            <option value="follow" ${w.style.map_mode === 'follow' ? 'selected' : ''}>地图跟随</option>
          </select>
        </label>
        <label>底图源:
          <select id="we_tileSource">
            <option value="" ${!w.style.tile_source ? 'selected' : ''}>无底图（矢量）</option>
            <option value="carto_dark" ${w.style.tile_source === 'carto_dark' ? 'selected' : ''}>CartoDB 暗色</option>
            <option value="carto_light" ${w.style.tile_source === 'carto_light' ? 'selected' : ''}>CartoDB 亮色</option>
            <option value="osm" ${w.style.tile_source === 'osm' ? 'selected' : ''}>OpenStreetMap</option>
            <option value="esri_satellite" ${w.style.tile_source === 'esri_satellite' ? 'selected' : ''}>ESRI 卫星</option>
            <option value="stamen_terrain" ${w.style.tile_source === 'stamen_terrain' ? 'selected' : ''}>Stamen 地形</option>
          </select>
        </label>
        <label>缩放级别: <input type="number" id="we_zoom" value="${w.style.zoom || 0}" min="0" max="18" step="1"> <small class="text-muted">(0=自动)</small></label>
        <label>跟随缩放: <input type="number" id="we_followZoom" value="${w.style.follow_zoom || 15}" min="10" max="18" step="1"> <small class="text-muted">(地图跟随模式)</small></label>
        <label id="we_autoAspectLabel"><input type="checkbox" id="we_autoAspect" ${(w.style.map_mode || "overview") === "follow" ? 'disabled' : ''} ${w.style.auto_aspect !== false ? 'checked' : ''}> 自动匹配轨迹宽高比</label>
      `;
    }
    if (w.widget_type === "CustomLabel") {
      html += `<label>文字: <input type="text" id="we_text" value="${w.style.text || 'Label'}"></label>`;
    }
    if (w.widget_type === "AltitudeChart") {
      html += `<label>线条颜色: <input type="color" id="we_lineColor" value="${(w.style.line_color || '#aa88ff').slice(0,7)}"></label>`;
    }
    document.getElementById("widgetEditBody").innerHTML = html;
    this._editingWidget = i;
    document.getElementById("widgetEditModal").style.display = "";
  },

  /** 地图模式下拉框切换回调 */
  _onMapModeChange(mode) {
    const autoAspectEl = document.getElementById("we_autoAspect");
    if (!autoAspectEl) return;
    if (mode === "follow") {
      autoAspectEl.checked = false;
      autoAspectEl.disabled = true;
    } else {
      autoAspectEl.disabled = false;
    }
  },

  saveWidgetEdit() {
    const i = this._editingWidget;
    if (i == null) return;
    const w = this.state.widgets[i];
    w.x = parseInt(document.getElementById("we_x").value) || 0;
    w.y = parseInt(document.getElementById("we_y").value) || 0;
    w.width = parseInt(document.getElementById("we_w").value) || 100;
    w.height = parseInt(document.getElementById("we_h").value) || 100;
    w.opacity = parseFloat(document.getElementById("we_opacity").value);
    w.style.color = document.getElementById("we_color").value;
    w.style.font_size = parseInt(document.getElementById("we_fontSize").value) || 28;
    w.style.unit = document.getElementById("we_unit").value;
    const formatEl = document.getElementById("we_format");
    if (formatEl) w.style.format = formatEl.value;
    const minValEl = document.getElementById("we_minVal");
    if (minValEl) w.style.min_val = parseFloat(minValEl.value);
    const maxValEl = document.getElementById("we_maxVal");
    if (maxValEl) w.style.max_val = parseFloat(maxValEl.value);
    const trackColorEl = document.getElementById("we_trackColor");
    if (trackColorEl) w.style.track_color = trackColorEl.value;
    const markerColorEl = document.getElementById("we_markerColor");
    if (markerColorEl) w.style.marker_color = markerColorEl.value;
    const tileSourceEl = document.getElementById("we_tileSource");
    if (tileSourceEl) w.style.tile_source = tileSourceEl.value;
    const zoomEl = document.getElementById("we_zoom");
    if (zoomEl) w.style.zoom = parseInt(zoomEl.value) || 0;
    const mapModeEl = document.getElementById("we_mapMode");
    if (mapModeEl) w.style.map_mode = mapModeEl.value;
    const followZoomEl = document.getElementById("we_followZoom");
    if (followZoomEl) w.style.follow_zoom = parseInt(followZoomEl.value) || 15;
    const autoAspectEl = document.getElementById("we_autoAspect");
    if (autoAspectEl) w.style.auto_aspect = autoAspectEl.checked;
    // 跟随模式强制关闭自动宽高比
    if (w.widget_type === "MapTrack" && w.style.map_mode === "follow") {
      w.style.auto_aspect = false;
    }
    const textEl = document.getElementById("we_text");
    if (textEl) w.style.text = textEl.value;
    const lineColorEl = document.getElementById("we_lineColor");
    if (lineColorEl) w.style.line_color = lineColorEl.value;

    // MapTrack 概览模式自动宽高比
    if (w.widget_type === "MapTrack" && w.style.auto_aspect && this.state.fitId && (w.style.map_mode || "overview") === "overview") {
      this._autoAdjustMapTrackAspect(i);
    }

    this.closeWidgetEdit();
    this.renderWidgetList();
    this.renderWidgetOverlay();
    this.requestPreview(parseFloat(document.getElementById("overlayTimeSlider").value));
  },

  closeWidgetEdit() { document.getElementById("widgetEditModal").style.display = "none"; this._editingWidget = null; },

  onOverlayTimeChange(sec) {
    document.getElementById("overlayTimeLabel").textContent = this.formatSeconds(parseFloat(sec));
    this.requestPreview(parseFloat(sec));
  },

  stepOverlayFrame(direction) {
    const slider = document.getElementById("overlayTimeSlider");
    const fps = this.state.videoInfo?.fps || 29.97;
    const overrideFps = parseFloat(document.getElementById("videoFpsOverride")?.value);
    const effectiveFps = overrideFps > 0 ? overrideFps : fps;
    const frameDuration = 1 / effectiveFps;
    const cur = parseFloat(slider.value);
    const next = Math.max(0, Math.min(parseFloat(slider.max), cur + direction * frameDuration));
    slider.value = next;
    this.onOverlayTimeChange(next);
  },

  previewPrevFrame() {
    this.stepOverlayFrame(-1);
  },

  previewPlay() {
    if (this.state.previewPlaying) return;
    this.state.previewPlaying = true;
    const slider = document.getElementById("overlayTimeSlider");
    const max = parseFloat(slider.max);
    const tick = () => {
      if (!this.state.previewPlaying) return;
      let cur = parseFloat(slider.value) + 0.5;
      if (cur > max) { this.state.previewPlaying = false; return; }
      slider.value = cur;
      this.onOverlayTimeChange(cur);
      this.state.previewTimer = setTimeout(tick, 500);
    };
    tick();
  },

  previewPause() { this.state.previewPlaying = false; clearTimeout(this.state.previewTimer); },

  setGlobalOpacity(v) {
    this.state.widgets.forEach(w => w.opacity = parseFloat(v));
    this.requestPreview(parseFloat(document.getElementById("overlayTimeSlider").value));
  },

  setGlobalScale(v) {
    const scale = parseFloat(v) / 100;
    this.state.widgets.forEach(w => { w.x = Math.round(w.x * scale); w.y = Math.round(w.y * scale); w.width = Math.round(w.width * scale); w.height = Math.round(w.height * scale); });
    this.renderWidgetList();
    this.renderWidgetOverlay();
    this.requestPreview(parseFloat(document.getElementById("overlayTimeSlider").value));
  },

  // ── Step 5: 渲染导出 ──────────────────
  initStep5() {
    if (this.state.videoInfo) {
      document.getElementById("outputPath").value = this.state.videoInfo.file_path.replace(/\.[^.]+$/, "_overlay.mp4");
      document.getElementById("renderEnd").value = this.formatDuration(this.state.videoInfo.duration);
    }

    // 编码器切换时联动预设选项和输出扩展名
    const codecSelect = document.getElementById("renderCodec");
    const presetSelect = document.getElementById("renderPreset");
    if (codecSelect) {
      codecSelect.addEventListener("change", () => {
        this._updatePresetOptions(codecSelect.value, presetSelect);
        this._updateOutputExtension();
      });
    }

    // overlay-only 复选框联动：显示/隐藏格式选择，切换输出扩展名
    const overlayCb = document.getElementById("renderOverlayOnly");
    const overlayCodec = document.getElementById("renderOverlayCodec");
    if (overlayCb) {
      overlayCb.addEventListener("change", () => {
        overlayCodec.style.display = overlayCb.checked ? "" : "none";
        // 自动调整输出路径扩展名
        const pathInput = document.getElementById("outputPath");
        const path = pathInput.value;
        if (overlayCb.checked) {
          const fmt = overlayCodec.value === "libvpx-vp9" ? ".webm" : ".mov";
          if (!path.toLowerCase().endsWith(fmt)) {
            pathInput.value = path.replace(/\.[^.]+$/, fmt);
          }
        } else {
          this._updateOutputExtension();
        }
      });
    }
    if (overlayCodec) {
      overlayCodec.addEventListener("change", () => {
        const pathInput = document.getElementById("outputPath");
        const path = pathInput.value;
        const fmt = overlayCodec.value === "libvpx-vp9" ? ".webm" : ".mov";
        if (!path.toLowerCase().endsWith(fmt)) {
          pathInput.value = path.replace(/\.[^.]+$/, fmt);
        }
      });
    }
  },

  /** 根据编码器更新预设选项 */
  _updatePresetOptions(codec, presetSelect) {
    const presets = {
      "libx264": [["ultrafast","ultrafast"],["veryfast","veryfast"],["fast","fast"],["medium","medium"],["slow","slow"]],
      "libx265": [["ultrafast","ultrafast"],["veryfast","veryfast"],["fast","fast"],["medium","medium"],["slow","slow"]],
      "libvpx-vp9": [["0","最佳 (0)"],["1","较好 (1)"],["2","良好 (2)"],["3","中等 (3)"],["4","较快 (4)"],["5","最快 (5)"]],
      "libaom-av1": [["0","最佳 (0)"],["2","较好 (2)"],["4","中等 (4)"],["6","较快 (6)"],["8","最快 (8)"]],
      "h264_nvenc": [["p1","最快 (p1)"],["p2","较快 (p2)"],["p3","中等 (p3)"],["p4","较慢 (p4)"],["p5","良好 (p5)"],["p6","较慢 (p6)"],["p7","最慢 (p7)"]],
      "hevc_nvenc": [["p1","最快 (p1)"],["p2","较快 (p2)"],["p3","中等 (p3)"],["p4","较慢 (p4)"],["p5","良好 (p5)"],["p6","较慢 (p6)"],["p7","最慢 (p7)"]],
      "h264_amf": [["speed","速度"],["balanced","平衡"],["quality","质量"]],
      "hevc_amf": [["speed","速度"],["balanced","平衡"],["quality","质量"]],
    };
    const opts = presets[codec] || presets["libx264"];
    presetSelect.innerHTML = "";
    opts.forEach(([val, label]) => {
      const o = document.createElement("option");
      o.value = val;
      o.textContent = label;
      presetSelect.appendChild(o);
    });
    // 选中接近中等质量的默认值
    const midIdx = Math.floor(opts.length / 2);
    presetSelect.selectedIndex = midIdx;
  },

  /** 根据编码器和 overlay-only 状态更新输出扩展名 */
  _updateOutputExtension() {
    const overlayOnly = document.getElementById("renderOverlayOnly")?.checked || false;
    if (overlayOnly) return; // overlay-only 由 overlayCodec 决定
    const codec = document.getElementById("renderCodec")?.value || "libx264";
    const pathInput = document.getElementById("outputPath");
    const path = pathInput.value;
    let ext = ".mp4";
    if (codec === "libvpx-vp9") ext = ".webm";
    if (!path.toLowerCase().endsWith(ext)) {
      pathInput.value = path.replace(/\.[^.]+$/, ext);
    }
  },

  async startRender() {
    if (!this.state.fitId) { this.toast("请先加载 FIT 文件", "error"); return; }
    if (!this.state.videoId) { this.toast("请先加载视频文件", "error"); return; }

    const btn = document.getElementById("startRenderBtn");
    btn.disabled = true;
    btn.textContent = "渲染中...";

    const session = this.state.fitData?.sessions?.[0];
    const videoInfo = this.state.videoInfo;
    let startSec = 0, endSec = videoInfo?.duration || 0;
    const rangeMode = document.querySelector('input[name="renderRange"]:checked')?.value || "all";
    if (rangeMode === "custom") {
      startSec = this.parseTimeStr(document.getElementById("renderStart").value);
      endSec = this.parseTimeStr(document.getElementById("renderEnd").value);
    }

    try {
      const overlayOnly = document.getElementById("renderOverlayOnly")?.checked || false;
      const overlayCodec = document.getElementById("renderOverlayCodec")?.value || "qtrle";
      const data = await API.startRender({
        project: {
          fit_path: this.state.fitId, video_path: this.state.videoId,
          widgets: this.state.widgets,
          time_sync: this.getTimeSync(),
          render_settings: {
            output_path: document.getElementById("outputPath").value,
            codec: overlayOnly ? overlayCodec : document.getElementById("renderCodec").value,
            preset: document.getElementById("renderPreset").value,
            crf: parseInt(document.getElementById("renderCrf").value),
            audio: overlayOnly ? "none" : document.querySelector('input[name="renderAudio"]:checked').value,
            overlay_only: overlayOnly,
            hwaccel_decode: document.getElementById("renderHwaccelDecode")?.checked || false,
            width: videoInfo?.width || 1920, height: videoInfo?.height || 1080,
            fps: parseFloat(document.getElementById("videoFpsOverride").value) || videoInfo?.fps || 29.97,
            start_sec: startSec, end_sec: endSec,
          },
        },
      });
      this.state.renderTaskId = data.task_id;
      document.getElementById("renderProgress").style.display = "";
      // 默认展开日志面板
      document.getElementById("renderLogContainer").style.display = "";
      document.getElementById("renderLogToggle").textContent = "▼";
      // 隐藏命令回显（等后端推送命令后再显示）
      document.getElementById("renderCmdContainer").style.display = "none";
      this._renderStartTime = Date.now();
      this.pollRenderStatus();
      this._startLogPolling();
    } catch (e) {
      this.toast(`渲染启动失败: ${e.message}`, "error");
      btn.disabled = false;
      btn.textContent = "▶ 开始渲染";
    }
  },

  async pollRenderStatus() {
    if (!this.state.renderTaskId) return;
    try {
      const data = await API.getRenderStatus(this.state.renderTaskId);
      const pct = data.progress || 0;
      document.getElementById("progressFill").style.width = pct + "%";
      document.getElementById("progressPercent").textContent = pct.toFixed(1) + "%";
      document.getElementById("progressFrames").textContent = `${data.current_frame || 0}/${data.total_frames || 0} 帧`;

      // 帧生成速度 & 编码速度
      const overlayFps = data.overlay_fps || 0;
      const encodeFps = data.encode_fps || 0;
      document.getElementById("progressOverlaySpeed").textContent = `🎨 ${overlayFps.toFixed(1)} fps`;
      document.getElementById("progressEncodeSpeed").textContent = `🎬 ${encodeFps.toFixed(1)} fps`;

      // 详细信息行
      const detail = document.getElementById("progressDetail");
      if (detail) {
        const elapsed = data.elapsed_sec || 0;
        const phase = data.phase || "rendering";
        const phaseLabel = { rendering: "渲染中", encoding: "编码音频", done: "完成", error: "出错", cancelled: "已取消" }[phase] || phase;
        let detailHtml = `<span>⏱ 已用时 ${this.formatDuration(elapsed)}</span>`;
        detailHtml += ` <span>| ${phaseLabel}</span>`;
        if (overlayFps > 0 && encodeFps > 0 && overlayFps < encodeFps) {
          detailHtml += ` <span style="color:var(--warning)">⚠ 帧生成速度低于编码速度（瓶颈: overlay 渲染）</span>`;
        }
        detail.innerHTML = detailHtml;
      }

      // 预计剩余
      if (data.eta_sec && data.eta_sec > 0) {
        const min = Math.floor(data.eta_sec / 60);
        const sec = Math.floor(data.eta_sec % 60);
        document.getElementById("progressEta").textContent = `预计剩余: ${min}:${String(sec).padStart(2, "0")}`;
      } else if (data.current_frame > 0 && this._renderStartTime) {
        const elapsedSec = (Date.now() - this._renderStartTime) / 1000;
        const rate = data.current_frame / elapsedSec;
        if (rate > 0) {
          const remaining = (data.total_frames - data.current_frame) / rate;
          const min = Math.floor(remaining / 60);
          const sec = Math.floor(remaining % 60);
          document.getElementById("progressEta").textContent = `预计剩余: ${min}:${String(sec).padStart(2, "0")}`;
        }
      }

      if (data.status === "completed") {
        this.toast("渲染完成！", "success");
        document.getElementById("startRenderBtn").disabled = false;
        document.getElementById("startRenderBtn").textContent = "▶ 开始渲染";
        this._stopLogPolling();
        this._fetchRenderLogs(); // 获取最终日志
        return;
      } else if (data.status === "cancelled" || data.status === "error") {
        this.toast(data.status === "error" ? `渲染出错: ${data.error || ''}` : "渲染已取消", data.status === "error" ? "error" : "info");
        document.getElementById("startRenderBtn").disabled = false;
        document.getElementById("startRenderBtn").textContent = "▶ 开始渲染";
        this._stopLogPolling();
        this._fetchRenderLogs(); // 获取最终日志
        return;
      }
      setTimeout(() => this.pollRenderStatus(), 1000);
    } catch (e) { console.error("轮询失败:", e); }
  },

  // ── 渲染日志 ──────────────────────────
  _logPollTimer: null,
  _logPollIndex: 0,
  _logLineCount: 0,

  _startLogPolling() {
    this._logPollIndex = 0;
    this._logLineCount = 0;
    const logPanel = document.getElementById("renderLogPanel");
    if (logPanel) logPanel.innerHTML = "";
    this._updateLogCount();
    this._pollLogs();
  },

  _stopLogPolling() {
    if (this._logPollTimer) {
      clearTimeout(this._logPollTimer);
      this._logPollTimer = null;
    }
  },

  async _pollLogs() {
    if (!this.state.renderTaskId) return;
    try {
      await this._fetchRenderLogs();
    } catch (e) { /* ignore */ }
    if (this.state.renderTaskId) {
      this._logPollTimer = setTimeout(() => this._pollLogs(), 300);
    }
  },

  async _fetchRenderLogs() {
    if (!this.state.renderTaskId) return;
    try {
      const data = await API.getRenderLogs(this.state.renderTaskId, this._logPollIndex);
      if (data.logs && data.logs.length > 0) {
        const logPanel = document.getElementById("renderLogPanel");
        if (logPanel) {
          const autoScroll = document.getElementById("renderLogAutoScroll")?.checked ?? true;
          const wasAtBottom = autoScroll || (logPanel.scrollTop + logPanel.clientHeight >= logPanel.scrollHeight - 20);

          for (const log of data.logs) {
            const line = document.createElement("div");
            const level = log.level || 'info';
            line.className = `render-log-line render-log-${level}`;
            const msg = log.msg || '';
            const displayMsg = msg.length > 300 ? msg.substring(0, 300) + '…' : msg;
            line.textContent = `[${log.time || ''}] ${displayMsg}`;

            // 检查是否为 ffmpeg 命令行（debug 级别，以 "命令:" 开头）
            if (level === 'debug' && msg.includes('命令:')) {
              line.classList.add('render-log-cmd');
              // 同时更新命令回显区域
              const cmdBody = document.getElementById("renderCmdBody");
              const cmdContainer = document.getElementById("renderCmdContainer");
              if (cmdBody && cmdContainer) {
                // 提取命令部分
                const cmdText = msg.replace(/^.*命令:\s*/, '');
                cmdBody.textContent = cmdText;
                cmdContainer.style.display = "";
              }
            }

            logPanel.appendChild(line);
            this._logLineCount++;
          }

          // 自动滚动到底部
          if (wasAtBottom) {
            logPanel.scrollTop = logPanel.scrollHeight;
          }
        }
        this._logPollIndex = data.total;
        this._updateLogCount();
      }
    } catch (e) { /* ignore */ }
  },

  _updateLogCount() {
    const el = document.getElementById("renderLogCount");
    if (el) el.textContent = `${this._logLineCount} 条`;
  },

  _toggleRenderLog() {
    const container = document.getElementById("renderLogContainer");
    const toggle = document.getElementById("renderLogToggle");
    if (container && toggle) {
      const isHidden = container.style.display === "none";
      container.style.display = isHidden ? "" : "none";
      toggle.textContent = isHidden ? "▼" : "▶";
    }
  },

  _toggleRenderCmd() {
    const body = document.getElementById("renderCmdBody");
    const toggle = document.getElementById("renderCmdToggle");
    if (body && toggle) {
      const isHidden = body.style.display === "none";
      body.style.display = isHidden ? "" : "none";
      toggle.textContent = isHidden ? "▼" : "▶";
    }
  },

  _clearRenderLog() {
    const logPanel = document.getElementById("renderLogPanel");
    if (logPanel) logPanel.innerHTML = "";
    this._logLineCount = 0;
    this._updateLogCount();
  },

  _copyRenderLog() {
    const logPanel = document.getElementById("renderLogPanel");
    if (!logPanel) return;
    const lines = Array.from(logPanel.querySelectorAll('.render-log-line')).map(el => el.textContent);
    const text = lines.join('\n');
    navigator.clipboard.writeText(text).then(() => {
      this.toast("日志已复制到剪贴板", "info");
    }).catch(() => {
      // 降级方案
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      this.toast("日志已复制到剪贴板", "info");
    });
  },

  async cancelRender() {
    if (!this.state.renderTaskId) return;
    try { await API.cancelRender(this.state.renderTaskId); this.toast("渲染已取消", "info"); }
    catch (e) { console.error(e); }
  },

  parseTimeStr(str) {
    const parts = str.split(":").map(Number);
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return parseFloat(str) || 0;
  },

  // ── 项目管理 ──────────────────────────
  async newProject() {
    const name = prompt("项目名称:", "新项目");
    if (!name) return;
    try {
      const data = await API.createProject({ name, fit_path: this.state.fitId || "", video_path: this.state.videoId || "" });
      this.state.projectId = data.id;
      document.getElementById("projectName").textContent = name;
      this.toast("项目已创建", "success");
    } catch (e) { this.toast(`创建失败: ${e.message}`, "error"); }
  },

  /** 收集当前完整项目配置 */
  _collectProjectData() {
    // 收集 sanitize config
    let sanitizeConfig = null;
    const sanGpsGlitch = document.getElementById("sanGpsGlitch");
    if (sanGpsGlitch) {
      sanitizeConfig = {
        gps_filter_glitches: sanGpsGlitch.checked,
        gps_out_of_range: document.getElementById("sanGpsRange")?.checked ?? true,
        gps_max_speed_ms: parseFloat(document.getElementById("sanGpsMaxSpeed")?.value) || 55,
        hr_range: [parseInt(document.getElementById("sanHrMin")?.value) || 30,
                   parseInt(document.getElementById("sanHrMax")?.value) || 250],
        hr_enable_rate_check: document.getElementById("sanHrRate")?.checked ?? true,
        hr_max_rate: parseFloat(document.getElementById("sanHrMaxRate")?.value) || 30,
        speed_range: [parseFloat(document.getElementById("sanSpeedMin")?.value) || 0,
                      parseFloat(document.getElementById("sanSpeedMax")?.value) || 55],
        speed_enable_accel_check: document.getElementById("sanSpeedAccel")?.checked ?? true,
        speed_max_accel: parseFloat(document.getElementById("sanSpeedMaxAccel")?.value) || 10,
        altitude_range: [parseFloat(document.getElementById("sanAltMin")?.value) || -500,
                         parseFloat(document.getElementById("sanAltMax")?.value) || 9000],
        cadence_range: [0, 250],
        power_range: [0, 2500],
        temperature_range: [-40, 60],
      };
    }

    // 收集 smoothing config
    let smoothingConfig = null;
    const filterMethod = document.getElementById("filterMethod");
    if (filterMethod) {
      const fields = this.getCheckedFilterFields();
      smoothingConfig = {
        enabled: fields.length > 0,
        fields,
        method: filterMethod.value,
        window_size: parseInt(document.getElementById("filterWindow")?.value) || 5,
      };
    }

    // 收集 render settings
    const renderSettings = {
      output_path: document.getElementById("outputPath")?.value || "",
      codec: document.getElementById("renderCodec")?.value || "libx264",
      preset: document.getElementById("renderPreset")?.value || "fast",
      crf: parseInt(document.getElementById("renderCrf")?.value) || 23,
      audio: document.querySelector('input[name="renderAudio"]:checked')?.value || "none",
      overlay_only: document.getElementById("renderOverlayOnly")?.checked || false,
      overlay_codec: document.getElementById("renderOverlayCodec")?.value || "qtrle",
      hwaccel_decode: document.getElementById("renderHwaccelDecode")?.checked || false,
    };

    return {
      fit_path: this.state.fitId || "",
      video_path: this.state.videoId || "",
      overlay_template_name: this.state.templateName,
      widgets: this.state.widgets,
      time_sync: this.getTimeSync(),
      render_settings: renderSettings,
      sanitize_config: sanitizeConfig,
      smoothing_config: smoothingConfig,
    };
  },

  async saveProject() {
    if (!this.state.projectId) { await this.newProject(); return; }
    try {
      await API.updateProject(this.state.projectId, this._collectProjectData());
      this.toast("项目已保存", "success");
    } catch (e) { this.toast(`保存失败: ${e.message}`, "error"); }
  },

  async openProject() {
    try {
      const projects = await API.listProjects();
      if (!projects.length) {
        this.toast("暂无已保存的项目", "info");
        return;
      }
      this._showProjectList(projects);
    } catch (e) { this.toast(`获取项目列表失败: ${e.message}`, "error"); }
  },

  _showProjectList(projects) {
    let html = '<div class="project-list">';
    for (const p of projects) {
      const date = p.created_at ? new Date(p.created_at).toLocaleString("zh-CN") : "";
      html += `<div class="project-item" data-id="${p.id}">
        <div class="project-item-info">
          <div class="project-item-name">${p.name || "未命名"}</div>
          <div class="project-item-meta">${date} · ${p.widget_count || 0} 个组件</div>
          <div class="project-item-paths">${p.fit_path ? "✅ FIT" : "❌ FIT"} ${p.video_path ? "✅ 视频" : "❌ 视频"}</div>
        </div>
        <div class="project-item-actions">
          <button onclick="App.loadProject('${p.id}')" class="btn-primary btn-sm">打开</button>
          <button onclick="App.deleteProject('${p.id}')" class="btn-secondary btn-sm" title="删除">🗑️</button>
        </div>
      </div>`;
    }
    html += '</div>';
    document.getElementById("projectListContent").innerHTML = html;
    document.getElementById("projectListModal").style.display = "";
  },

  async loadProject(projectId) {
    try {
      const data = await API.getProject(projectId);
      document.getElementById("projectListModal").style.display = "none";

      // 还原 state
      this.state.projectId = data.id;
      this.state.widgets = data.widgets || [];
      this.state.templateName = data.overlay_template_name || "";
      document.getElementById("projectName").textContent = data.name || "未命名项目";

      // 还原 FIT
      if (data.fit_path) {
        document.getElementById("fitPathInput").value = data.fit_path;
        await this.loadFit();
      }

      // 还原视频
      if (data.video_path) {
        document.getElementById("videoPathInput").value = data.video_path;
        await this.loadVideo();
      }

      // 还原 time_sync
      if (data.video_config?.time_sync) {
        const ts = data.video_config.time_sync;
        if (ts.time_scale) {
          const scale = ts.time_scale;
          const sel = document.getElementById("timeScaleSelect");
          if (scale === 1) sel.value = "1";
          else if (scale === 30) sel.value = "30";
          else { sel.value = "custom"; document.getElementById("customTimeScaleLabel").style.display = ""; }
          if (sel.value === "custom") {
            document.getElementById("customTimeScale").value = scale;
          }
        }
        if (ts.video_start_time) {
          const dt = new Date(ts.video_start_time);
          const local = new Date(dt.getTime() - dt.getTimezoneOffset() * 60000);
          document.getElementById("syncManualStart")?.setAttribute("value", local.toISOString().slice(0, 16));
        }
      }

      // 还原 sanitize config
      if (data.sanitize_config) {
        const sc = data.sanitize_config;
        this._setCheckbox("sanGpsGlitch", sc.gps_filter_glitches);
        this._setCheckbox("sanGpsRange", sc.gps_out_of_range);
        this._setInput("sanGpsMaxSpeed", sc.gps_max_speed_ms);
        if (sc.hr_range) { this._setInput("sanHrMin", sc.hr_range[0]); this._setInput("sanHrMax", sc.hr_range[1]); }
        this._setCheckbox("sanHrRate", sc.hr_enable_rate_check);
        this._setInput("sanHrMaxRate", sc.hr_max_rate);
        if (sc.speed_range) { this._setInput("sanSpeedMin", sc.speed_range[0]); this._setInput("sanSpeedMax", sc.speed_range[1]); }
        this._setCheckbox("sanSpeedAccel", sc.speed_enable_accel_check);
        this._setInput("sanSpeedMaxAccel", sc.speed_max_accel);
        if (sc.altitude_range) { this._setInput("sanAltMin", sc.altitude_range[0]); this._setInput("sanAltMax", sc.altitude_range[1]); }
      }

      // 还原 smoothing config
      if (data.smoothing_config) {
        const sm = data.smoothing_config;
        this._setInput("filterMethod", sm.method);
        this._setInput("filterWindow", sm.window_size);
        // 还原勾选的平滑字段
        if (sm.fields) {
          document.querySelectorAll("#filterFields input[type=checkbox]").forEach(cb => {
            cb.checked = sm.fields.includes(cb.value);
          });
        }
      }

      // 还原 render settings
      if (data.render_settings) {
        const rs = data.render_settings;
        this._setInput("outputPath", rs.output_path);
        this._setInput("renderCodec", rs.codec);
        this._setInput("renderPreset", rs.preset);
        this._setInput("renderCrf", rs.crf);
        if (rs.audio) {
          const radio = document.querySelector(`input[name="renderAudio"][value="${rs.audio}"]`);
          if (radio) radio.checked = true;
        }
        this._setCheckbox("renderOverlayOnly", rs.overlay_only);
        if (rs.overlay_codec) {
          this._setInput("renderOverlayCodec", rs.overlay_codec);
        }
        // 联动显示 overlay codec 选择
        const overlayCb = document.getElementById("renderOverlayOnly");
        const overlayCodec = document.getElementById("renderOverlayCodec");
        if (overlayCb && overlayCodec) {
          overlayCodec.style.display = overlayCb.checked ? "" : "none";
        }
      }

      // 跳到叠加设计步骤显示 widget
      this.goStep(4);

      this.toast(`项目「${data.name}」已加载`, "success");
    } catch (e) { this.toast(`加载失败: ${e.message}`, "error"); }
  },

  async deleteProject(projectId) {
    if (!confirm("确定删除此项目？此操作不可恢复。")) return;
    try {
      await API.deleteProject(projectId);
      // 刷新列表
      const projects = await API.listProjects();
      if (projects.length) {
        this._showProjectList(projects);
      } else {
        document.getElementById("projectListModal").style.display = "none";
        this.toast("项目已删除", "success");
      }
      if (this.state.projectId === projectId) {
        this.state.projectId = null;
        document.getElementById("projectName").textContent = "未命名项目";
      }
      this.toast("项目已删除", "success");
    } catch (e) { this.toast(`删除失败: ${e.message}`, "error"); }
  },

  _setCheckbox(id, value) {
    const el = document.getElementById(id);
    if (el) el.checked = value;
  },

  _setInput(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
  },

  // ── 文件浏览 ──────────────────────────
  _fbState: { targetInput: null, currentPath: "", parentPath: "", selectedItem: null, selectedPath: "", exts: "" },

  browseFitFile() {
    this._fbState.targetInput = "fit";
    this._fbState.exts = ".fit";
    this._fbState.selectedItem = null;
    this._fbState.selectedPath = "";
    document.getElementById("fileBrowseTitle").textContent = "浏览 FIT 文件";
    const currentPath = document.getElementById("fitPathInput").value.trim();
    const initialDir = currentPath ? currentPath.replace(/[^\\\/]+$/, "") : "D:\\";
    this.fbNavigate(initialDir);
    document.getElementById("fileBrowseModal").style.display = "";
  },

  browseVideoFile() {
    this._fbState.targetInput = "video";
    this._fbState.exts = ".mp4,.mov,.avi,.mkv,.m4v";
    this._fbState.selectedItem = null;
    this._fbState.selectedPath = "";
    document.getElementById("fileBrowseTitle").textContent = "浏览视频文件";
    const currentPath = document.getElementById("videoPathInput").value.trim();
    const initialDir = currentPath ? currentPath.replace(/[^\\\/]+$/, "") : "D:\\";
    this.fbNavigate(initialDir);
    document.getElementById("fileBrowseModal").style.display = "";
  },

  async fbNavigate(path) {
    this._fbState.currentPath = path;
    this._fbState.selectedItem = null;
    this._fbState.selectedPath = "";
    document.getElementById("fbPathBar").value = path;
    const content = document.getElementById("fbContent");
    content.innerHTML = '<div class="fb-loading">加载中...</div>';
    try {
      const data = await API.browseDirectory(path, this._fbState.exts);
      this._fbState.parentPath = data.parent || "";
      this._fbState.currentPath = data.path;
      document.getElementById("fbPathBar").value = data.path;
      let html = "";
      if (data.parent !== undefined) {
        html += `<div class="fb-item" ondblclick="App.fbNavigate('${this._escapePath(data.parent)}')" onclick="App.fbSelectDir('${this._escapePath(data.parent)}')"><span class="fb-icon">⬆</span><span class="fb-name">..</span></div>`;
      }
      for (const d of data.dirs) {
        html += `<div class="fb-item" ondblclick="App.fbNavigate('${this._escapePath(d.path)}')" onclick="App.fbSelectDir('${this._escapePath(d.path)}')"><span class="fb-icon">📁</span><span class="fb-name">${this._escapeHtml(d.name)}</span></div>`;
      }
      for (const f of data.files) {
        const icon = this._fbFileIcon(f.ext);
        html += `<div class="fb-item" onclick="App.fbSelectFile(this, '${this._escapePath(f.path)}')"><span class="fb-icon">${icon}</span><span class="fb-name">${this._escapeHtml(f.name)}</span><span class="fb-size">${this._formatFileSize(f.size)}</span><span class="fb-ext">${f.ext}</span></div>`;
      }
      if (!data.dirs.length && !data.files.length) html = '<div class="fb-empty">📂 空目录</div>';
      content.innerHTML = html;
    } catch (e) {
      content.innerHTML = `<div class="fb-empty">❌ ${this._escapeHtml(e.message)}</div>`;
    }
  },

  fbSelectDir(path) { this._fbState.selectedItem = null; this._fbState.selectedPath = path; document.querySelectorAll(".fb-item.selected").forEach(el => el.classList.remove("selected")); },
  fbSelectFile(el, path) { document.querySelectorAll(".fb-item.selected").forEach(item => item.classList.remove("selected")); el.classList.add("selected"); this._fbState.selectedItem = el; this._fbState.selectedPath = path; },
  fbGoUp() { if (this._fbState.parentPath) this.fbNavigate(this._fbState.parentPath); },
  fbRefresh() { if (this._fbState.currentPath) this.fbNavigate(this._fbState.currentPath); },

  fbSelect() {
    const path = this._fbState.selectedPath;
    if (!path) { this.toast("请先选择文件或目录", "error"); return; }
    if (this._fbState.targetInput === "fit") {
      if (!path.toLowerCase().endsWith(".fit")) { this.fbNavigate(path); return; }
      document.getElementById("fitPathInput").value = path;
    } else if (this._fbState.targetInput === "video") {
      const videoExts = [".mp4", ".mov", ".avi", ".mkv", ".m4v"];
      if (!videoExts.some(e => path.toLowerCase().endsWith(e))) { this.fbNavigate(path); return; }
      document.getElementById("videoPathInput").value = path;
    }
    this.closeFileBrowse();
  },

  closeFileBrowse() { document.getElementById("fileBrowseModal").style.display = "none"; },
  _escapePath(path) { return path.replace(/\\/g, "\\\\").replace(/'/g, "\\'"); },
  _escapeHtml(str) { const div = document.createElement("div"); div.textContent = str; return div.innerHTML; },
  _fbFileIcon(ext) { const icons = { ".fit": "🏃", ".mp4": "🎬", ".mov": "🎬", ".avi": "🎬", ".mkv": "🎬", ".m4v": "🎬", ".jpg": "🖼️", ".png": "🖼️" }; return icons[ext.toLowerCase()] || "📄"; },
  _formatFileSize(bytes) { if (bytes < 1024) return bytes + " B"; if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB"; if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB"; return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB"; },

  // ── 工具方法 ──────────────────────────
  toast(msg, type = "info") {
    const container = document.getElementById("toastContainer");
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3000);
  },

  formatDuration(sec) {
    if (!sec) return "--";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
    return `${m}:${String(s).padStart(2,"0")}`;
  },

  formatSeconds(sec) {
    if (sec == null || isNaN(sec)) return "--";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    const ms = Math.round((sec % 1) * 1000);
    return `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}.${String(ms).padStart(3,"0")}`;
  },

  formatDateTime(iso) {
    try { const d = new Date(iso); return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
    catch { return iso; }
  },

  sportName(sport) {
    const map = { cycling: "🚴 骑行", running: "🏃 跑步", walking: "🚶 步行", hiking: "🥾 徒步", swimming: "🏊 游泳" };
    return map[sport] || `🏅 ${sport || "运动"}`;
  },

  widgetIcon(type) {
    const map = { MapTrack: "🗺️", SpeedGauge: "💨", HeartRateGauge: "❤️", CadenceGauge: "🔄",
      PowerGauge: "⚡", AltitudeChart: "📈", ElevationGauge: "⛰️", DistanceCounter: "📏",
      TimerDisplay: "⏱️", GradientIndicator: "📐", CustomLabel: "🏷️" };
    return map[type] || "📦";
  },

  widgetLabel(type) {
    const map = { MapTrack: "轨迹地图", SpeedGauge: "速度表", HeartRateGauge: "心率表", CadenceGauge: "踏频表",
      PowerGauge: "功率表", AltitudeChart: "海拔图", ElevationGauge: "海拔", DistanceCounter: "距离",
      TimerDisplay: "时间", GradientIndicator: "坡度", CustomLabel: "标签" };
    return map[type] || type;
  },
};

document.addEventListener("DOMContentLoaded", () => App.init());
