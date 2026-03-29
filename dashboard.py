import argparse
import json
import os
import pandas as pd
from datetime import datetime, timezone
from jinja2 import Template
import requests
from fetch_ohlcv import fetch_ohlcv
from sr_detect import detect_sr, load_config

CACHE_FILE = "data/pipeline_cache.json"

SYMBOLS = ["US500", "GER40", "JP225", "USOIL", "XAUUSD", "BTCUSD", "AUDUSD", "EURUSD", "USDJPY"]
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "REDACTED")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "REDACTED")


def build_history_string(bar_timestamps, symbol_results, last_n=10):
    """
    Build an N-char S/R history string for the last N bars.

    bar_timestamps: list of unix int timestamps for all bars, sorted ascending
    symbol_results: list of dicts with 'detected_at' (unix int) and 'result' ('support'/'resistance')
    Returns a string of length last_n: 'S', 'R', or '~' per bar, left-padded with '~' if fewer bars.
    """
    detection_map = {r['detected_at']: r['result'] for r in symbol_results}
    recent = bar_timestamps[-last_n:]
    chars = []
    for t in recent:
        if t in detection_map:
            chars.append('S' if detection_map[t] == 'support' else 'R')
        else:
            chars.append('~')
    return ''.join(chars).rjust(last_n, '~')


def format_telegram_message(detections):
    """
    Build the consolidated Telegram alert message body.

    detections: list of dicts with keys 'symbol' (str), 'result' ('support'/'resistance'),
                'history' (10-char string from build_history_string)
    Returns a Markdown-formatted string ready to send.
    """
    message = "🚨 S/R ALERT 🚨\n\n"
    for det in detections:
        emoji = {"support": "⬆️", "resistance": "⬇️"}[det['result']]
        message += f"`{det['symbol']:<6} {det['history']}` {emoji}\n"
    message += "\n[View Dashboard](https://binaryman-aus.github.io/uudd/)"
    return message


def evaluate_zone_accuracy(zone, df):
    detected_at = zone['detected_at']
    zone_type   = zone['result']
    z_low       = zone['price_range']['low']
    z_high      = zone['price_range']['high']
    z_range     = z_high - z_low if z_high > z_low else 1

    bar_ts = df['time'].apply(lambda t: int(t.timestamp()))
    future = df[bar_ts > detected_at]

    filled     = False
    p1_outcome = 'untested'
    p1_time    = None
    p1_entry   = None
    p2_outcome = 'untested'
    p2_mag     = None

    for _, bar in future.iterrows():
        if not filled:
            if zone_type == 'support':
                if bar['low'] > z_high:
                    continue
                elif bar['high'] < z_high:
                    if bar['low'] < z_low:            # gapped through entire zone
                        p1_outcome = 'break'; p1_time = int(bar['time'].timestamp()); p1_entry = 'gap'
                        p2_outcome = 'broken'; p2_mag = 0.0
                        break
                    else:
                        continue                       # gapped into zone only — still alive
                else:                                  # straddles z_high: limit fills
                    p1_time = int(bar['time'].timestamp()); p1_entry = 'valid'
                    if bar['low'] < z_low:             # SL hit on fill bar
                        p1_outcome = 'break'; p2_outcome = 'broken'; p2_mag = 0.0
                        break
                    p1_outcome = 'bounce'; filled = True; p2_outcome = 'active'; p2_mag = 0.0
            else:                                      # resistance
                if bar['high'] < z_low:
                    continue
                elif bar['low'] > z_low:
                    if bar['high'] > z_high:           # gapped through entire zone
                        p1_outcome = 'break'; p1_time = int(bar['time'].timestamp()); p1_entry = 'gap'
                        p2_outcome = 'broken'; p2_mag = 0.0
                        break
                    else:
                        continue                       # gapped into zone only — still alive
                else:                                  # straddles z_low: limit fills
                    p1_time = int(bar['time'].timestamp()); p1_entry = 'valid'
                    if bar['high'] > z_high:           # SL hit on fill bar
                        p1_outcome = 'break'; p2_outcome = 'broken'; p2_mag = 0.0
                        break
                    p1_outcome = 'bounce'; filled = True; p2_outcome = 'active'; p2_mag = 0.0

        if filled:                                     # Phase 2: track excursion and SL
            if zone_type == 'support':
                if bar['high'] > z_high:
                    p2_mag = max(p2_mag, (bar['high'] - z_high) / z_range)
                if bar['low'] < z_low:
                    p2_outcome = 'broken'; break
            else:
                if bar['low'] < z_low:
                    p2_mag = max(p2_mag, (z_low - bar['low']) / z_range)
                if bar['high'] > z_high:
                    p2_outcome = 'broken'; break

    return {
        'outcome': p1_outcome,
        'test_bar_time': p1_time,
        'entry': p1_entry,
        'phase2': {
            'outcome': p2_outcome,
            'max_magnitude': round(p2_mag, 2) if p2_mag is not None else None
        }
    }


