// YTC Price Action Trader - Web UI Client
let chartM30, chartM3, chartM1;
let seriesM30, seriesM3, seriesM1;
let wsClient;
let entryLine, slLine, tp1Line, tp2Line, lrpLine;
let htfLines = [];

let currentRadar = null;
let activeTradesData = [];
let currentManualSide = "BUY";

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
  currentRadar = radar;
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
    if (payload && payload.type === "AI_PRE_ENTRY_EVALUATING") {
      logTelemetry(`🤖 [AI GATEKEEPER] Đang đánh giá điểm vào lệnh ${payload.symbol} ${payload.side} @ ${payload.entry} (Model: ${payload.model || 'AI'})...`);
    } else if (payload && payload.type === "AI_ENTRY_APPROVED") {
      logTelemetry(`✅ [AI APPROVED] Điểm vào lệnh ${payload.symbol} ${payload.side} @ ${payload.entry} ĐÃ ĐƯỢC CHẤP THUẬN! (Tin cậy: ${(payload.confidence * 100).toFixed(0)}%) - ${payload.reason}`);
    } else if (payload && payload.type === "AI_ENTRY_VETOED") {
      const concerns = payload.concerns && payload.concerns.length ? ` | Cảnh báo: ${payload.concerns.join("; ")}` : "";
      logTelemetry(`🚫 [AI VETOED] Từ chối vào lệnh ${payload.symbol} ${payload.side} @ ${payload.entry}! (Tin cậy: ${(payload.confidence * 100).toFixed(0)}%) - Lý do: ${payload.reason}${concerns}`);
    } else if (payload && payload.type === "AI_ENTRY_EVAL_TIMEOUT") {
      logTelemetry(`⚠️ [AI TIMEOUT] Hết thời gian chờ AI đánh giá pre-entry (${payload.reason}). Áp dụng chính sách fallback: ${payload.policy}`);
    } else {
      logTelemetry(`[${msg.topic.toUpperCase()}] ${JSON.stringify(payload)}`);
    }
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

      // Kiểm tra và cập nhật trạng thái AutoTrading
      const badge = document.getElementById("stat-autotrading-badge");
      const banner = document.getElementById("autotrading-warning-banner");
      const bannerTitle = document.getElementById("autotrading-warning-title");
      const bannerDesc = document.getElementById("autotrading-warning-desc");

      if (badge && data.autotrading) {
        const at = data.autotrading;
        if (at.auto_trading_ready) {
          badge.textContent = "ALGO ON (READY)";
          badge.style.background = "#0f2d1e";
          badge.style.color = "#3fb950";
          badge.style.borderColor = "#238636";
          if (banner) banner.style.display = "none";
        } else {
          badge.textContent = "ALGO OFF (BLOCKED)";
          badge.style.background = "#3b1219";
          badge.style.color = "#f85149";
          badge.style.borderColor = "#da3633";

          if (banner) {
            banner.style.display = "flex";
            if (!at.connected) {
              if (bannerTitle) bannerTitle.textContent = "MT5 CHƯA KẾT NỐI";
              if (bannerDesc) bannerDesc.textContent = "Chưa kết nối được với phần mềm MetaTrader 5 terminal. Vui lòng mở MT5 trên máy.";
            } else if (!at.trade_allowed) {
              if (bannerTitle) bannerTitle.textContent = "NÚT 'ALGO TRADING' ĐANG BỊ TẮT TRÊN MT5!";
              if (bannerDesc) bannerDesc.textContent = "Vui lòng bấm vào nút 'Algo Trading' trên thanh công cụ phần mềm MT5 (chuyển sang màu Xanh lá) để bot có thể tự động vào lệnh.";
            } else {
              if (bannerTitle) bannerTitle.textContent = "TÀI KHOẢN CHƯA BẬT GIAO DỊCH TỰ ĐỘNG!";
              if (bannerDesc) bannerDesc.textContent = at.message || "Vào Tools -> Options -> Expert Advisors và bật 'Allow Algo Trading'.";
            }
          }
        }
      }
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
      activeTradesData = trades;
      const tbody = document.getElementById("positions-tbody");
      document.getElementById("position-count").textContent = `${trades.length} Open`;

      if (trades.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:#8b949e;">No active positions. Scanning for wholesale setups...</td></tr>`;
        return;
      }

      tbody.innerHTML = trades.map(t => {
        const ticket = t.part1.ticket || t.limit_order_ticket || 0;
        return `
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
            <div style="display:flex; gap:4px;">
              <button class="btn btn-primary" style="padding:2px 6px; font-size:10px;" onclick="openModifyModal(${ticket}, ${t.part1.sl_price}, ${t.part1.tp_price}, '${t.trade_id}')">Sửa SL/TP</button>
              <button class="btn btn-warning" style="padding:2px 6px; font-size:10px;" onclick="forceScratch(${t.part1.ticket})">Scratch</button>
            </div>
          </td>
        </tr>
      `;
      }).join("");
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
  const jsonArea = document.getElementById("ai-plan-json");
  let cfg;
  try {
    cfg = JSON.parse(jsonArea.value);
  } catch (err) {
    alert("Kế hoạch JSON không hợp lệ. Vui lòng kiểm tra hoặc bấm 'Generate AI Plan' lại.");
    return;
  }

  try {
    const res = await fetch(`${SERVER_A_URL}/api/deploy_plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: cfg })
    });
    const data = await res.json();
    if (res.ok) {
      alert(`Đã nạp kế hoạch AI (${cfg.session_id || "Session"}) thành công vào Server A!\nChế độ thị trường: ${cfg.market_regime}`);
      fetchStatus();
    } else {
      alert(`Lỗi khi nạp kế hoạch vào Server A: ${data.detail || "Không rõ nguyên nhân"}`);
    }
  } catch (e) {
    alert(`Lỗi kết nối tới Server A: ${e.message}`);
  }
}

let closedTradesData = [];

async function fetchTradeHistory() {
  try {
    const res = await fetch(`${SERVER_A_URL}/api/trades/history`);
    if (res.ok) {
      const data = await res.json();
      const closed = data.closed_trades || [];
      closedTradesData = closed;
      const countEl = document.getElementById("history-count");
      if (countEl) countEl.textContent = `${closed.length} Closed`;

      const tbody = document.getElementById("history-tbody");
      if (!tbody) return;

      if (closed.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:#8b949e;">Chưa có lệnh hoàn tất nào trong phiên.</td></tr>`;
        return;
      }

      tbody.innerHTML = closed.map(t => {
        const eCtx = t.entry_context || {};
        const cCtx = t.close_context || {};
        const sideColor = t.side === 'BUY' ? '#3fb950' : '#f85149';
        
        let stateBadge = '<span style="background:#21262d; color:#8b949e; padding:2px 6px; border-radius:4px;">UNKNOWN</span>';
        if (t.state === 'FULLY_CLOSED') {
          stateBadge = '<span style="background:#0f2d1e; color:#3fb950; border:1px solid #238636; padding:2px 6px; border-radius:4px; font-weight:bold;">T1/T2 HIT</span>';
        } else if (t.state === 'STOPPED_OUT') {
          stateBadge = '<span style="background:#3b1219; color:#f85149; border:1px solid #da3633; padding:2px 6px; border-radius:4px; font-weight:bold;">STOPPED OUT</span>';
        } else if (t.state === 'SCRATCHED') {
          stateBadge = '<span style="background:#3d2e05; color:#e3b341; border:1px solid #9e6a03; padding:2px 6px; border-radius:4px; font-weight:bold;">SCRATCHED</span>';
        }

        const ws = eCtx.wholesale || {};
        const wsText = ws.LWP ? `LWP: ${ws.LWP} | LRP: ${ws.LRP} (R:R: ${(ws.rr_ratio_part1||0).toFixed(1)})` : '--';
        const entryPrice = eCtx.order_price ? Number(eCtx.order_price).toFixed(2) : (t.part1 ? Number(t.part1.entry_price).toFixed(2) : '--');
        const slPrice = eCtx.sl ? Number(eCtx.sl).toFixed(2) : (t.part1 ? Number(t.part1.sl_price).toFixed(2) : '--');
        const tpPrice = eCtx.tp1 ? Number(eCtx.tp1).toFixed(2) : (t.part1 ? Number(t.part1.tp_price).toFixed(2) : '--');
        const timeStr = eCtx.time_str || (t.open_time ? new Date(t.open_time * 1000).toLocaleTimeString() : '--');
        const regimeStr = eCtx.market_regime ? `<br><small style="color:#8b949e;">${eCtx.market_regime}</small>` : '';
        const exitReason = cCtx.close_reason || t.state;

        return `
          <tr>
            <td><code>${t.trade_id}</code></td>
            <td style="color:#8b949e; font-size:11px;">${timeStr}</td>
            <td><strong>${t.setup}</strong> ${regimeStr}</td>
            <td style="color:${sideColor}; font-weight:bold;">${t.side}</td>
            <td>${entryPrice}</td>
            <td><span style="color:#f85149;">${slPrice}</span> / <span style="color:#3fb950;">${tpPrice}</span></td>
            <td style="font-size:11px; color:#8b949e;">${wsText}</td>
            <td>${stateBadge}</td>
            <td style="font-size:11px; color:#e3b341;">${exitReason}</td>
          </tr>
        `;
      }).reverse().join("");
    }
  } catch (e) {
    console.error("fetchTradeHistory error:", e);
  }
}

