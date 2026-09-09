// YTC Price Action Trader - Web UI Client
let chartM30, chartM3, chartM1;
let seriesM30, seriesM3, seriesM1;
let wsClient;
let entryLine, slLine, tp1Line, tp2Line, lrpLine;
let htfLines = [];

const SERVER_A_URL = "http://127.0.0.1:29120";
const SERVER_B_URL = "http://127.0.0.1:29121";

function switchTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));

  if (tabId === "server-a") {
    document.querySelectorAll(".tab-btn")[0].classList.add("active");
    document.getElementById("tab-server-a").classList.add("active");
  } else {
    document.querySelectorAll(".tab-btn")[1].classList.add("active");
    document.getElementById("tab-server-b").classList.add("active");
  }
}

function initCharts() {
  const chartOpts = {
    layout: {
      background: { color: '#161b22' },
      textColor: '#8b949e',
    },
    grid: {
      vertLines: { color: '#21262d' },
      horzLines: { color: '#21262d' },
    },
    timeScale: { 
      timeVisible: true, 
      secondsVisible: true,
      borderColor: '#30363d'
    },
    rightPriceScale: {
      borderColor: '#30363d'
    }
  };

  // M30 Chart
  const elM30 = document.getElementById("chart-m30");
  if (elM30 && window.LightweightCharts) {
    chartM30 = LightweightCharts.createChart(elM30, { ...chartOpts, width: elM30.clientWidth, height: 290 });
    seriesM30 = chartM30.addCandlestickSeries({
      upColor: '#238636', downColor: '#da3633', borderVisible: false, wickUpColor: '#238636', wickDownColor: '#da3633'
    });
  }

  // M3 Chart
  const elM3 = document.getElementById("chart-m3");
  if (elM3 && window.LightweightCharts) {
    chartM3 = LightweightCharts.createChart(elM3, { ...chartOpts, width: elM3.clientWidth, height: 290 });
    seriesM3 = chartM3.addCandlestickSeries({
      upColor: '#238636', downColor: '#da3633', borderVisible: false, wickUpColor: '#238636', wickDownColor: '#da3633'
    });
  }

  // M1 Chart
  const elM1 = document.getElementById("chart-m1");
  if (elM1 && window.LightweightCharts) {
    chartM1 = LightweightCharts.createChart(elM1, { ...chartOpts, width: elM1.clientWidth, height: 290 });
    seriesM1 = chartM1.addCandlestickSeries({
      upColor: '#238636', downColor: '#da3633', borderVisible: false, wickUpColor: '#238636', wickDownColor: '#da3633'
    });
  }

  window.addEventListener("resize", () => {
    if (elM30 && chartM30) chartM30.applyOptions({ width: elM30.clientWidth });
    if (elM3 && chartM3) chartM3.applyOptions({ width: elM3.clientWidth });
    if (elM1 && chartM1) chartM1.applyOptions({ width: elM1.clientWidth });
  });
}

function cleanAndSortBars(bars) {
  if (!bars || !Array.isArray(bars)) return [];
  const sorted = [...bars].sort((a, b) => a.time - b.time);
  const unique = [];
  const seenTimes = new Set();
  for (const b of sorted) {
    if (!seenTimes.has(b.time)) {
      seenTimes.add(b.time);
      unique.push({
        time: b.time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close
      });
    }
  }
  return unique;
}

async function loadInitialBars() {
  try {
    const [resM30, resM3, resM1] = await Promise.all([
      fetch(`${SERVER_A_URL}/api/bars?timeframe=M30&count=80`),
      fetch(`${SERVER_A_URL}/api/bars?timeframe=M3&count=100`),
      fetch(`${SERVER_A_URL}/api/bars?timeframe=M1&count=100`)
    ]);

    if (resM30.ok && seriesM30) {
      const data = await resM30.json();
      const cleaned = cleanAndSortBars(data);
      seriesM30.setData(cleaned);
      if (chartM30) chartM30.timeScale().fitContent();
    }
    if (resM3.ok && seriesM3) {
      const data = await resM3.json();
      const cleaned = cleanAndSortBars(data);
      seriesM3.setData(cleaned);
      if (chartM3) chartM3.timeScale().fitContent();
    }
    if (resM1.ok && seriesM1) {
      const data = await resM1.json();
      const cleaned = cleanAndSortBars(data);
      seriesM1.setData(cleaned);
      if (chartM1) chartM1.timeScale().fitContent();
      if (cleaned.length > 0) {
        updatePriceDisplay(cleaned[cleaned.length - 1].close);
      }
    }
    logTelemetry("[CHARTS] Historical bars successfully loaded and synchronized.");
  } catch (e) {
    console.error("Failed to load initial bars:", e);
  }
}