def generate_dashboard(all_results, params, output_file="dashboard.html"):
    """
    Generates a 3x3 dashboard HTML report with mobile responsiveness and fullscreen toggle.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    template_str = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>S/R Dashboard (3x3)</title>
        <script>
            /* Runs synchronously before CSS renders — no layout flash.
               userAgentData.mobile is the W3C UA Client Hints standard: browsers explicitly
               report true on mobile, false on desktop (incl. Surface with touch). Falls back
               to UA string regex for Firefox/Safari which don't yet support userAgentData. */
            if (navigator.userAgentData?.mobile ?? /Mobi|Android/i.test(navigator.userAgent)) {
                document.documentElement.classList.add('phone');
            }
        </script>
        <script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js"></script>
        <style>
            html, body { 
                margin: 0; 
                padding: 0; 
                width: 100vw; 
                height: 100vh; 
                overflow: hidden; 
                font-family: sans-serif;
                background-color: #f0f0f5;
            }
            .dashboard-header {
                height: 40px;
                background: #333;
                color: white;
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 0 15px;
                font-size: 0.9em;
                position: sticky;
                top: 0;
                z-index: 1000;
            }
            .grid-container {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                grid-template-rows: repeat(3, 1fr);
                gap: 5px;
                height: calc(100vh - 40px);
                padding: 5px;
                box-sizing: border-box;
            }
            /* Phone layout — class set synchronously by inline script using userAgentData.mobile */
            html.phone, html.phone body {
                overflow: auto;
            }
            html.phone .grid-container {
                grid-template-columns: 1fr;
                grid-template-rows: none;
                height: auto;
                overflow: visible;
            }
            html.phone .chart-box {
                height: 600px !important;
                margin-bottom: 10px;
            }
            .chart-box {
                background: white;
                border: 1px solid #ccc;
                position: relative;
                display: flex;
                flex-direction: column;
                overflow: hidden;
            }
            .chart-header {
                background: #eee;
                padding: 4px 8px;
                font-size: 0.8em;
                font-weight: bold;
                border-bottom: 1px solid #ddd;
                display: flex;
                flex-direction: row;
                align-items: center;
                justify-content: space-between;
                cursor: pointer;
                white-space: nowrap;
                overflow: hidden;
            }
            .chart-header:hover {
                background: #ddd;
            }
            .reset-btn {
                background: none;
                border: 1px solid #aaa;
                border-radius: 3px;
                cursor: pointer;
                font-size: 1em;
                line-height: 1;
                padding: 0px 5px;
                color: #555;
                flex-shrink: 0;
                align-self: center;
            }
            .reset-btn:hover { background: #bbb; color: #000; }
            .chart-container {
                flex-grow: 1;
                position: relative;
            }
            .sr-label {
                font-size: 0.8em;
            }
            .support { color: green; }
            .resistance { color: red; }
            .settings-btn {
                background: none; border: none; cursor: pointer;
                font-size: 1.2em; color: white; padding: 4px 8px;
                line-height: 1; border-radius: 3px;
            }
            .settings-btn:hover { background: rgba(255,255,255,0.15); }
            #settings-panel {
                display: none; position: absolute; top: 40px; right: 10px;
                background: white; border: 1px solid #ccc; border-radius: 4px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.2); z-index: 2000;
                min-width: 180px; padding: 6px 0;
            }
            .settings-label {
                padding: 4px 14px; font-size: 0.8em; color: #888;
                font-weight: bold; text-transform: uppercase;
            }
            .settings-item {
                padding: 8px 14px; cursor: pointer; font-size: 0.9em; color: #333;
            }
            .settings-item:hover { background: #f0f0f0; }
            .settings-item.active { font-weight: bold; color: #2196F3; }
        </style>
    </head>
    <body>
        <div class="dashboard-header" style="position:relative;">
            <div><strong>S/R Multi-Symbol Dashboard</strong> | H1 Timeframe | <span id="bars-label">Last 150 Bars</span></div>
            <div style="display:flex;align-items:center;gap:10px;">
                <div id="dashboard-generated">Generated: {{ now }}</div>
                <button class="settings-btn" onclick="toggleSettings(event)" title="Settings">&#x2699;&#xFE0F;</button>
            </div>
            <div id="settings-panel" onclick="event.stopPropagation()">
                <div class="settings-label">Default Bars</div>
                <div class="settings-item" onclick="setDefaultBars(50)">50 bars</div>
                <div class="settings-item" onclick="setDefaultBars(100)">100 bars</div>
                <div class="settings-item" onclick="setDefaultBars(150)">150 bars</div>
                <div class="settings-item" onclick="setDefaultBars(200)">200 bars</div>
            </div>
        </div>
        <div class="grid-container">
            {% for symbol in symbols %}
            <div id="box-{{ loop.index }}" class="chart-box">
                <div class="chart-header" onclick="openFullscreen('{{ symbol }}')">
                    <span id="title-{{ symbol }}">{{ symbol }} 🔍</span>
                    <span style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
                        <span style="font-family:monospace;font-size:1.2em;letter-spacing:2px;">{% for ch in histories[symbol] %}{% if ch == 'S' %}<span class="support">S</span>{% elif ch == 'R' %}<span class="resistance">R</span>{% else %}<span style="color:#bbb;">~</span>{% endif %}{% endfor %}</span>
                        <button class="reset-btn" onclick="resetChart('{{ symbol }}', event)" title="Reset zoom &amp; position">&#x21BA;</button>
                    </span>
                </div>
                <div id="chart-{{ loop.index }}" class="chart-container"></div>
            </div>
            {% endfor %}
        </div>

        <!-- Fullscreen overlay: fresh chart instance so grid charts are never resized/corrupted -->
        <div id="fs-overlay" style="display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:9999;background:white;flex-direction:column;">
            <div id="fs-header" style="background:#eee;padding:4px 12px;font-size:0.85em;font-weight:bold;border-bottom:1px solid #ddd;display:flex;justify-content:space-between;align-items:center;flex-shrink:0;cursor:pointer;" onclick="closeFullscreen()">
                <span id="fs-title"></span>
                <span style="display:flex;gap:8px;align-items:center;">
                    <button class="reset-btn" onclick="resetFsChart(event)" title="Reset zoom &amp; position" style="font-size:1.1em;padding:2px 8px;">&#x21BA; Reset</button>
                    <span style="font-size:1.1em;padding:2px 8px;background:#ccc;border-radius:3px;">&#x2715; Close</span>
                </span>
            </div>
            <div style="flex:1;display:flex;min-height:0;">
                <div style="flex:1;display:flex;flex-direction:column;min-height:0;">
                    <div id="fs-chart-container" style="flex:7;position:relative;min-height:0;"></div>
                    <div id="fs-scatter-container" style="flex:3;position:relative;min-height:0;border-top:1px solid #eee;"></div>
                </div>
                <div id="fs-accuracy-panel" style="width:300px;overflow-y:auto;border-left:1px solid #ddd;background:#fafafa;padding:8px;font-size:0.8em;flex-shrink:0;">
                    <div id="fs-accuracy-summary" style="margin-bottom:8px;border-bottom:1px solid #ddd;padding-bottom:6px;font-weight:bold;"></div>
                    <div id="fs-accuracy-list"></div>
                </div>
            </div>
        </div>

        <script>
            const allData = {{ all_data_json }};
            const allResults = {{ all_results_json }};
            const symbols = {{ symbols_json }};

            const genTime = {{ gen_timestamp }};
            if (genTime > 0) {
                const genDate = new Date(genTime * 1000);
                const gpad      = n => String(n).padStart(2, '0');
                const genUtcStr   = `${genDate.getUTCFullYear()}-${gpad(genDate.getUTCMonth()+1)}-${gpad(genDate.getUTCDate())} ${gpad(genDate.getUTCHours())}:${gpad(genDate.getUTCMinutes())}`;
                const genLocalStr = `${genDate.getFullYear()}-${gpad(genDate.getMonth()+1)}-${gpad(genDate.getDate())} ${gpad(genDate.getHours())}:${gpad(genDate.getMinutes())}`;
                document.getElementById('dashboard-generated').innerHTML =
                    `Generated: <span style="font-weight:normal;">UTC: ${genUtcStr} | Local: ${genLocalStr}</span>`;
            }

            const isPhone = document.documentElement.classList.contains('phone');

            function getDefaultBars() {
                const saved = localStorage.getItem('defaultBars');
                if (saved) return parseInt(saved, 10);
                return isPhone ? 100 : 150;
            }
            function setDefaultBars(n) {
                localStorage.setItem('defaultBars', n);
                document.getElementById('bars-label').textContent = `Last ${n} Bars`;
                symbols.forEach(symbol => {
                    const chart = gridCharts[symbol];
                    if (!chart) return;
                    const idx = symbols.indexOf(symbol) + 1;
                    const container = document.getElementById(`chart-${idx}`);
                    const w = container.offsetWidth || 600;
                    chart.timeScale().applyOptions({ barSpacing: Math.max(1, w / n), rightOffset: 5 });
                    chart.timeScale().scrollToRealTime();
                });
                closeSettings();
            }
            function closeSettings() {
                document.getElementById('settings-panel').style.display = 'none';
            }
            function toggleSettings(e) {
                e.stopPropagation();
                const panel = document.getElementById('settings-panel');
                const isOpen = panel.style.display === 'block';
                panel.style.display = isOpen ? 'none' : 'block';
                if (!isOpen) {
                    const cur = getDefaultBars();
                    document.querySelectorAll('.settings-item').forEach(el => {
                        el.classList.toggle('active', el.textContent.startsWith(String(cur)));
                    });
                }
            }
            document.addEventListener('click', () => closeSettings());

            const CHART_OPTS = {
                autoSize: true,
                layout: { background: { type: 'solid', color: 'white' }, textColor: '#333' },
                grid: { vertLines: { color: '#f5f5f5' }, horzLines: { color: '#f5f5f5' } },
                timeScale: { timeVisible: true, secondsVisible: false, borderVisible: false },
                rightPriceScale: { autoScale: true, borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.1 } },
                handleScroll: { mouseWheel: !isPhone, pressedMouseMove: !isPhone, horzTouchDrag: !isPhone, vertTouchDrag: false },
                handleScale: { axisPressedMouseMove: !isPhone, mouseWheel: !isPhone, pinch: !isPhone },
            };

            const FS_CHART_OPTS = {
                ...CHART_OPTS,
                handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
                handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
            };

            function buildChart(container, symbol, visibleBars = 150, opts = CHART_OPTS) {
                const symbolData = allData[symbol] || { candles: [], ema9: [], ema21: [] };
                const chartData  = symbolData.candles;
                const srResults  = (allResults[symbol] && allResults[symbol].results) || [];
                if (chartData.length === 0) return null;

                const chart = LightweightCharts.createChart(container, opts);

                const candleSeries = chart.addCandlestickSeries({
                    upColor: '#26a69a', downColor: '#ef5350',
                    borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350',
                });
                candleSeries.setData(chartData);
                if (opts === FS_CHART_OPTS) fsCandleSeriesRef = candleSeries;

                if (symbolData.ema9.length > 0)
                    chart.addLineSeries({ color: '#2196F3', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
                         .setData(symbolData.ema9);

                if (symbolData.ema21.length > 0)
                    chart.addLineSeries({ color: '#FF9800', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
                         .setData(symbolData.ema21);

                srResults.forEach(res => {
                    const color = res.result === 'support' ? 'rgba(38,166,154,0.25)' : 'rgba(239,83,80,0.25)';
                    const boxSeries = chart.addBaselineSeries({
                        baseValue: { type: 'price', price: res.price_range.low },
                        topFillColor1: color, topFillColor2: color, topLineColor: 'transparent',
                        bottomFillColor1: 'transparent', bottomFillColor2: 'transparent', bottomLineColor: 'transparent',
                        lineWidth: 0, priceLineVisible: false, lastValueVisible: false,
                        crosshairMarkerVisible: false, autoscaleInfoProvider: () => null,
                    });
                    const startTime = typeof res.start_time === 'number' ? res.start_time : Math.floor(new Date(res.start_time).getTime() / 1000);
                    const endTime   = typeof res.end_time   === 'number' ? res.end_time   : Math.floor(new Date(res.end_time).getTime()   / 1000);
                    const boxData = chartData
                        .filter(d => d.time >= startTime && d.time <= endTime)
                        .map(d => ({ time: d.time, value: res.price_range.high }));
                    if (boxData.length > 0) boxSeries.setData(boxData);
                });

                const containerWidth = container.offsetWidth || 600;
                chart.timeScale().applyOptions({
                    barSpacing: Math.max(1, containerWidth / visibleBars),
                    rightOffset: 5,
                });
                chart.timeScale().scrollToRealTime();
                return chart;
            }

            let fsChartInstance = null;
            let fsScatterInstance = null;
            let fsCandleSeriesRef = null;
            const gridCharts = {};

            function resetChart(symbol, e) {
                e.stopPropagation();
                const chart = gridCharts[symbol];
                if (!chart) return;
                const idx = symbols.indexOf(symbol) + 1;
                const container = document.getElementById(`chart-${idx}`);
                const visibleBars = getDefaultBars();
                const containerWidth = container.offsetWidth || 600;
                chart.timeScale().applyOptions({
                    barSpacing: Math.max(1, containerWidth / visibleBars),
                    rightOffset: 5,
                });
                chart.timeScale().scrollToRealTime();
            }

            function resetFsChart(e) {
                e.stopPropagation();
                if (!fsChartInstance) return;
                const container = document.getElementById('fs-chart-container');
                const containerWidth = container.offsetWidth || 600;
                fsChartInstance.timeScale().applyOptions({
                    barSpacing: Math.max(1, containerWidth / 500),
                    rightOffset: 5,
                });
                fsChartInstance.timeScale().scrollToRealTime();
            }

            function fmtPrice(p) {
                if (p >= 1000) return p.toFixed(2);
                if (p >= 1)    return p.toFixed(4);
                return p.toFixed(5);
            }

            function renderAccuracyPanel(symbol) {
                const data    = allResults[symbol];
                if (!data) return;
                const summary = data.accuracy_summary || {};
                const zones   = (data.results || []).filter(z => z.result !== 'nil');

                document.getElementById('fs-accuracy-summary').innerHTML =
                    `&#x1F7E2; ${summary.active || 0} active &nbsp; &#x1F534; ${summary.broken || 0} broken &nbsp; &#x26AA; ${summary.untested || 0} untested`;

                const P2_ICON = { active: '&#x1F7E2;', broken: '&#x1F534;', untested: '&#x26AA;' };
                const MONTHS  = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                function fmtTs(ts) {
                    const d = new Date(ts * 1000);
                    const p = n => String(n).padStart(2, '0');
                    return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
                }
                document.getElementById('fs-accuracy-list').innerHTML = [...zones].reverse().map(z => {
                    const acc      = z.accuracy || {};
                    const p2       = acc.phase2 || { outcome: 'untested', max_magnitude: null };
                    const label    = z.result === 'support' ? '\u25B2 S' : '\u25BC R';
                    const price    = `${fmtPrice(z.price_range.low)}\u2013${fmtPrice(z.price_range.high)}`;
                    const icon     = P2_ICON[p2.outcome] || '&#x26AA;';
                    const mag      = p2.max_magnitude !== null && p2.max_magnitude !== undefined
                                     ? `<span style="color:#555;margin-left:6px;">max ${p2.max_magnitude}x</span>` : '';
                    const broken   = p2.outcome === 'broken'
                                     ? ' <span style="color:#c00;font-weight:bold;">\u2717</span>' : '';
                    const gap      = acc.entry === 'gap'
                                     ? ' <span style="color:#bbb;font-size:0.85em;">gap</span>' : '';
                    const detected = fmtTs(z.detected_at);
                    const startTs  = Math.floor(new Date(z.start_time).getTime() / 1000);
                    const endTs    = Math.floor(new Date(z.end_time).getTime() / 1000);
                    return `<div class="acc-zone-row" data-low="${z.price_range.low}" data-high="${z.price_range.high}" data-start="${startTs}" data-end="${endTs}" style="padding:4px 2px;border-bottom:1px solid #eee;cursor:default;">
                        ${icon} ${label} <span style="font-family:monospace;">${price}</span>${gap}${broken}${mag}
                        <div style="color:#aaa;font-size:0.9em;padding-left:18px;">${detected}</div>
                    </div>`;
                }).join('');
            }

            function buildScatterChart(container, symbol) {
                const zones   = (allResults[symbol]?.results || []).filter(z => z.result !== 'nil');
                const candles = allData[symbol]?.candles || [];
                if (!candles.length) return null;

                // Map detected_at → {value, color} for zones with a fill
                const zoneMap = {};
                zones.forEach(z => {
                    const p2  = z.accuracy?.phase2;
                    if (!p2 || p2.max_magnitude === null || p2.max_magnitude === undefined) return;
                    const val   = z.result === 'support' ? p2.max_magnitude : -p2.max_magnitude;
                    const color = z.result === 'support' ? 'rgba(38,166,154,0.8)' : 'rgba(239,83,80,0.8)';
                    zoneMap[z.detected_at] = { value: val, color };
                });

                // Build full-density data array (same bar count as candlestick for logical-range sync)
                const histData = candles.map(c => {
                    const zd = zoneMap[c.time];
                    return zd ? { time: c.time, value: zd.value, color: zd.color }
                               : { time: c.time, value: 0, color: 'rgba(0,0,0,0)' };
                });

                const chart = LightweightCharts.createChart(container, {
                    autoSize: true,
                    layout: { background: { type: 'solid', color: '#fafafa' }, textColor: '#555' },
                    grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
                    timeScale: { timeVisible: true, secondsVisible: false, borderVisible: false, rightOffset: 5 },
                    rightPriceScale: { autoScale: true, borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.1 } },
                    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
                    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
                    handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
                });

                // Zero baseline
                chart.addLineSeries({
                    color: '#bbb', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
                    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
                }).setData([
                    { time: candles[0].time, value: 0 },
                    { time: candles[candles.length - 1].time, value: 0 },
                ]);

                const hist = chart.addHistogramSeries({
                    base: 0, priceLineVisible: false, lastValueVisible: false,
                    priceFormat: { type: 'custom', formatter: p => p.toFixed(1) + 'x' },
                });
                hist.setData(histData);

                return chart;
            }

            function openFullscreen(symbol) {
                const overlay         = document.getElementById('fs-overlay');
                const container       = document.getElementById('fs-chart-container');
                const scatterContainer = document.getElementById('fs-scatter-container');
                if (fsChartInstance)  { fsChartInstance.remove();  fsChartInstance  = null; }
                if (fsScatterInstance) { fsScatterInstance.remove(); fsScatterInstance = null; }
                container.innerHTML = '';
                scatterContainer.innerHTML = '';
                document.getElementById('fs-title').textContent = symbol + ' \u2014 click header or press Esc to close';
                overlay.style.display = 'flex';
                document.body.style.overflow = 'hidden';
                fsChartInstance   = buildChart(container, symbol, 500, FS_CHART_OPTS);
                fsScatterInstance = buildScatterChart(scatterContainer, symbol);
                // Sync time scales (logical range — both charts share same bar count)
                if (fsChartInstance && fsScatterInstance) {
                    let syncing = false;
                    fsChartInstance.timeScale().subscribeVisibleLogicalRangeChange(range => {
                        if (syncing || !range) return; syncing = true;
                        fsScatterInstance.timeScale().setVisibleLogicalRange(range); syncing = false;
                    });
                    fsScatterInstance.timeScale().subscribeVisibleLogicalRangeChange(range => {
                        if (syncing || !range) return; syncing = true;
                        fsChartInstance.timeScale().setVisibleLogicalRange(range); syncing = false;
                    });
                    // Apply initial range from main chart
                    const initRange = fsChartInstance.timeScale().getVisibleLogicalRange();
                    if (initRange) fsScatterInstance.timeScale().setVisibleLogicalRange(initRange);
                }
                renderAccuracyPanel(symbol);
                if (fsChartInstance) {
                    fsChartInstance.subscribeCrosshairMove(param => {
                        const rows = document.querySelectorAll('.acc-zone-row');
                        if (!param.point || !param.time) {
                            rows.forEach(el => el.style.background = '');
                            return;
                        }
                        const price = fsCandleSeriesRef ? fsCandleSeriesRef.coordinateToPrice(param.point.y) : null;
                        if (price === null) { rows.forEach(el => el.style.background = ''); return; }
                        const t = typeof param.time === 'number' ? param.time : Math.floor(new Date(param.time).getTime() / 1000);
                        rows.forEach(el => {
                            const lo    = parseFloat(el.dataset.low);
                            const hi    = parseFloat(el.dataset.high);
                            const start = parseInt(el.dataset.start);
                            const end   = parseInt(el.dataset.end);
                            el.style.background = (price >= lo && price <= hi && t >= start && t <= end) ? '#fff9c4' : '';
                        });
                    });
                }
            }

            function closeFullscreen() {
                if (fsChartInstance)   { fsChartInstance.remove();   fsChartInstance   = null; }
                if (fsScatterInstance) { fsScatterInstance.remove();  fsScatterInstance = null; }
                document.getElementById('fs-chart-container').innerHTML = '';
                document.getElementById('fs-scatter-container').innerHTML = '';
                document.getElementById('fs-overlay').style.display = 'none';
                document.body.style.overflow = '';
            }

            document.addEventListener('keydown', e => { if (e.key === 'Escape') closeFullscreen(); });

            // Sync header label with stored preference on load
            document.getElementById('bars-label').textContent = `Last ${getDefaultBars()} Bars`;

            symbols.forEach((symbol, index) => {
                const container   = document.getElementById(`chart-${index + 1}`);
                const lastBarTime = (allResults[symbol] && allResults[symbol].last_bar_time) || 0;

                if (lastBarTime > 0) {
                    const date     = new Date(lastBarTime * 1000);
                    const pad      = n => String(n).padStart(2, '0');
                    const utcStr   = `${date.getUTCFullYear()}-${pad(date.getUTCMonth()+1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
                    const localStr = `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
                    const nowTs      = Math.floor(Date.now() / 1000);
                    const isOutdated = (nowTs - lastBarTime) > (3600 * 2);
                    const warning    = isOutdated
                        ? ' <span style="color:white;background:#ef5350;padding:1px 4px;border-radius:3px;font-weight:bold;font-size:0.8em;margin-left:5px;">\u26a0\ufe0f OUTDATED</span>'
                        : '';
                    document.getElementById(`title-${symbol}`).innerHTML =
                        `${symbol}${warning} &#x1F50D; <span style="font-weight:normal;color:#666;">UTC: ${utcStr} | Local: ${localStr}</span>`;
                }

                gridCharts[symbol] = buildChart(container, symbol, getDefaultBars());
            });
        </script>
    </body>
    </html>
    """
    
    template = Template(template_str)
    
    # Organize data for JS
    formatted_all_data = {}
    formatted_all_results = {}
    
    for symbol in SYMBOLS:
        symbol_data = all_results.get(symbol, {}).get('data', [])
        symbol_results = all_results.get(symbol, {}).get('results', [])
        
        # Format chart data and EMAs from table
        if symbol_data:
            chart_data = []
            ema9_data = []
            ema21_data = []
            
            # Sort by time ascending
            symbol_data.sort(key=lambda x: x['time'])
            
            for row in symbol_data:
                t = int(pd.to_datetime(row['time']).timestamp())
                chart_data.append({
                    "time": t,
                    "open": float(row['open']),
                    "high": float(row['high']),
                    "low": float(row['low']),
                    "close": float(row['close'])
                })
                # Use data from table columns
                if 'ema9' in row and row['ema9'] is not None:
                    ema9_data.append({"time": t, "value": float(row['ema9'])})
                if 'ema21' in row and row['ema21'] is not None:
                    ema21_data.append({"time": t, "value": float(row['ema21'])})
            
            formatted_all_data[symbol] = {
                "candles": chart_data,
                "ema9": ema9_data,
                "ema21": ema21_data
            }
        else:
            formatted_all_data[symbol] = {"candles": [], "ema9": [], "ema21": []}

        p2_list  = [z.get('accuracy', {}).get('phase2', {}) for z in symbol_results]
        active   = sum(1 for p in p2_list if p.get('outcome') == 'active')
        broken   = sum(1 for p in p2_list if p.get('outcome') == 'broken')
        untested = sum(1 for p in p2_list if p.get('outcome') == 'untested')
        formatted_all_results[symbol] = {
            "results": symbol_results,
            "last_bar_time": chart_data[-1]['time'] if chart_data else 0,
            "accuracy_summary": {
                "active": active,
                "broken": broken,
                "untested": untested
            }
        }

    # Build 10-char history strings for each symbol (used in chart headers)
    histories = {}
    for symbol in SYMBOLS:
        symbol_data = all_results.get(symbol, {}).get('data', [])
        symbol_results = all_results.get(symbol, {}).get('results', [])
        if symbol_data:
            sorted_data = sorted(symbol_data, key=lambda x: x['time'])
            bar_timestamps = [int(pd.to_datetime(row['time']).timestamp()) for row in sorted_data]
            histories[symbol] = build_history_string(bar_timestamps, symbol_results)
        else:
            histories[symbol] = '~~~~~~~~~~'

    html_content = template.render(
        symbols=SYMBOLS,
        results=formatted_all_results,
        histories=histories,
        now=now_str,
        gen_timestamp=int(datetime.now().timestamp()),
        all_data_json=json.dumps(formatted_all_data),
        all_results_json=json.dumps(formatted_all_results),
        symbols_json=json.dumps(SYMBOLS)
    )
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"Dashboard generated: {output_file}")