function renderAuditReport(r) {
  const out = document.getElementById("audit-output");
  if (!out) return;

  const scorePct = (r.compliance_score * 100).toFixed(1);
  const scoreColor = r.compliance_score >= 0.8 ? '#3fb950' : (r.compliance_score >= 0.5 ? '#e3b341' : '#f85149');
  const violationsCount = (r.rule_violations || []).length;

  const critique = r.plan_critique || {};
  const tradeEvals = r.trade_evaluations || [];
  const lessons = r.lessons_learned || [];
  const params = r.parameter_adjustments_suggested || {};

  out.innerHTML = `
    <div style="border-bottom:1px solid #30363d; padding-bottom:10px; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
      <div>
        <span style="font-size:14px; font-weight:bold; color:#58a6ff;">📊 KẾT QUẢ KIỂM TOÁN PHIÊN (${r.session_id || "SESSION"})</span>
        <div style="color:#8b949e; font-size:11px; margin-top:2px;">Khung phương pháp: Lance Beggs YTC Price Action + AI Quantitative Review</div>
      </div>
      <div style="display:flex; gap:15px; align-items:center;">
        <div style="background:#161b22; border:1px solid ${scoreColor}; border-radius:6px; padding:4px 12px; text-align:center;">
          <div style="font-size:10px; color:#8b949e;">COMPLIANCE SCORE</div>
          <div style="font-size:16px; font-weight:bold; color:${scoreColor};">${scorePct}%</div>
        </div>
        <div style="background:#161b22; border:1px solid ${violationsCount > 0 ? '#da3633' : '#30363d'}; border-radius:6px; padding:4px 12px; text-align:center;">
          <div style="font-size:10px; color:#8b949e;">VIOLATIONS</div>
          <div style="font-size:16px; font-weight:bold; color:${violationsCount > 0 ? '#f85149' : '#8b949e'};">${violationsCount}</div>
        </div>
      </div>
    </div>

    <!-- 1. ĐÁNH GIÁ CÁCH TÍNH PLAN -->
    <div style="background:#161b22; border:1px solid #30363d; border-radius:6px; padding:10px 12px; margin-bottom:12px;">
      <div style="font-weight:bold; color:#f0883e; margin-bottom:6px; font-size:12px;">🎯 1. ĐÁNH GIÁ CÁCH TÍNH TRADING PLAN (PLAN CRITIQUE)</div>
      <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:8px; font-size:11px;">
        <div style="background:#0d1117; padding:8px; border-radius:4px; border-left:3px solid #58a6ff;">
          <div style="color:#8b949e;">Nhận định Market Regime:</div>
          <div style="color:#c9d1d9; margin-top:2px;">${critique.regime_accuracy || "Đã phân tích tương thích cấu trúc thị trường."}</div>
        </div>
        <div style="background:#0d1117; padding:8px; border-radius:4px; border-left:3px solid #bc8cff;">
          <div style="color:#8b949e;">Đánh giá Vùng HTF S/R:</div>
          <div style="color:#c9d1d9; margin-top:2px;">${critique.sr_zones_evaluation || "Các vùng cản H1/M30 phát huy vai trò làm mốc đối chiếu."}</div>
        </div>
        <div style="background:#0d1117; padding:8px; border-radius:4px; border-left:3px solid #e3b341;">
          <div style="color:#8b949e;">Bộ tính Giá sỉ Wholesale (LWP/LRP):</div>
          <div style="color:#c9d1d9; margin-top:2px;">${critique.wholesale_engine_assessment || "Tỷ lệ R:R và biên độ giá sỉ được kiểm soát."}</div>
        </div>
      </div>
      ${critique.summary ? `<div style="margin-top:8px; color:#8b949e; font-size:11px; font-style:italic;">"${critique.summary}"</div>` : ''}
    </div>

    <!-- 2. ĐÁNH GIÁ TỪNG LỆNH THEO HOÀN CẢNH VÀO -->
    ${tradeEvals.length > 0 ? `
    <div style="background:#161b22; border:1px solid #30363d; border-radius:6px; padding:10px 12px; margin-bottom:12px;">
      <div style="font-weight:bold; color:#58a6ff; margin-bottom:6px; font-size:12px;">📝 2. ĐÁNH GIÁ TỪNG LỆNH THEO HOÀN CẢNH VÀO (TRADE EVALUATIONS)</div>
      <div style="display:flex; flex-direction:column; gap:6px; max-height:220px; overflow-y:auto;">
        ${tradeEvals.map(te => {
          const badgeColor = te.score >= 0.8 ? '#3fb950' : (te.score >= 0.5 ? '#e3b341' : '#f85149');
          return `
            <div style="background:#0d1117; border:1px solid #21262d; border-radius:4px; padding:6px 10px; font-size:11px; display:flex; justify-content:space-between; align-items:center; gap:10px;">
              <div>
                <strong>${te.setup || 'TRADE'}</strong> <code style="color:#8b949e;">${te.trade_id}</code> |
                Side: <span style="color:${te.side==='BUY'?'#3fb950':'#f85149'}; font-weight:bold;">${te.side}</span> |
                Giá vào: <strong>${te.entry_price || '--'}</strong> |
                Trạng thái: <span style="color:#bc8cff;">${te.state || '--'}</span>
                <div style="color:#8b949e; margin-top:2px;">${te.critique || te.notes || ''}</div>
              </div>
              <div style="background:#161b22; border:1px solid ${badgeColor}; color:${badgeColor}; font-weight:bold; padding:2px 8px; border-radius:4px; white-space:nowrap;">
                ${(te.score * 100).toFixed(0)}%
              </div>
            </div>
          `;
        }).join("")}
      </div>
    </div>
    ` : ''}

    <!-- 3. KHUYẾN NGHỊ THAM SỐ -->
    <div style="background:#161b22; border:1px solid #30363d; border-radius:6px; padding:10px 12px; margin-bottom:12px;">
      <div style="font-weight:bold; color:#d29922; margin-bottom:6px; font-size:12px;">⚙️ 3. ĐỀ XUẤT ĐIỀU CHỈNH THAM SỐ CHO PHIÊN TỚI (PARAMETER ADJUSTMENTS)</div>
      <div style="display:flex; gap:15px; font-size:11px; flex-wrap:wrap;">
        ${Object.entries(params).map(([k, v]) => `
          <div style="background:#0d1117; padding:4px 10px; border-radius:4px; border:1px solid #30363d;">
            <code style="color:#79c0ff;">${k}</code>: <strong style="color:#e3b341;">${v}</strong>
          </div>
        `).join("") || '<div style="color:#8b949e;">Duy trì tham số mặc định hiện tại.</div>'}
      </div>
    </div>

    <!-- 4. BÀI HỌC KINH NGHIỆM ĐÃ NẠP VÀO VECTOR RAG -->
    <div style="background:#161b22; border:1px solid #30363d; border-radius:6px; padding:10px 12px;">
      <div style="font-weight:bold; color:#3fb950; margin-bottom:6px; font-size:12px;">🧠 4. BÀI HỌC KINH NGHIỆM ĐÃ NẠP VÀO VECTOR RAG (LESSONS LEARNED)</div>
      <ul style="margin:0; padding-left:18px; font-size:11px; color:#c9d1d9;">
        ${lessons.map(l => `<li style="margin-bottom:4px;">${l}</li>`).join("")}
      </ul>
    </div>
    ${r.raw_ai_analysis ? `<div style="margin-top:10px; color:#8b949e; font-size:11px; border-top:1px solid #21262d; padding-top:6px;"><strong>Tóm tắt AI:</strong> ${r.raw_ai_analysis}</div>` : ''}
  `;
}

