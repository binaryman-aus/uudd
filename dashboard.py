import json
import os
import pandas as pd
from datetime import datetime
from jinja2 import Template
import requests
from fetch_ohlcv import fetch_ohlcv
from sr_detect import detect_sr, load_config

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
            /* Mobile Responsiveness */
            @media (max-width: 768px) {
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
                    height: 400px !important;
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
                transition: all 0.2s ease-in-out;
            }
            .chart-box.fullscreen {
                position: fixed !important;
                top: 0 !important;
                left: 0 !important;
                width: 100vw !important;
                height: 100vh !important;
                z-index: 9999 !important;
                border: none !important;
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
            <div><strong>S/R Multi-Symbol Dashboard</strong> | H1 Timeframe | Last 500 Bars</div>
            <div id="dashboard-generated">Generated: {{ now }}</div>
        </div>
        <div class="grid-container">
            {% for symbol in symbols %}
            <div id="box-{{ loop.index }}" class="chart-box">
                <div class="chart-header" onclick="toggleFullscreen('{{ symbol }}', 'box-{{ loop.index }}')">
                    <span id="title-{{ symbol }}">{{ symbol }} 🔍</span>
                    {% if results[symbol] and results[symbol].results %}
                        {% set latest = results[symbol].results[-1] %}
                        <span class="sr-label {{ latest.result }}">
                            {{ latest.result.upper() }} @ {{ "%.2f"|format(latest.price_range.low) }}-{{ "%.2f"|format(latest.price_range.high) }}
                        </span>
                    {% else %}
                        <span style="color: #999;">No S/R Detected</span>
                    {% endif %}
                </div>
                <div id="chart-{{ loop.index }}" class="chart-container"></div>
            </div>
            {% endfor %}
        </div>

        <script>
            const allData = {{ all_data_json }};
            const allResults = {{ all_results_json }};
            const symbols = {{ symbols_json }};

            // Update Dashboard Generated Time
            const genTime = {{ gen_timestamp }};
            if (genTime > 0) {
                const genDate = new Date(genTime * 1000);
                document.getElementById('dashboard-generated').innerHTML = 
                    `Generated: <span style="font-weight: normal;">UTC: ${genDate.toUTCString().replace(' GMT', '')} | Local: ${genDate.toLocaleString()}</span>`;
            }

            const charts = {};

            function toggleFullscreen(symbol, boxId) {
                const box = document.getElementById(boxId);
                const isFullscreen = box.classList.toggle('fullscreen');
                
                // Hide/Show body scroll
                document.body.style.overflow = isFullscreen ? 'hidden' : '';
                
                // Resize chart to fit new container size
                if (charts[symbol]) {
                    setTimeout(() => {
                        charts[symbol].resize(box.clientWidth, box.clientHeight - 30);
                        charts[symbol].timeScale().fitContent(); // Reset zoom to show all data
                    }, 50);
                }
            }

            symbols.forEach((symbol, index) => {
                const containerId = `chart-${index + 1}`;
                const container = document.getElementById(containerId);
                const symbolData = allData[symbol] || { candles: [], ema9: [], ema21: [] };
                const chartData = symbolData.candles;
                const srResults = (allResults[symbol] && allResults[symbol].results) || [];
                const lastBarTime = (allResults[symbol] && allResults[symbol].last_bar_time) || 0;

                if (chartData.length === 0) return;

                // Update Title with Time & Warning
                if (lastBarTime > 0) {
                    const date = new Date(lastBarTime * 1000);
                    const utcStr = date.toUTCString().replace(' GMT', '');
                    const localStr = date.toLocaleString();
                    
                    const nowTs = Math.floor(Date.now() / 1000);
                    const isOutdated = (nowTs - lastBarTime) > (3600 * 2);
                    const warning = isOutdated ? ' <span style="color: white; background: #ef5350; padding: 1px 4px; border-radius: 3px; font-weight: bold; font-size: 0.8em; margin-left: 5px;">⚠️ OUTDATED</span>' : '';

                    document.getElementById(`title-${symbol}`).innerHTML = 
                        `${symbol}${warning} 🔍 | <span style="font-weight: normal; font-size: 0.85em; color: #666;">UTC: ${utcStr} | Local: ${localStr}</span>`;
                }

                const chart = LightweightCharts.createChart(container, {
                    autoSize: true,
                    layout: {
                        background: { type: 'solid', color: 'white' },
                        textColor: '#333',
                    },
                    grid: {
                        vertLines: { color: '#f5f5f5' },
                        horzLines: { color: '#f5f5f5' },
                    },
                    timeScale: {
                        timeVisible: true,
                        secondsVisible: false,
                        borderVisible: false,
                    },
                    rightPriceScale: {
                        autoScale: true,
                        borderVisible: false,
                        scaleMargins: {
                            top: 0.1,
                            bottom: 0.1,
                        },
                    },
                    handleScroll: {
                        mouseWheel: true,
                        pressedMouseMove: true,
                        horzTouchDrag: true,
                        vertTouchDrag: true,
                    },
                    handleScale: {
                        axisPressedMouseMove: true,
                        mouseWheel: true,
                        pinch: true,
                    },
                });
                charts[symbol] = chart;

                const candleSeries = chart.addCandlestickSeries({
                    upColor: '#26a69a',
                    downColor: '#ef5350',
                    borderVisible: false,
                    wickUpColor: '#26a69a',
                    wickDownColor: '#ef5350',
                });

                candleSeries.setData(chartData);

                // Add EMAs
                if (symbolData.ema9.length > 0) {
                    const ema9Series = chart.addLineSeries({ color: '#2196F3', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
                    ema9Series.setData(symbolData.ema9);
                }
                if (symbolData.ema21.length > 0) {
                    const ema21Series = chart.addLineSeries({ color: '#FF9800', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
                    ema21Series.setData(symbolData.ema21);
                }

                // Add S/R zones
                srResults.forEach(res => {
                    const color = res.result === 'support' ? 'rgba(38, 166, 154, 0.25)' : 'rgba(239, 83, 80, 0.25)';
                    
                    const boxSeries = chart.addBaselineSeries({
                        baseValue: { type: 'price', price: res.price_range.low },
                        topFillColor1: color,
                        topFillColor2: color,
                        topLineColor: 'transparent',
                        bottomFillColor1: 'transparent',
                        bottomFillColor2: 'transparent',
                        bottomLineColor: 'transparent',
                        lineWidth: 0,
                        priceLineVisible: false,
                        lastValueVisible: false,
                        crosshairMarkerVisible: false,
                        autoscaleInfoProvider: () => null,
                    });

                    const startTime = typeof res.start_time === 'number' ? res.start_time : Math.floor(new Date(res.start_time).getTime() / 1000);
                    const endTime = typeof res.end_time === 'number' ? res.end_time : Math.floor(new Date(res.end_time).getTime() / 1000);

                    const boxData = chartData
                        .filter(d => d.time >= startTime && d.time <= endTime)
                        .map(d => ({
                            time: d.time,
                            value: res.price_range.high
                        }));
                    
                    if (boxData.length > 0) {
                        boxSeries.setData(boxData);
                    }
                });
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
    
    with open(output_file, "w") as f:
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
            
    # 3. Generate Dashboard
    generate_dashboard(all_results, config)
    
    # 4. Send Consolidated Notification
    send_consolidated_telegram(active_detections)

if __name__ == "__main__":
    run_pipeline()