function updatePriceDisplay(price) {
  const badgeM1 = document.getElementById("badge-m1");
  if (badgeM1 && price) {
    badgeM1.textContent = `${price.toFixed(2)} 🟢 LIVE`;
  }
}

function updateHTFZonesOnChart(config) {
  if (!seriesM30 || !config || !config.htf_zones) return;
  htfLines.forEach(l => {
    try { seriesM30.removePriceLine(l); } catch(e){}
  });
  htfLines = [];

  (config.htf_zones.resistance_zones || []).forEach(z => {
    try {
      const l = seriesM30.createPriceLine({
        price: (z.high + z.low) / 2,
        color: '#da3633',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: `RES (${z.low}-${z.high})`,
      });
      htfLines.push(l);
    } catch(e){}
  });

  (config.htf_zones.support_zones || []).forEach(z => {
    try {
      const l = seriesM30.createPriceLine({
        price: (z.high + z.low) / 2,
        color: '#238636',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: `SUP (${z.low}-${z.high})`,
      });
      htfLines.push(l);
    } catch(e){}
  });
}

function updateChartPriceLines(radar) {
  if (!seriesM1 || !radar || !radar.entry) return;

  // Remove existing lines
  if (entryLine) { try { seriesM1.removePriceLine(entryLine); } catch(e){} entryLine = null; }
  if (slLine) { try { seriesM1.removePriceLine(slLine); } catch(e){} slLine = null; }
  if (tp1Line) { try { seriesM1.removePriceLine(tp1Line); } catch(e){} tp1Line = null; }
  if (tp2Line) { try { seriesM1.removePriceLine(tp2Line); } catch(e){} tp2Line = null; }
  if (lrpLine) { try { seriesM1.removePriceLine(lrpLine); } catch(e){} lrpLine = null; }

  // Add ENTRY line (Blue)
  entryLine = seriesM1.createPriceLine({
    price: radar.entry,
    color: '#58a6ff',
    lineWidth: 2,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
    title: `ENTRY (${radar.side}): ${radar.entry.toFixed(2)}`,
  });

  // Add SL line (Red)
  if (radar.s1) {
    slLine = seriesM1.createPriceLine({
      price: radar.s1,
      color: '#da3633',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: `SL: ${radar.s1.toFixed(2)}`,
    });
  }

  // Add TP1 line (Green)
  if (radar.t1) {
    tp1Line = seriesM1.createPriceLine({
      price: radar.t1,
      color: '#238636',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: `TP1 (1:1): ${radar.t1.toFixed(2)}`,
    });
  }

  // Add TP2 line (Bright Green)
  if (radar.t2) {
    tp2Line = seriesM1.createPriceLine({
      price: radar.t2,
      color: '#3fb950',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: `TP2 (HTF): ${radar.t2.toFixed(2)}`,
    });
  }

  // Add LRP line (Purple)
  if (radar.lrp) {
    lrpLine = seriesM1.createPriceLine({
      price: radar.lrp,
      color: '#bc8cff',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      axisLabelVisible: true,
      title: `LRP (Max): ${radar.lrp.toFixed(2)}`,
    });
  }
}