async function runHindsightAudit() {
  const out = document.getElementById("audit-output");
  out.innerHTML = `<div style="padding:15px; text-align:center; color:#58a6ff;">⏳ Đang thu thập nhật ký giao dịch và gửi yêu cầu kiểm toán tới AI (Lance Beggs YTC Engine)...</div>`;

  try {
    const symbol = document.getElementById("stat-symbol").textContent || "XAUUSD";

    // 1. Fetch real closed trades and active trades from Server A
    let tradesHistory = [];
    try {
      const histRes = await fetch(`${SERVER_A_URL}/api/trades/history`);
      if (histRes.ok) {
        const hData = await histRes.json();
        tradesHistory = hData.closed_trades || [];
      }
    } catch (e) {
      console.warn("Could not fetch trade history from Server A:", e);
    }

    // 2. Fetch current session config from Server A or plan textarea
    let tradingConfig = null;
    const planArea = document.getElementById("ai-plan-json");
    const planText = planArea ? planArea.value : "";
    if (planText && planText.trim().startsWith("{")) {
      try {
        tradingConfig = JSON.parse(planText);
      } catch (e) {}
    }

    if (!tradingConfig) {
      tradingConfig = {
        session_id: `sess_${symbol}_${new Date().toISOString().slice(0, 10)}`,
        symbol: symbol,
        market_regime: document.getElementById("stat-regime") ? document.getElementById("stat-regime").textContent : "SIDEWAYS_RANGE",
        setups_enabled: { "TST": true, "BOF": true, "BPB": false, "PB": true, "CPB": true }
      };
    }

    // 3. Fetch latest bars for context
    let recentBars = {};
    try {
      const bRes = await fetch(`${SERVER_A_URL}/api/bars?timeframe=M3&count=10`);
      if (bRes.ok) recentBars["M3"] = await bRes.json();
    } catch (e) {}

    // 4. Send comprehensive audit payload to Server B
    const res = await fetch(`${SERVER_B_URL}/api/audit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        trading_config: tradingConfig,
        session_trades: tradesHistory,
        full_session_ohlcv: recentBars
      })
    });

    if (res.ok) {
      const data = await res.json();
      renderAuditReport(data.audit_report);
    } else {
      const err = await res.json();
      out.innerHTML = `<div style="color:#f85149; padding:12px;">❌ Lỗi khi thực hiện Audit từ Server B: ${err.detail || "Không rõ nguyên nhân"}</div>`;
    }
  } catch (e) {
    out.innerHTML = `<div style="color:#f85149; padding:12px;">❌ Lỗi kết nối tới Server B: ${e.message}</div>`;
  }
}

async function loadLatestAudit() {
  const out = document.getElementById("audit-output");
  out.innerHTML = `<div style="padding:15px; text-align:center; color:#8b949e;">Đang nạp báo cáo audit gần nhất...</div>`;

  try {
    const res = await fetch(`${SERVER_B_URL}/api/audit/latest`);
    if (res.ok) {
      const data = await res.json();
      if (data.status === "SUCCESS" && data.audit_report) {
        renderAuditReport(data.audit_report);
      } else {
        out.innerHTML = `<div style="color:#8b949e; text-align:center; padding:20px;">Chưa có báo cáo audit nào được lưu trên hệ thống.</div>`;
      }
    } else {
      out.innerHTML = `<div style="color:#f85149; padding:12px;">Không thể tải báo cáo audit gần nhất.</div>`;
    }
  } catch (e) {
    out.innerHTML = `<div style="color:#f85149; padding:12px;">Lỗi kết nối tới Server B: ${e.message}</div>`;
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

// ==========================================
// RISK CONFIGURATION (VOLUME / SL / TP OVERRIDES)
// ==========================================

async function loadRiskConfig() {
  try {
    const res = await fetch(`${SERVER_A_URL}/api/risk_config`);
    if (res.ok) {
      const data = await res.json();
      const badge = document.getElementById("risk-mode-badge");
      const volInput = document.getElementById("cfg-volume-input");
      const riskPctInput = document.getElementById("cfg-risk-pct-input");
      const slInput = document.getElementById("cfg-sl-input");
      const tpInput = document.getElementById("cfg-tp-input");
      const aiPreEntryCheck = document.getElementById("cfg-ai-pre-entry-input");
      const aiConfInput = document.getElementById("cfg-ai-confidence-input");
      const aiBadge = document.getElementById("ai-gatekeeper-badge");

      if (volInput && data.fixed_lot_size) volInput.value = data.fixed_lot_size;
      if (riskPctInput && data.account_risk_limit_percent) riskPctInput.value = data.account_risk_limit_percent;
      if (slInput && data.manual_sl) slInput.value = data.manual_sl;
      if (tpInput && data.manual_tp1) tpInput.value = data.manual_tp1;

      if (aiPreEntryCheck && data.enable_ai_pre_entry !== undefined) {
        aiPreEntryCheck.checked = Boolean(data.enable_ai_pre_entry);
      }
      if (aiConfInput && data.min_ai_confidence !== undefined) {
        aiConfInput.value = data.min_ai_confidence;
      }
      if (aiBadge) {
        if (data.enable_ai_pre_entry) {
          aiBadge.textContent = "AI ACTIVE";
          aiBadge.style.background = "#0f2d1e";
          aiBadge.style.color = "#3fb950";
          aiBadge.style.borderColor = "#238636";
        } else {
          aiBadge.textContent = "AI OFF";
          aiBadge.style.background = "#21262d";
          aiBadge.style.color = "#8b949e";
          aiBadge.style.borderColor = "#30363d";
        }
      }

      if (badge) {
        if (data.fixed_lot_size || data.manual_sl || data.manual_tp1) {
          badge.textContent = "CUSTOM (MANUAL OVERRIDE)";
          badge.style.color = "#e3b341";
          badge.style.borderColor = "#e3b341";
        } else {
          badge.textContent = "AUTO (SUGGESTED)";
          badge.style.color = "#3fb950";
          badge.style.borderColor = "#3fb950";
        }
      }
    }
  } catch (e) {
    console.error("loadRiskConfig error:", e);
  }
}

function applySuggestedLot() {
  const lot = currentRadar && currentRadar.lot_total ? currentRadar.lot_total : 0.17;
  const input = document.getElementById("cfg-volume-input");
  if (input) input.value = lot;
}

function applySuggestedSL() {
  const sl = currentRadar && currentRadar.s1 ? currentRadar.s1 : "";
  const input = document.getElementById("cfg-sl-input");
  if (input && sl) input.value = sl;
}

function applySuggestedTP() {
  const tp = currentRadar && currentRadar.t1 ? currentRadar.t1 : "";
  const input = document.getElementById("cfg-tp-input");
  if (input && tp) input.value = tp;
}

async function saveRiskConfig() {
  try {
    const vol = parseFloat(document.getElementById("cfg-volume-input").value) || null;
    const riskPct = parseFloat(document.getElementById("cfg-risk-pct-input").value) || null;
    const sl = parseFloat(document.getElementById("cfg-sl-input").value) || null;
    const tp = parseFloat(document.getElementById("cfg-tp-input").value) || null;
    const aiPreEntry = document.getElementById("cfg-ai-pre-entry-input") ? document.getElementById("cfg-ai-pre-entry-input").checked : true;
    const minAiConf = parseFloat(document.getElementById("cfg-ai-confidence-input") ? document.getElementById("cfg-ai-confidence-input").value : 0.65) || 0.65;

    const payload = {
      fixed_lot_size: vol,
      account_risk_limit_percent: riskPct,
      manual_sl: sl,
      manual_tp1: tp,
      enable_ai_pre_entry: aiPreEntry,
      min_ai_confidence: minAiConf
    };

    const res = await fetch(`${SERVER_A_URL}/api/risk_config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      alert("✅ Đã lưu cấu hình Khối lượng, SL/TP & AI Gatekeeper thành công!");
      loadRiskConfig();
    } else {
      alert("❌ Lỗi khi lưu cấu hình rủi ro.");
    }
  } catch (e) {
    alert(`Lỗi: ${e.message}`);
  }
}