def send_consolidated_telegram(detections):
    """
    Sends a consolidated Telegram notification for all active S/R detections.
    Each symbol shows a 10-bar history string and a directional arrow emoji.
    """
    if not detections:
        print("No active S/R detections to notify.")
        return

    message = format_telegram_message(detections)

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Consolidated Telegram notification sent.")
        else:
            print(f"Failed to send consolidated Telegram: {response.text}")
    except Exception as e:
        print(f"Error sending consolidated Telegram: {e}")

def run_pipeline():
    config = load_config()
    all_results = {}
    active_detections = []
    
    print(f"Starting hourly pipeline for {len(SYMBOLS)} symbols...")
    
    for symbol in SYMBOLS:
        print(f"Processing {symbol}...")
        try:
            # 1. Fetch data
            data = fetch_ohlcv(symbol, timeframe="H1", limit=500)
            if not data:
                print(f"No data for {symbol}, skipping.")
                continue
            
            # Sort by time ascending
            df = pd.DataFrame(data)
            df['time'] = pd.to_datetime(df['time'])
            df = df.sort_values('time').reset_index(drop=True)
            
            # 2. Run backtest (sliding window)
            window_size = config.get("window", 200)
            nbars = config.get("nbars", 7)
            min_bars = config.get("min_bars", 5)
            threshold = config.get("threshold", 0.5)
            atr_period = config.get("atr_period", 21)
            wick = config.get("wick", 0.1)
            min_wick_bars = config.get("min_wick_bars", 2)
            
            symbol_results = []
            
            # For the dashboard, we only really care about detections in the most recent windows
            total_bars = len(df)
            if total_bars >= window_size:
                for i in range(total_bars - window_size + 1):
                    window_data = df.iloc[i : i + window_size].to_dict('records')
                    sr_result = detect_sr(
                        window_data, 
                        n_bars=nbars, 
                        threshold_factor=threshold, 
                        min_bars=min_bars,
                        atr_period=atr_period,
                        wick_percentage=wick,
                        min_wick_bars=min_wick_bars
                    )
                    if sr_result['result'] != 'nil':
                        sr_result['detected_at'] = int(window_data[-1]['time'].timestamp())
                        symbol_results.append(sr_result)
            
            for zone in symbol_results:
                zone['accuracy'] = evaluate_zone_accuracy(zone, df)

            all_results[symbol] = {
                "data": data,
                "results": symbol_results
            }
            
            # Check for latest bar detection (Active S/R)
            # Also verify the last bar is fresh (within 2h of now) to exclude closed markets
            if symbol_results:
                latest_detection = symbol_results[-1]
                last_bar_time = int(df['time'].iloc[-1].timestamp())
                now_utc = int(datetime.now(timezone.utc).timestamp())
                market_is_open = (now_utc - last_bar_time) <= 7200  # 2 hours = one H1 bar
                if latest_detection['detected_at'] == last_bar_time and market_is_open:
                    bar_timestamps = [int(t.timestamp()) for t in df['time']]
                    history = build_history_string(bar_timestamps, symbol_results)
                    active_detections.append({
                        "symbol": symbol,
                        "result": latest_detection['result'],
                        "history": history
                    })

            print(f"Done {symbol}. Found {len(symbol_results)} S/R zones.")
            
        except Exception as e:
            print(f"Error processing {symbol}: {e}")
            
    # 3. Cache results for fast regen
    os.makedirs("data", exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f)
    print(f"Pipeline cache saved: {CACHE_FILE}")

    # 4. Generate Dashboard
    generate_dashboard(all_results, config)

    # 5. Send Consolidated Notification
    send_consolidated_telegram(active_detections)

def regen_dashboard():
    if not os.path.exists(CACHE_FILE):
        print(f"No cache found at {CACHE_FILE}. Run the full pipeline first.")
        return
    config = load_config()
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        all_results = json.load(f)
    generate_dashboard(all_results, config)
    print("Dashboard regenerated from cache.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--regen", action="store_true", help="Regenerate dashboard from cached data without running the pipeline")
    args = parser.parse_args()
    if args.regen:
        regen_dashboard()
    else:
        run_pipeline()