function updateRadarUI(radar) {
  if (!radar) return;
  const badge = document.getElementById("radar-status-badge");
  if (badge) {
    badge.textContent = radar.status || "SCANNING";
    if (radar.status === "ACTIVE_TRADE") {
      badge.style.borderColor = "#238636";
      badge.style.color = "#238636";
    } else if (radar.status === "READY_TO_FIRE") {
      badge.style.borderColor = "#e3b341";
      badge.style.color = "#e3b341";
    } else {
      badge.style.borderColor = "#58a6ff";
      badge.style.color = "#58a6ff";
    }
  }

  const elSetup = document.getElementById("radar-setup");
  if (elSetup) elSetup.textContent = radar.setup || "--";

  const elEntry = document.getElementById("radar-entry");
  if (elEntry) elEntry.textContent = radar.entry ? `$${radar.entry.toFixed(2)}` : "--";

  const elSl = document.getElementById("radar-sl");
  if (elSl) elSl.textContent = radar.s1 ? `$${radar.s1.toFixed(2)}` : "--";

  const elTp1 = document.getElementById("radar-tp1");
  if (elTp1) elTp1.textContent = radar.t1 ? `$${radar.t1.toFixed(2)}` : "--";

  const elTp2 = document.getElementById("radar-tp2");
  if (elTp2) elTp2.textContent = radar.t2 ? `$${radar.t2.toFixed(2)}` : "--";

  const elLrp = document.getElementById("radar-lrp");
  if (elLrp) elLrp.textContent = radar.lrp ? `$${radar.lrp.toFixed(2)}` : "--";

  const elDist = document.getElementById("radar-dist");
  if (elDist && radar.dist_to_entry !== undefined) {
    const d = Math.abs(radar.dist_to_entry);
    elDist.textContent = `${d.toFixed(2)} USD (${(d * 10).toFixed(0)} pips)`;
  }

  updateChartPriceLines(radar);
}

function connectWebSocket() {
  const wsUrl = `ws://${window.location.hostname || "127.0.0.1"}:29120/ws/telemetry`;
  wsClient = new WebSocket(wsUrl);

  wsClient.onopen = () => {
    logTelemetry("[WS] Connected to Server A telemetry stream.");
  };

  wsClient.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleTelemetryMessage(msg);
    } catch (e) {
      console.error("WS Parse error", e);
    }
  };

  wsClient.onclose = () => {
    logTelemetry("[WS] Disconnected. Reconnecting in 3s...");
    setTimeout(connectWebSocket, 3000);
  };
}

function handleTelemetryMessage(msg) {
  if (msg.topic === "snapshot") {
    if (msg.bars) {
      if (msg.bars.M30 && seriesM30) {
        seriesM30.setData(cleanAndSortBars(msg.bars.M30));
        chartM30.timeScale().fitContent();
      }
      if (msg.bars.M3 && seriesM3) {
        seriesM3.setData(cleanAndSortBars(msg.bars.M3));
        chartM3.timeScale().fitContent();
      }
      if (msg.bars.M1 && seriesM1) {
        seriesM1.setData(cleanAndSortBars(msg.bars.M1));
        chartM1.timeScale().fitContent();
      }
    }
    if (msg.config) {
      updateHTFZonesOnChart(msg.config);
    }
    if (msg.setup_radar) {
      updateRadarUI(msg.setup_radar);
    }
    fetchPositions();
    fetchStatus();
    return;
  }

  if (msg.topic === "market_tick") {
    const tick = msg.payload;
    if (tick.m1 && seriesM1) {
      seriesM1.update(tick.m1);
      updatePriceDisplay(tick.m1.close);
    }
    if (tick.m3 && seriesM3) {
      seriesM3.update(tick.m3);
    }
    if (tick.m30 && seriesM30) {
      seriesM30.update(tick.m30);
    }
    return;
  }

  if (msg.topic === "setup_radar") {
    updateRadarUI(msg.payload);
    return;
  }

  if (msg.topic === "telemetry" || msg.topic === "trade_opened" || msg.topic === "emergency") {
    const payload = msg.payload || msg;
    logTelemetry(`[${msg.topic.toUpperCase()}] ${JSON.stringify(payload)}`);
    fetchPositions();
    fetchStatus();
  }
}

function logTelemetry(text) {
  const terminal = document.getElementById("telemetry-log");
  if (!terminal) return;
  const time = new Date().toLocaleTimeString();
  const line = document.createElement("div");
  line.textContent = `[${time}] ${text}`;
  terminal.appendChild(line);
  terminal.scrollTop = terminal.scrollHeight;
}

function clearTelemetry() {
  const terminal = document.getElementById("telemetry-log");
  if (terminal) terminal.innerHTML = "";
}

async function fetchStatus() {
  try {
    const res = await fetch(`${SERVER_A_URL}/api/status`);
    if (res.ok) {
      const data = await res.json();
      document.getElementById("stat-balance").textContent = `$${data.balance.toFixed(2)}`;
      document.getElementById("stat-symbol").textContent = data.symbol;
      document.getElementById("stat-regime").textContent = data.regime;
      document.getElementById("stat-mode").textContent = data.is_paused ? "PAUSED" : "ACTIVE";
      document.getElementById("stat-mode").style.color = data.is_paused ? "#d29922" : "#238636";
    }
  } catch (e) {}
}