async function resetRiskConfigToAuto() {
  try {
    const payload = {
      fixed_lot_size: 0,
      account_risk_limit_percent: 1.0,
      manual_sl: 0,
      manual_tp1: 0,
      manual_tp2: 0,
      enable_ai_pre_entry: true,
      min_ai_confidence: 0.65
    };

    const res = await fetch(`${SERVER_A_URL}/api/risk_config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      document.getElementById("cfg-volume-input").value = "";
      document.getElementById("cfg-risk-pct-input").value = "1.0";
      document.getElementById("cfg-sl-input").value = "";
      document.getElementById("cfg-tp-input").value = "";
      if (document.getElementById("cfg-ai-pre-entry-input")) document.getElementById("cfg-ai-pre-entry-input").checked = true;
      if (document.getElementById("cfg-ai-confidence-input")) document.getElementById("cfg-ai-confidence-input").value = "0.65";
      loadRiskConfig();
      alert("🔄 Đã đặt lại cấu hình Khối lượng, SL/TP & AI Gatekeeper về chế độ Gợi ý Tự động!");
    }
  } catch (e) {
    alert(`Lỗi: ${e.message}`);
  }
}

// ==========================================
// MANUAL ORDER MODAL (BUY / SELL)
// ==========================================

function openOrderModal(side) {
  currentManualSide = side;
  const modal = document.getElementById("modal-order");
  const header = document.getElementById("modal-order-header");
  const submitBtn = document.getElementById("modal-order-submit");

  if (header) {
    header.textContent = side === "BUY" ? "🟢 MỞ LỆNH MUA (BUY) - MT5" : "🔴 MỞ LỆNH BÁN (SELL) - MT5";
    header.style.color = side === "BUY" ? "#3fb950" : "#f85149";
  }

  if (submitBtn) {
    submitBtn.className = side === "BUY" ? "btn btn-success" : "btn btn-danger";
    submitBtn.textContent = side === "BUY" ? "🚀 Xác Nhận Mua (BUY)" : "🚀 Xác Nhận Bán (SELL)";
  }

  // Tự động điền giá trị gợi ý
  fillSuggestedOrderLot();
  fillSuggestedOrderSL();
  fillSuggestedOrderTP();

  if (modal) modal.style.display = "flex";
}

function closeOrderModal() {
  const modal = document.getElementById("modal-order");
  if (modal) modal.style.display = "none";
}

function onOrderTypeChange() {
  const type = document.getElementById("modal-order-type").value;
  const grpPrice = document.getElementById("grp-order-price");
  if (grpPrice) {
    grpPrice.style.display = (type === "LIMIT" || type === "STOP") ? "block" : "none";
  }
  if (type === "LIMIT" || type === "STOP") {
    fillSuggestedOrderPrice();
  }
}

function fillSuggestedOrderLot() {
  const lot = currentRadar && currentRadar.lot_total ? currentRadar.lot_total : 0.01;
  const input = document.getElementById("modal-order-volume");
  if (input) input.value = lot;
}

function fillSuggestedOrderPrice() {
  const price = currentRadar && currentRadar.entry ? currentRadar.entry : "";
  const input = document.getElementById("modal-order-price");
  if (input && price) input.value = price;
}

function fillSuggestedOrderSL() {
  const sl = currentRadar && currentRadar.s1 ? currentRadar.s1 : "";
  const input = document.getElementById("modal-order-sl");
  if (input && sl) input.value = sl;
}

function fillSuggestedOrderTP() {
  const tp = currentRadar && currentRadar.t1 ? currentRadar.t1 : "";
  const input = document.getElementById("modal-order-tp");
  if (input && tp) input.value = tp;
}

async function submitManualOrder() {
  try {
    const orderType = document.getElementById("modal-order-type").value;
    const vol = parseFloat(document.getElementById("modal-order-volume").value);
    const priceVal = parseFloat(document.getElementById("modal-order-price").value) || null;
    const slVal = parseFloat(document.getElementById("modal-order-sl").value) || null;
    const tpVal = parseFloat(document.getElementById("modal-order-tp").value) || null;

    if (!vol || vol <= 0) {
      alert("Vui lòng nhập khối lượng Lot hợp lệ (> 0)!");
      return;
    }

    const payload = {
      side: currentManualSide,
      order_type: orderType,
      volume: vol,
      price: priceVal,
      sl: slVal,
      tp: tpVal,
      comment: `MANUAL_${currentManualSide}`
    };

    const res = await fetch(`${SERVER_A_URL}/api/trade/place`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      const data = await res.json();
      closeOrderModal();
      alert(`✅ Đặt lệnh ${currentManualSide} thành công! Ticket MT5: #${data.ticket}`);
      fetchPositions();
    } else {
      const err = await res.json();
      alert(`❌ Lỗi đặt lệnh: ${err.detail || "Không rõ nguyên nhân"}`);
    }
  } catch (e) {
    alert(`Lỗi kết nối đặt lệnh: ${e.message}`);
  }
}

