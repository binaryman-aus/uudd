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

    filled      = False
    entry_price = None
    p1_outcome  = 'untested'
    p1_time     = None
    p1_entry    = None
    p2_outcome  = 'untested'
    p2_mag      = None

    # Phase 1: only check the very next bar to decide fill/break/untested
    if len(future) > 0:
        bar = future.iloc[0]
        if zone_type == 'support':
            if bar['low'] > z_high:
                pass                                   # price never reached zone — untested
            elif bar['high'] < z_high:
                if bar['low'] < z_low:                 # gapped through entire zone
                    entry_price = z_high               # virtual entry at zone top
                    p1_outcome = 'break'; p1_time = int(bar['time'].timestamp()); p1_entry = 'gap'
                    p2_outcome = 'broken'; p2_mag = 0.0
                else:
                    pass                               # gapped into zone only — untested
            else:                                      # straddles z_high: limit fills
                entry_price = min(bar['open'], z_high) # if open below z_high, enter at open
                p1_time = int(bar['time'].timestamp()); p1_entry = 'valid'
                if bar['low'] < z_low:                 # SL hit on fill bar
                    p1_outcome = 'break'; p2_outcome = 'broken'; p2_mag = 0.0
                else:
                    p1_outcome = 'bounce'; filled = True; p2_outcome = 'active'; p2_mag = 0.0
        else:                                          # resistance
            if bar['high'] < z_low:
                pass                                   # price never reached zone — untested
            elif bar['low'] > z_low:
                if bar['high'] > z_high:               # gapped through entire zone
                    entry_price = z_low                # virtual entry at zone bottom
                    p1_outcome = 'break'; p1_time = int(bar['time'].timestamp()); p1_entry = 'gap'
                    p2_outcome = 'broken'; p2_mag = 0.0
                else:
                    pass                               # gapped into zone only — untested
            else:                                      # straddles z_low: limit fills
                entry_price = max(bar['open'], z_low)  # if open above z_low, enter at open
                p1_time = int(bar['time'].timestamp()); p1_entry = 'valid'
                if bar['high'] > z_high:               # SL hit on fill bar
                    p1_outcome = 'break'; p2_outcome = 'broken'; p2_mag = 0.0
                else:
                    p1_outcome = 'bounce'; filled = True; p2_outcome = 'active'; p2_mag = 0.0

    # Phase 2: track excursion and SL (includes fill bar for excursion capture)
    if filled:
        risk = (entry_price - z_low) if zone_type == 'support' else (z_high - entry_price)
        risk = risk if risk > 0 else z_range
        for _, bar in future.iterrows():
            if zone_type == 'support':
                if bar['high'] > entry_price:
                    p2_mag = max(p2_mag, (bar['high'] - entry_price) / risk)
                if bar['low'] < z_low:
                    p2_outcome = 'broken'; break
            else:
                if bar['low'] < entry_price:
                    p2_mag = max(p2_mag, (entry_price - bar['low']) / risk)
                if bar['high'] > z_high:
                    p2_outcome = 'broken'; break

    return {
        'outcome': p1_outcome,
        'test_bar_time': p1_time,
        'entry': p1_entry,
        'entry_price': round(entry_price, 2) if entry_price is not None else None,
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
            /* ── Accuracy Panel ──────────────────────────────────────────── */
            #fs-accuracy-panel {
                background: #fff; color: #333;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                font-size: 0.92em; border-left: 1px solid #e0e0e0;
            }
            .ap-section { padding: 10px 12px; border-bottom: 1px solid #eee; }
            .ap-title {
                font-size: 0.72em; font-weight: 700; text-transform: uppercase;
                letter-spacing: 0.1em; color: #aaa; margin-bottom: 8px;
            }
            .ap-input {
                background: #f7f7f7; border: 1px solid #ddd; border-radius: 4px;
                color: #333; font-family: monospace; font-size: 0.9em;
                padding: 3px 5px; width: 46px; text-align: right;
                transition: border-color 0.15s; -moz-appearance: textfield;
            }
            .ap-input::-webkit-outer-spin-button,
            .ap-input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
            .ap-input:focus { outline: none; border-color: #2196F3; background: #fff; }
            .ap-total {
                font-size: 0.78em; font-weight: 700; padding: 2px 8px; border-radius: 10px;
            }
            .ap-total.valid   { background: rgba(38,166,154,0.12); color: #1a9188; }
            .ap-total.invalid { background: rgba(239,83,80,0.12);  color: #d32f2f; }
            .ap-pnl-big {
                text-align: center; font-size: 1.5em; font-weight: 700;
                font-family: monospace; padding: 6px 0 2px;
            }
            .ap-badge {
                display: inline-flex; align-items: center; gap: 3px;
                padding: 2px 8px; border-radius: 10px; font-size: 0.82em; font-weight: 600;
            }
            .ap-badge.grn { background: rgba(38,166,154,0.10); color: #1a9188; }
            .ap-badge.red { background: rgba(239,83,80,0.10);  color: #d32f2f; }
            .ap-badge.gry { background: rgba(0,0,0,0.06); color: #888; }
            .ap-badge-row { display: flex; gap: 5px; flex-wrap: wrap; padding: 2px 0; }
            .acc-zone-row {
                padding: 6px 10px 5px 12px; border-bottom: 1px solid #f0f0f0;
                cursor: default; border-left: 3px solid #e0e0e0;
            }
            .acc-zone-row.out-active   { border-left-color: #26a69a; }
            .acc-zone-row.out-broken   { border-left-color: #ef5350; }
            .acc-zone-row.out-untested { border-left-color: #ddd; }
            .acc-zone-row.highlighted  { background: #fffde7 !important; }
            .zone-dir {
                font-size: 0.74em; font-weight: 700; padding: 1px 4px;
                border-radius: 3px; margin-right: 3px;
            }
            .zone-dir.sup { background: rgba(38,166,154,0.12); color: #1a9188; }
            .zone-dir.res { background: rgba(239,83,80,0.12);  color: #d32f2f; }
            .zone-price { font-family: monospace; color: #222; font-size: 0.95em; }
            .zone-timestamp { font-size: 0.78em; color: #bbb; }
            .zone-mag   { font-family: monospace; font-size: 0.85em; color: #aaa; }
            .zone-status {
                font-size: 0.7em; font-weight: 700; padding: 1px 6px; border-radius: 3px;
                letter-spacing: 0.02em;
            }
            .zone-status.st-open     { background: rgba(38,166,154,0.10); color: #1a9188; }
            .zone-status.st-closed   { background: rgba(38,166,154,0.18); color: #0d7a6f; }
            .zone-status.st-sl       { background: rgba(239,83,80,0.10);  color: #d32f2f; }
            .zone-status.st-untested { background: rgba(0,0,0,0.04);      color: #bbb; }
            .zone-tp-detail { padding: 4px 10px 6px 12px; display: none; }
            .zone-tp-detail.visible { display: block; }
            .zone-detail-row {
                display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
            }
            .zone-tp-row { display: flex; gap: 4px; flex-wrap: wrap; }
            .tp-pill {
                font-size: 0.72em; font-weight: 700; padding: 1px 6px; border-radius: 3px;
                font-family: monospace; letter-spacing: 0.03em;
            }
            .tp-pill.hit  { background: rgba(38,166,154,0.12); color: #1a9188; }
            .tp-pill.miss { background: rgba(239,83,80,0.10);  color: #d32f2f; }
            .tp-pill.open { background: rgba(0,0,0,0.05);      color: #999; }
            .tp-pill.sl   { background: rgba(239,83,80,0.15);  color: #c62828; font-style: italic; }
            .zone-pnl-row {
                font-size: 0.78em; color: #5a6a8a; display: flex; gap: 12px;
            }
            .zone-pnl-lbl { color: #bbb; margin-right: 4px; }
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
                <div id="fs-accuracy-panel" style="width:450px;overflow-y:auto;flex-shrink:0;">
                    <div id="fs-tp-section"></div>
                    <div id="fs-tp-results"></div>
                    <div id="fs-accuracy-summary"></div>
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
            let currentPanelSymbol = null;
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

            // ── Phase 3: TP simulator ─────────────────────────────────────
            function defaultTps() {
                return [{mag:1.0, pct:50}, {mag:2.0, pct:30}, {mag:4.0, pct:20}];
            }
            function getTpConfig() {
                try { const s = localStorage.getItem('tpConfig'); return s ? JSON.parse(s) : null; }
                catch { return null; }
            }
            function saveTpConfig(tps) {
                try { localStorage.setItem('tpConfig', JSON.stringify(tps)); } catch {}
            }
            function readTpInputs() {
                return [1,2,3].map(i => ({
                    mag: parseFloat(document.getElementById('tp-mag-'+i)?.value) || 0,
                    pct: parseFloat(document.getElementById('tp-pct-'+i)?.value) || 0
                }));
            }
            function calcZonePnL(zone, tps) {
                const p2 = zone.accuracy?.phase2;
                if (!p2 || p2.outcome === 'untested') return null;
                const mag    = p2.max_magnitude ?? 0;
                const broken = p2.outcome === 'broken';
                const active = p2.outcome === 'active';
                let realizedPnl = 0, unrealizedPnl = 0;
                for (const tp of tps) {
                    const frac = tp.pct / 100;
                    if (mag >= tp.mag)   realizedPnl   += frac * tp.mag;
                    else if (broken)     realizedPnl   += frac * (-1.0);
                    else                 unrealizedPnl += frac * mag;   // open at max excursion
                }
                return {realizedPnl, unrealizedPnl, totalPnl: realizedPnl + unrealizedPnl, isActive: active};
            }
            function fmtPnl(v, mono) {
                const s = (v >= 0 ? '+' : '') + v.toFixed(2) + 'x';
                const c = v >= 0 ? '#26a69a' : '#ef5350';
                return mono
                    ? '<span style="color:'+c+';font-family:monospace;font-weight:700;">'+s+'</span>'
                    : '<span style="color:'+c+';">'+s+'</span>';
            }
            function updateTPResults(symbol, tps) {
                const total   = tps.reduce((s,t) => s + t.pct, 0);
                const valid   = Math.abs(total - 100) < 0.01;
                const totalEl = document.getElementById('fs-tp-total');
                if (totalEl) {
                    totalEl.className = 'ap-total ' + (valid ? 'valid' : 'invalid');
                    totalEl.innerHTML = valid ? '&#x2713;&nbsp;100%' : 'Total: ' + total + '%';
                }
                const resultsEl = document.getElementById('fs-tp-results');
                if (!valid) {
                    if (resultsEl) resultsEl.innerHTML =
                        '<div class="ap-section" style="color:#ef5350;font-size:0.85em;">Position % must sum to 100%</div>';
                    document.querySelectorAll('.zone-pnl').forEach(el => el.innerHTML = '');
                    document.querySelectorAll('.zone-tp-detail').forEach(el => { el.innerHTML = ''; el.classList.remove('visible'); });
                    return;
                }
                const zones = (allResults[symbol]?.results || []).filter(z => z.result !== 'nil');
                let sumRealized = 0, sumUnrealized = 0, wins = 0, losses = 0, filled = 0, untestedCount = 0;
                const brokenMags = [], closedMags = [], openMags = [];
                const brokenPnls = [], closedPnls = [], openPnls = [];
                zones.forEach(z => {
                    const r      = calcZonePnL(z, tps);
                    const detail = document.querySelector('.zone-tp-detail[data-dat="' + z.detected_at + '"]');
                    if (!r) {
                        untestedCount++;
                        if (detail) { detail.innerHTML = ''; detail.classList.remove('visible'); }
                        return;
                    }
                    filled++;
                    sumRealized   += r.realizedPnl;
                    sumUnrealized += r.unrealizedPnl;
                    const mag       = z.accuracy?.phase2?.max_magnitude ?? 0;
                    const broken    = z.accuracy?.phase2?.outcome === 'broken';
                    const allTpsHit = tps.every(tp => mag >= tp.mag);
                    if (broken)          { brokenMags.push(mag); brokenPnls.push(r.totalPnl); }
                    else if (allTpsHit)  { closedMags.push(mag); closedPnls.push(r.totalPnl); }
                    else if (r.isActive) { openMags.push(mag);   openPnls.push(r.totalPnl);   }
                    const hasOpen = r.isActive && r.unrealizedPnl !== 0;
                    if (r.totalPnl > 0) wins++; else losses++;
                         // TP pills + P&L breakdown in detail row
                     if (detail) {
                         let pills = '';
                         tps.forEach((tp, i) => {
                             if (mag >= tp.mag) {
                                 pills += '<span class="tp-pill hit">TP'+(i+1)+' \u2713</span>';
                             } else if (broken) {
                                 pills += '<span class="tp-pill miss">TP'+(i+1)+' \u2717</span>';
                             } else {
                                 pills += '<span class="tp-pill open">TP'+(i+1)+' \u007E</span>';
                             }
                         });
                         if (broken) pills += '<span class="tp-pill sl">SL</span>';
                         // Update status badge to "Closed" when all TPs hit on active zone
                         const statusEl = document.querySelector('.zone-status[data-dat-status="' + z.detected_at + '"]');
                         if (statusEl && !broken && allTpsHit) {
                             statusEl.textContent = 'Closed';
                             statusEl.className = 'zone-status st-closed';
                         }
                          const rStr = fmtPnl(r.realizedPnl, false);
                          const uStr = r.unrealizedPnl !== 0
                              ? fmtPnl(r.unrealizedPnl, false)
                              : '<span style="color:#ccc;">&#8209;</span>';
                          detail.innerHTML =
                              '<div class="zone-detail-row">' +
                              '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">' +
                              '<div class="zone-tp-row" style="display:flex;align-items:center;flex:1;min-width:150px;">' + pills + '</div>' +
                              '<div class="zone-pnl-row" style="display:flex;align-items:center;flex:1;min-width:150px;text-align:right;">' +
                              '<span><span class="zone-pnl-lbl">Real.</span>' + rStr + '</span>' +
                              '<span><span class="zone-pnl-lbl">Unrreal.</span>' + uStr + '</span>' +
                              '</div>' +
                              '</div>' +
                              '</div>';
                         detail.classList.add('visible');
                     }
                });
                const totalZones = filled + untestedCount;
                const dash = '\u2014';
                function avgPnl(pnls) {
                    return pnls.length ? pnls.reduce((s,v) => s+v, 0) / pnls.length : null;
                }
                function pnlCell(val) {
                    const s = val !== null ? fmtPnl(val, false) : '<span style="color:#ccc;">'+dash+'</span>';
                    return '<td style="padding:2px 0 2px 6px;font-family:monospace;text-align:right;vertical-align:middle;">'+s+'</td>';
                }
                // TP hit-rate cells for a group
                function tpRateCells(mags) {
                    if (!mags.length) return tps.map(() => '<td style="padding:2px 6px 2px 0;font-family:monospace;font-size:0.8em;color:#ddd;vertical-align:middle;">—</td>').join('');
                    return tps.map((tp, i) => {
                        const pct = Math.round(mags.filter(m => m >= tp.mag).length / mags.length * 100);
                        const c   = pct >= 50 ? '#1a9188' : (pct >= 25 ? '#5a6a8a' : '#bbb');
                        return '<td style="padding:2px 6px 2px 0;font-family:monospace;font-size:0.8em;color:'+c+';vertical-align:middle;">'+pct+'%</td>';
                    }).join('');
                }
                function groupRow(label, labelColor, mags, pnls, total) {
                    const cnt    = mags.length;
                    const rowPct = total > 0 ? Math.round(cnt / total * 100) : 0;
                    return '<tr>' +
                        '<td style="padding:4px 8px 4px 0;white-space:nowrap;vertical-align:middle;">' +
                        '<span style="font-size:0.74em;font-weight:700;color:'+labelColor+';">'+label+'</span>' +
                        '</td>' +
                        '<td style="padding:4px 8px 4px 0;font-family:monospace;font-size:0.82em;color:#333;white-space:nowrap;vertical-align:middle;">' +
                        cnt + ' <span style="color:#aaa;font-size:0.88em;">('+rowPct+'%)</span>' +
                        '</td>' +
                        tpRateCells(mags) +
                        pnlCell(avgPnl(pnls)) +
                        '</tr>';
                }
                const untestedTpCells = tps.map(() =>
                    '<td style="padding:2px 6px 2px 0;font-family:monospace;font-size:0.8em;color:#ddd;vertical-align:middle;">—</td>'
                ).join('');
                const untestedPct = totalZones > 0 ? Math.round(untestedCount / totalZones * 100) : 0;
                const untestedRow = '<tr>' +
                    '<td style="padding:4px 8px 4px 0;vertical-align:middle;"><span style="font-size:0.74em;font-weight:700;color:#ccc;">Untested</span></td>' +
                    '<td style="padding:4px 8px 4px 0;font-family:monospace;font-size:0.82em;color:#333;vertical-align:middle;">' +
                    untestedCount + ' <span style="color:#aaa;font-size:0.88em;">('+untestedPct+'%)</span></td>' +
                    untestedTpCells +
                    '<td style="padding:2px 0 2px 6px;color:#ddd;text-align:right;vertical-align:middle;">'+dash+'</td>' +
                    '</tr>';
                const totalPnl  = filled > 0 ? (sumRealized + sumUnrealized) / filled : null;
                const totalTpCells = tps.map((tp, i) => {
                    const allMags = [...brokenMags, ...closedMags, ...openMags];
                    const pct = allMags.length ? Math.round(allMags.filter(m => m >= tp.mag).length / allMags.length * 100) : 0;
                    const c   = pct >= 50 ? '#1a9188' : (pct >= 25 ? '#5a6a8a' : '#bbb');
                    return '<td style="padding:2px 6px 2px 0;font-family:monospace;font-size:0.8em;color:'+c+';vertical-align:middle;">'+pct+'%</td>';
                }).join('');
                const totalRow = '<tr style="border-top:1px solid #eee;">' +
                    '<td style="padding:6px 8px 4px 0;vertical-align:middle;"><span style="font-size:0.74em;font-weight:700;color:#333;">Total</span></td>' +
                    '<td style="padding:6px 8px 4px 0;font-family:monospace;font-size:0.82em;font-weight:700;color:#333;vertical-align:middle;">' + totalZones + '</td>' +
                    totalTpCells +
                    pnlCell(totalPnl) +
                    '</tr>';
                const headerRow = '<tr style="border-bottom:1px solid #ddd;">' +
                    '<td style="padding:4px 8px 4px 0;font-size:0.68em;font-weight:600;color:#888;vertical-align:middle;">Case</td>' +
                    '<td style="padding:4px 8px 4px 0;font-size:0.68em;font-weight:600;color:#888;vertical-align:middle;"># Zones</td>' +
                    tps.map((_, i) => '<td style="padding:4px 6px 4px 0;font-size:0.68em;font-weight:600;color:#888;vertical-align:middle;">TP'+(i+1)+'</td>').join('') +
                    '<td style="padding:4px 0 4px 6px;font-size:0.68em;font-weight:600;color:#888;text-align:right;vertical-align:middle;">P&amp;L</td>' +
                    '</tr>';
                if (resultsEl) resultsEl.innerHTML =
                    '<div class="ap-section">' +
                    '<table style="width:100%;border-collapse:collapse;">' +
                    headerRow +
                    groupRow('Hit SL',   '#d32f2f', brokenMags, brokenPnls, totalZones) +
                    groupRow('Closed',   '#1a9188', closedMags, closedPnls, totalZones) +
                    groupRow('Open',     '#888',    openMags,   openPnls,   totalZones) +
                    untestedRow +
                    totalRow +
                    '</table>' +
                    '</div>';
            }
            function onTpChange() {
                const tps = readTpInputs();
                saveTpConfig(tps);
                if (currentPanelSymbol) updateTPResults(currentPanelSymbol, tps);
            }
            function renderTPSection() {
                const tps   = getTpConfig() || defaultTps();
                const total = tps.reduce((s,t) => s + t.pct, 0);
                const valid = Math.abs(total - 100) < 0.01;
                const rows  = tps.map((tp, i) =>
                    '<tr>' +
                    '<td style="padding:4px 6px;color:#4a5a8a;font-size:0.8em;font-weight:700;letter-spacing:0.05em;">TP'+(i+1)+'</td>' +
                    '<td style="padding:4px 4px;"><input id="tp-mag-'+(i+1)+'" class="ap-input" type="number" value="'+tp.mag+'" min="0.1" step="0.1" oninput="onTpChange()"> <span style="color:#3e4e70;font-size:0.82em;">x</span></td>' +
                    '<td style="padding:4px 4px;"><input id="tp-pct-'+(i+1)+'" class="ap-input" type="number" value="'+tp.pct+'" min="0" max="100" step="1" oninput="onTpChange()"> <span style="color:#3e4e70;font-size:0.82em;">%</span></td>' +
                    '</tr>'
                ).join('');
                document.getElementById('fs-tp-section').innerHTML =
                    '<div class="ap-section">' +
                    '<div class="ap-title">Strategy Simulator</div>' +
                    '<table style="width:100%;border-collapse:collapse;">' +
                    '<tr style="font-size:0.74em;color:#354060;">' +
                    '<td></td><td style="padding:2px 4px;">Target</td><td style="padding:2px 4px;">Size</td>' +
                    '</tr>' +
                    rows + '</table>' +
                    '<div style="text-align:right;margin-top:7px;">' +
                    '<span id="fs-tp-total" class="ap-total '+(valid?'valid':'invalid')+'">' +
                    (valid ? '&#x2713;&nbsp;100%' : 'Total: '+total+'%') +
                    '</span></div></div>';
            }
            // ──────────────────────────────────────────────────────────────

            function renderAccuracyPanel(symbol) {
                const data    = allResults[symbol];
                if (!data) return;
                currentPanelSymbol = symbol;
                const summary = data.accuracy_summary || {};
                const zones   = (data.results || []).filter(z => z.result !== 'nil');

                renderTPSection();

                document.getElementById('fs-accuracy-summary').innerHTML =
                    '<div class="ap-section">' +
                    '<div class="ap-title">Zone Performance</div>' +
                    '</div>';

                const MONTHS  = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                function fmtTs(ts) {
                    const d = new Date(ts * 1000);
                    const p = n => String(n).padStart(2, '0');
                    return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
                }
                document.getElementById('fs-accuracy-list').innerHTML = [...zones].reverse().map(z => {
                    const acc      = z.accuracy || {};
                    const p2       = acc.phase2 || { outcome: 'untested', max_magnitude: null };
                    const isSupport = z.result === 'support';
                    const isFilled  = p2.outcome === 'active' || p2.outcome === 'broken';
                    const price    = isFilled && acc.entry_price != null
                                     ? `${fmtPrice(acc.entry_price)}\u00d7${isSupport ? fmtPrice(z.price_range.low) : fmtPrice(z.price_range.high)}`
                                     : `${fmtPrice(z.price_range.low)}\u2013${fmtPrice(z.price_range.high)}`;
                    const mag      = p2.max_magnitude !== null && p2.max_magnitude !== undefined
                                     ? `<span class="zone-mag">${p2.max_magnitude}x</span>` : '';
                    const statusCls = p2.outcome === 'broken' ? 'st-sl'
                                    : p2.outcome === 'active' ? 'st-open' : 'st-untested';
                    const statusLbl = p2.outcome === 'broken' ? 'Hit SL'
                                    : p2.outcome === 'active' ? 'Open' : 'Untested';
                    const gap      = acc.entry === 'gap'
                                     ? ' <span style="color:#354060;font-size:0.78em;">gap</span>' : '';
                    const detected = fmtTs(z.detected_at);
                    const startTs  = Math.floor(new Date(z.start_time).getTime() / 1000);
                    const endTs    = Math.floor(new Date(z.end_time).getTime() / 1000);
                    return `<div class="acc-zone-row out-${p2.outcome}" data-low="${z.price_range.low}" data-high="${z.price_range.high}" data-start="${startTs}" data-end="${endTs}">
                        <div style="display:flex;align-items:center;justify-content:space-between;">
                            <span style="display:flex;align-items:center;gap:6px;"><span class="zone-dir ${isSupport?'sup':'res'}">${isSupport?'\u25B2 S':'\u25BC R'}</span><span class="zone-timestamp">${detected}</span><span class="zone-price">${price}</span>${gap}</span>
                            <span style="display:flex;align-items:center;gap:6px;">${mag}<span class="zone-status ${statusCls}" data-dat-status="${z.detected_at}">${statusLbl}</span></span>
                        </div>
                        <div class="zone-tp-detail" data-dat="${z.detected_at}"></div>
                    </div>`;
                }).join('');

                updateTPResults(symbol, getTpConfig() || defaultTps());
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
                    const color = z.result === 'support'
                        ? (p2.outcome === 'active' ? 'rgba(38,166,154,0.35)' : 'rgba(38,166,154,0.85)')
                        : (p2.outcome === 'active' ? 'rgba(239,83,80,0.35)'  : 'rgba(239,83,80,0.85)');
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
                            rows.forEach(el => { el.classList.remove('highlighted'); el.style.display = ''; });
                            return;
                        }
                        const price = fsCandleSeriesRef ? fsCandleSeriesRef.coordinateToPrice(param.point.y) : null;
                        if (price === null) { rows.forEach(el => { el.classList.remove('highlighted'); el.style.display = ''; }); return; }
                        const t = typeof param.time === 'number' ? param.time : Math.floor(new Date(param.time).getTime() / 1000);
                        const matched = new Set();
                        rows.forEach(el => {
                            const lo    = parseFloat(el.dataset.low);
                            const hi    = parseFloat(el.dataset.high);
                            const start = parseInt(el.dataset.start);
                            const end   = parseInt(el.dataset.end);
                            if (price >= lo && price <= hi && t >= start && t <= end) matched.add(el);
                        });
                        rows.forEach(el => {
                            el.classList.toggle('highlighted', matched.has(el));
                            el.style.display = matched.size > 0 && !matched.has(el) ? 'none' : '';
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