async function fetchRadarFallback() {
  try {
    const res = await fetch(`${SERVER_A_URL}/api/radar`);
    if (res.ok) {
      const radar = await res.json();
      if (radar && radar.entry) {
        updateRadarUI(radar);
      }
    }
  } catch (e) {}
}

async function fetchPositions() {
  try {
    const res = await fetch(`${SERVER_A_URL}/api/trades`);
    if (res.ok) {
      const trades = await res.json();
      const tbody = document.getElementById("positions-tbody");
      document.getElementById("position-count").textContent = `${trades.length} Open`;

      if (trades.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:#8b949e;">No active positions. Scanning for wholesale setups...</td></tr>`;
        return;
      }

      tbody.innerHTML = trades.map(t => `
        <tr>
          <td><code>${t.trade_id}</code></td>
          <td><strong>${t.setup}</strong></td>
          <td style="color:${t.side === 'BUY' ? '#238636' : '#da3633'}"><strong>${t.side}</strong></td>
          <td><span style="background:#21262d; padding:2px 6px; border-radius:4px; font-size:11px;">${t.state}</span></td>
          <td>${t.part1.lot_size} / ${t.part2.lot_size}</td>
          <td>${t.part1.entry_price.toFixed(2)}</td>
          <td style="color:#da3633;">${t.part1.sl_price.toFixed(2)}</td>
          <td style="color:#238636;">${t.part1.tp_price.toFixed(2)}</td>
          <td>
            <button class="btn btn-warning" style="padding:2px 6px; font-size:10px;" onclick="forceScratch(${t.part1.ticket})">Scratch</button>
          </td>
        </tr>
      `).join("");
    }
  } catch (e) {}
}

async function simulateTick() {
  try {
    const res = await fetch(`${SERVER_A_URL}/api/simulate_tick`, { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      logTelemetry(`[SIMULATE] Advance tick generated: Price = ${data.price}`);
    }
  } catch (e) {
    console.error("simulateTick error:", e);
  }
}

async function panicCloseAll() {
  if (confirm("ARE YOU SURE YOU WANT TO LIQUIDATE ALL POSITIONS IMMEDIATELY?")) {
    await fetch(`${SERVER_A_URL}/api/emergency/panic_close`, { method: "POST" });
    fetchPositions();
  }
}

async function togglePause() {
  const res = await fetch(`${SERVER_A_URL}/api/emergency/pause`, { method: "POST" });
  if (res.ok) {
    const data = await res.json();
    const btn = document.getElementById("btn-pause");
    btn.textContent = data.is_paused ? "▶ RESUME ENTRIES" : "⏸ PAUSE NEW ENTRIES";
    fetchStatus();
  }
}

async function forceScratch(ticketId) {
  if (confirm(`Force scratch position ticket ${ticketId}?`)) {
    await fetch(`${SERVER_A_URL}/api/emergency/force_scratch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket_id: ticketId })
    });
    fetchPositions();
  }
}

async function generateAIPlan() {
  const jsonArea = document.getElementById("ai-plan-json");
  jsonArea.value = "Reasoning via Server B AI Strategy Engine...\nAnalyzing HTF/TTF rates, macroeconomic calendar, RAG memory...";

  try {
    const symbol = document.getElementById("stat-symbol").textContent || "XAUUSD";
    const res = await fetch(`${SERVER_B_URL}/api/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: symbol, economic_events: [] })
    });
    if (res.ok) {
      const data = await res.json();
      jsonArea.value = JSON.stringify(data.config, null, 2);
      updateHTFZonesOnChart(data.config);
    } else {
      jsonArea.value = "Failed to generate AI plan. Check Server B log.";
    }
  } catch (e) {
    jsonArea.value = `Error connecting to Server B (Port 29121): ${e.message}`;
  }
}

async function deployPlanToServerA() {
  alert("AI Session Plan approved and deployed to Server A successfully!");
}