// ==========================================
// MODIFY POSITION MODAL (SL / TP)
// ==========================================

function openModifyModal(ticket, currentSL, currentTP, tradeId) {
  const modal = document.getElementById("modal-modify");
  document.getElementById("mod-ticket-label").textContent = `${ticket} (${tradeId})`;
  document.getElementById("mod-ticket-input").value = ticket;
  document.getElementById("mod-sl-input").value = currentSL ? currentSL.toFixed(2) : "";
  document.getElementById("mod-tp-input").value = currentTP ? currentTP.toFixed(2) : "";
  if (modal) modal.style.display = "flex";
}

function closeModifyModal() {
  const modal = document.getElementById("modal-modify");
  if (modal) modal.style.display = "none";
}

function fillModBreakeven() {
  const ticket = parseInt(document.getElementById("mod-ticket-input").value);
  const trade = activeTradesData.find(t => t.part1.ticket === ticket || t.part2.ticket === ticket || t.limit_order_ticket === ticket);
  if (trade && trade.part1) {
    const entry = trade.part1.entry_price;
    const beBuffer = 0.30;
    const beSL = trade.side === "BUY" ? (entry + beBuffer) : (entry - beBuffer);
    document.getElementById("mod-sl-input").value = beSL.toFixed(2);
  }
}

