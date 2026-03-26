import argparse
import json
import os
import pandas as pd
from datetime import datetime
from jinja2 import Template
import requests
from fetch_ohlcv import fetch_ohlcv
from sr_detect import detect_sr, load_config

CACHE_FILE = "data/pipeline_cache.json"

SYMBOLS = ["US500", "GER40", "JP225", "USOIL", "XAUUSD", "BTCUSD", "AUDUSD", "EURUSD", "USDJPY"]
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "REDACTED")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "REDACTED")

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
            /* Phone-only layout: touch input + no hover = phone browser */
            @media (pointer: coarse) and (hover: none) {
                html, body {
                    overflow: auto;
                }
                .grid-container {
                    grid-template-columns: 1fr;
                    grid-template-rows: none;
                    height: auto;
                    overflow: visible;
                }
                .chart-box {
                    height: 600px !important;
                    margin-bottom: 10px;
                }
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
                justify-content: space-between;
                cursor: pointer;
            }
            .chart-header:hover {
                background: #ddd;
            }
            .chart-container {
                flex-grow: 1;
                position: relative;
            }
            .sr-label {
                font-size: 0.8em;
            }
            .support { color: green; }
            .resistance { color: red; }
        </style>
    </head>
    <body>
        <div class="dashboard-header">
            <div><strong>S/R Multi-Symbol Dashboard</strong> | H1 Timeframe | Last 150 Bars</div>
            <div id="dashboard-generated">Generated: {{ now }}</div>
        </div>
        <div class="grid-container">
            {% for symbol in symbols %}
            <div id="box-{{ loop.index }}" class="chart-box">
                <div class="chart-header" onclick="openFullscreen('{{ symbol }}')">
                    <span id="title-{{ symbol }}">{{ symbol }} 🔍</span>
                    {% if results[symbol] and results[symbol].results %}
                        {% set latest = results[symbol].results[-1] %}
                        {% if latest.detected_at == results[symbol].last_bar_time %}
                            <span class="sr-label {{ latest.result }}">
                                {{ latest.result.upper() }} @ {{ "%.2f"|format(latest.price_range.low) }}-{{ "%.2f"|format(latest.price_range.high) }}
                            </span>
                        {% else %}
                            <span style="color: #999;">No S/R Detected</span>
                        {% endif %}
                    {% else %}
                        <span style="color: #999;">No S/R Detected</span>
                    {% endif %}
                </div>
                <div id="chart-{{ loop.index }}" class="chart-container"></div>
            </div>
            {% endfor %}
        </div>

        <!-- Fullscreen overlay: fresh chart instance so grid charts are never resized/corrupted -->
        <div id="fs-overlay" style="display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:9999;background:white;flex-direction:column;">
            <div id="fs-header" style="background:#eee;padding:4px 12px;font-size:0.85em;font-weight:bold;border-bottom:1px solid #ddd;display:flex;justify-content:space-between;align-items:center;flex-shrink:0;cursor:pointer;" onclick="closeFullscreen()">
                <span id="fs-title"></span>
                <span style="font-size:1.1em;padding:2px 8px;background:#ccc;border-radius:3px;">&#x2715; Close</span>
            </div>
            <div id="fs-chart-container" style="flex:1;position:relative;min-height:0;"></div>
        </div>

        <script>
            const allData = {{ all_data_json }};
            const allResults = {{ all_results_json }};
            const symbols = {{ symbols_json }};

            const genTime = {{ gen_timestamp }};
            if (genTime > 0) {
                const genDate = new Date(genTime * 1000);
                document.getElementById('dashboard-generated').innerHTML =
                    `Generated: <span style="font-weight:normal;">UTC: ${genDate.toUTCString().replace(' GMT', '')} | Local: ${genDate.toLocaleString()}</span>`;
            }

            const isPhone = window.matchMedia('(pointer: coarse) and (hover: none)').matches;

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

                chart.addCandlestickSeries({
                    upColor: '#26a69a', downColor: '#ef5350',
                    borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350',
                }).setData(chartData);

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

            function openFullscreen(symbol) {
                const overlay   = document.getElementById('fs-overlay');
                const container = document.getElementById('fs-chart-container');
                if (fsChartInstance) { fsChartInstance.remove(); fsChartInstance = null; }
                container.innerHTML = '';
                document.getElementById('fs-title').textContent = symbol + ' \u2014 click header or press Esc to close';
                overlay.style.display = 'flex';
                document.body.style.overflow = 'hidden';
                fsChartInstance = buildChart(container, symbol, 500, FS_CHART_OPTS);
            }

            function closeFullscreen() {
                if (fsChartInstance) { fsChartInstance.remove(); fsChartInstance = null; }
                document.getElementById('fs-chart-container').innerHTML = '';
                document.getElementById('fs-overlay').style.display = 'none';
                document.body.style.overflow = '';
            }

            document.addEventListener('keydown', e => { if (e.key === 'Escape') closeFullscreen(); });

            symbols.forEach((symbol, index) => {
                const container   = document.getElementById(`chart-${index + 1}`);
                const lastBarTime = (allResults[symbol] && allResults[symbol].last_bar_time) || 0;

                if (lastBarTime > 0) {
                    const date       = new Date(lastBarTime * 1000);
                    const utcStr     = date.toUTCString().replace(' GMT', '');
                    const localStr   = date.toLocaleString();
                    const nowTs      = Math.floor(Date.now() / 1000);
                    const isOutdated = (nowTs - lastBarTime) > (3600 * 2);
                    const warning    = isOutdated
                        ? ' <span style="color:white;background:#ef5350;padding:1px 4px;border-radius:3px;font-weight:bold;font-size:0.8em;margin-left:5px;">\u26a0\ufe0f OUTDATED</span>'
                        : '';
                    document.getElementById(`title-${symbol}`).innerHTML =
                        `${symbol}${warning} &#x1F50D; | <span style="font-weight:normal;font-size:0.85em;color:#666;">UTC: ${utcStr} | Local: ${localStr}</span>`;
                }

                buildChart(container, symbol, isPhone ? 100 : 150);
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

        formatted_all_results[symbol] = {
            "results": symbol_results,
            "last_bar_time": chart_data[-1]['time'] if chart_data else 0
        }
        
    html_content = template.render(
        symbols=SYMBOLS,
        results=formatted_all_results,
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
    Sends a consolidated Telegram notification for all active S/R levels.
    """
    if not detections:
        print("No active S/R detections to notify.")
        return

    message = "🚨 *S/R DASHBOARD ALERT* 🚨\n\n"
    for det in detections:
        message += (
            f"📍 *{det['symbol']}*: {det['result'].upper()}\n"
            f"   Range: `{det['range_low']:.2f} - {det['range_high']:.2f}`\n\n"
        )
    
    message += "[View Live Dashboard](https://binaryman-aus.github.io/uudd/)"
    
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
                        wick_percentage=wick
                    )
                    if sr_result['result'] != 'nil':
                        sr_result['detected_at'] = int(window_data[-1]['time'].timestamp())
                        symbol_results.append(sr_result)
            
            all_results[symbol] = {
                "data": data,
                "results": symbol_results
            }
            
            # Check for latest bar detection (Active S/R)
            if symbol_results:
                latest_detection = symbol_results[-1]
                last_bar_time = int(df['time'].iloc[-1].timestamp())
                if latest_detection['detected_at'] == last_bar_time:
                    active_detections.append({
                        "symbol": symbol,
                        "result": latest_detection['result'],
                        "range_low": latest_detection['price_range']['low'],
                        "range_high": latest_detection['price_range']['high']
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