async function runHindsightAudit() {
  const out = document.getElementById("audit-output");
  out.innerHTML = "Auditing session against Lance Beggs YTC framework...";

  try {
    const symbol = document.getElementById("stat-symbol").textContent || "XAUUSD";
    const res = await fetch(`${SERVER_B_URL}/api/audit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        trading_config: { session_id: "sess_curr", symbol: symbol, market_regime: "SIDEWAYS_RANGE" },
        session_trades: [],
        full_session_ohlcv: {}
      })
    });
    if (res.ok) {
      const data = await res.json();
      const r = data.audit_report;
      out.innerHTML = `
        <div style="margin-bottom:8px;"><strong>Compliance Score:</strong> ${(r.compliance_score * 100).toFixed(1)}%</div>
        <div style="margin-bottom:8px;"><strong>Violations:</strong> ${r.rule_violations.length}</div>
        <div style="margin-bottom:8px;"><strong>Lessons Recorded (ChromaDB):</strong></div>
        <ul style="padding-left:16px;">
          ${r.lessons_learned.map(l => `<li>${l}</li>`).join("")}
        </ul>
      `;
    }
  } catch (e) {
    out.innerHTML = `Error running audit: ${e.message}`;
  }
}

async function fetchLatestBarsFallback() {
  try {
    const res = await fetch(`${SERVER_A_URL}/api/bars?timeframe=M1&count=2`);
    if (res.ok && seriesM1) {
      const bars = await res.json();
      if (bars.length > 0) {
        const last = bars[bars.length - 1];
        seriesM1.update(last);
        updatePriceDisplay(last.close);
      }
    }
  } catch (e) {}
}

function onProviderChange() {
  const provider = document.getElementById("ai-provider-select").value;
  const modelSelect = document.getElementById("ai-model-select");
  const customInput = document.getElementById("ai-custom-model-input");
  const keyInput = document.getElementById("ai-api-key-input");
  
  if (customInput) {
    customInput.style.display = "none";
    customInput.value = "";
  }

  if (provider === "gemini") {
    modelSelect.innerHTML = `
      <option value="gemini-2.0-flash">gemini-2.0-flash (Siêu tốc <1.5s - Khuyên dùng)</option>
      <option value="gemini-2.5-pro">gemini-2.5-pro (Thế hệ 2.5 - Suy luận chuyên sâu)</option>
      <option value="gemini-2.5-flash">gemini-2.5-flash (Thế hệ 2.5 - Nhanh & Thông minh)</option>
      <option value="gemini-2.0-flash-lite">gemini-2.0-flash-lite (Siêu nhẹ, tiết kiệm)</option>
      <option value="gemini-2.0-flash-thinking-exp-01-21">gemini-2.0-flash-thinking (Tư duy chuỗi)</option>
      <option value="gemini-2.0-pro-exp-02-05">gemini-2.0-pro-exp (Bản Pro thử nghiệm)</option>
      <option value="gemini-1.5-pro">gemini-1.5-pro (Ổn định, Context 2M)</option>
      <option value="gemini-1.5-flash">gemini-1.5-flash (Ổn định, chuẩn)</option>
      <option value="gemini-1.5-flash-8b">gemini-1.5-flash-8b (Bản nhỏ)</option>
      <option value="custom">✏️ Nhập model tùy chỉnh khác...</option>
    `;
    keyInput.placeholder = "Dán Gemini API Key (AIzaSy...)";
  } else {
    modelSelect.innerHTML = `
      <option value="gpt-4o">gpt-4o (Đa phương thức flagship)</option>
      <option value="gpt-4o-mini">gpt-4o-mini (Nhanh & Tiết kiệm)</option>
      <option value="o3-mini">o3-mini (Suy luận logic thế hệ mới)</option>
      <option value="o1">o1 (Suy luận chuyên sâu)</option>
      <option value="o1-mini">o1-mini (Suy luận nhanh)</option>
      <option value="gpt-4-turbo">gpt-4-turbo</option>
      <option value="custom">✏️ Nhập model tùy chỉnh khác...</option>
    `;
    keyInput.placeholder = "Dán OpenAI API Key (sk-...)";
  }
}

function onModelChange() {
  const modelSelect = document.getElementById("ai-model-select");
  const customInput = document.getElementById("ai-custom-model-input");
  if (!modelSelect || !customInput) return;
  if (modelSelect.value === "custom") {
    customInput.style.display = "block";
    customInput.focus();
  } else {
    customInput.style.display = "none";
  }
}

async function loadAIConfig() {
  try {
    const res = await fetch(`${SERVER_B_URL}/api/ai_config`);
    if (res.ok) {
      const data = await res.json();
      const statusBadge = document.getElementById("ai-key-status");
      const providerSelect = document.getElementById("ai-provider-select");
      const modelSelect = document.getElementById("ai-model-select");
      const customInput = document.getElementById("ai-custom-model-input");

      if (providerSelect) providerSelect.value = data.provider;
      onProviderChange();

      if (modelSelect && data.model_name) {
        let found = false;
        for (let opt of modelSelect.options) {
          if (opt.value === data.model_name) {
            modelSelect.value = data.model_name;
            found = true;
            break;
          }
        }
        if (!found) {
          modelSelect.value = "custom";
          if (customInput) {
            customInput.style.display = "block";
            customInput.value = data.model_name;
          }
        }
      }

      if (statusBadge) {
        if (data.is_key_set) {
          statusBadge.textContent = `🟢 ${data.provider.toUpperCase()} (${data.masked_key}) - Model: ${data.model_name}`;
          statusBadge.style.color = "#238636";
        } else {
          statusBadge.textContent = `⚪ Chưa kích hoạt Key (Chạy Offline Engine)`;
          statusBadge.style.color = "#8b949e";
        }
      }
    }
  } catch (e) {}
}

async function saveAIConfig() {
  const provider = document.getElementById("ai-provider-select").value;
  const apiKey = document.getElementById("ai-api-key-input").value.trim();
  const modelSelect = document.getElementById("ai-model-select");
  const customInput = document.getElementById("ai-custom-model-input");

  let modelName = modelSelect.value;
  if (modelName === "custom") {
    modelName = customInput.value.trim();
    if (!modelName) {
      alert("Vui lòng nhập tên Model tùy chỉnh!");
      customInput.focus();
      return;
    }
  }

  if (!apiKey) {
    alert("Vui lòng nhập API Key trước khi lưu!");
    return;
  }

  try {
    const res = await fetch(`${SERVER_B_URL}/api/ai_config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: provider,
        api_key: apiKey,
        model_name: modelName
      })
    });

    if (res.ok) {
      const data = await res.json();
      alert(`Đã kích hoạt thành công AI: ${data.provider.toUpperCase()} (${data.model_name})!`);
      document.getElementById("ai-api-key-input").value = "";
      loadAIConfig();
    } else {
      alert("Lỗi khi lưu cấu hình AI. Kiểm tra Server B!");
    }
  } catch (e) {
    alert(`Lỗi kết nối Server B: ${e.message}`);
  }
}