function fillModSuggestedTP() {
  if (currentRadar && currentRadar.t1) {
    document.getElementById("mod-tp-input").value = currentRadar.t1.toFixed(2);
  }
}

async function submitModifyPosition() {
  try {
    const ticket = parseInt(document.getElementById("mod-ticket-input").value);
    const sl = parseFloat(document.getElementById("mod-sl-input").value);
    const tp = parseFloat(document.getElementById("mod-tp-input").value);

    if (isNaN(sl) || isNaN(tp)) {
      alert("Vui lòng nhập đầy đủ giá trị SL và TP!");
      return;
    }

    const payload = { ticket_id: ticket, sl: sl, tp: tp };
    const res = await fetch(`${SERVER_A_URL}/api/trade/modify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      closeModifyModal();
      alert(`✅ Đã cập nhật SL/TP cho Ticket #${ticket} thành công trên MT5!`);
      fetchPositions();
    } else {
      const err = await res.json();
      alert(`❌ Lỗi cập nhật SL/TP: ${err.detail || "Không thể sửa lệnh"}`);
    }
  } catch (e) {
    alert(`Lỗi kết nối khi sửa vị thế: ${e.message}`);
  }
}

window.addEventListener("DOMContentLoaded", () => {
  initCharts();
  loadInitialBars();
  connectWebSocket();
  fetchStatus();
  fetchPositions();
  fetchTradeHistory();
  fetchRadarFallback();
  loadAIConfig();
  loadRiskConfig();
  setInterval(() => {
    fetchStatus();
    fetchPositions();
    fetchTradeHistory();
    fetchLatestBarsFallback();
    fetchRadarFallback();
  }, 1500);
});