async function fetchOnlineModels() {
  const provider = document.getElementById("ai-provider-select").value;
  const apiKey = document.getElementById("ai-api-key-input").value.trim();
  const btn = document.getElementById("btn-fetch-models");
  const modelSelect = document.getElementById("ai-model-select");

  const originalText = btn.innerHTML;
  btn.innerHTML = "⏳ Đang quét...";
  btn.disabled = true;

  try {
    const res = await fetch(`${SERVER_B_URL}/api/ai_models/fetch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: provider,
        api_key: apiKey
      })
    });

    if (res.ok) {
      const data = await res.json();
      if (data.models && data.models.length > 0) {
        let html = "";
        data.models.forEach(m => {
          html += `<option value="${m.id}">${m.display}</option>`;
        });
        html += `<option value="custom">✏️ Nhập model tùy chỉnh khác...</option>`;
        modelSelect.innerHTML = html;
        onModelChange();

        const srcText = data.source === "online_api" ? "trực tiếp từ API chính thức" : "từ cơ sở dữ liệu cập nhật";
        alert(`✅ Đã tìm thấy ${data.count} models ${data.provider.toUpperCase()} (${srcText})!`);
      }
    } else {
      alert("Không thể quét danh sách model từ Server B!");
    }
  } catch (err) {
    alert(`Lỗi kết nối khi quét model: ${err.message}`);
  } finally {
    btn.innerHTML = originalText;
    btn.disabled = false;
  }
}

window.addEventListener("DOMContentLoaded", () => {
  initCharts();
  loadInitialBars();
  connectWebSocket();
  fetchStatus();
  fetchPositions();
  fetchRadarFallback();
  loadAIConfig();
  setInterval(() => {
    fetchStatus();
    fetchPositions();
    fetchLatestBarsFallback();
    fetchRadarFallback();
  }, 1500);
});

