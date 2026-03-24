import json
import os
import pandas as pd
from sr_detect import detect_sr
from jinja2 import Template
from datetime import datetime

def run_backtest(input_file, window_size=200, nbars=20, threshold=0.3, confirm=0.5, atr_period=14, wick_percentage=0.4):
    """
    Runs a sliding window backtest on OHLCV data.
    """
    if not os.path.exists(input_file):
        print(f"File {input_file} not found.")
        return None

    with open(input_file, "r") as f:
        data = json.load(f)

    # Sort by time ascending
    df = pd.DataFrame(data)
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)
    
    total_bars = len(df)
    if total_bars < window_size:
        print(f"Not enough data for window size {window_size}. Total bars: {total_bars}")
        return None

    results = []
    print(f"Running backtest on {total_bars} bars with window size {window_size}...")
    print(f"S/R Params: nbars={nbars}, threshold={threshold}, confirm={confirm}, atr_period={atr_period}, wick={wick_percentage}")

    # Sliding window
    for i in range(total_bars - window_size + 1):
        window_data = df.iloc[i : i + window_size].to_dict('records')
        
        # Call detection logic with custom parameters
        sr_result = detect_sr(
            window_data, 
            n_bars=nbars, 
            threshold_factor=threshold, 
            confirm_percentage=confirm,
            atr_period=atr_period,
            wick_percentage=wick_percentage
        )
        
        if sr_result['result'] != 'nil':
            # Add the last bar's time in the window as the detection timestamp
            sr_result['detected_at'] = window_data[-1]['time'].isoformat()
            results.append(sr_result)

    print(f"Backtest complete. Found {len(results)} detections.")
    return results

def generate_html_report(results, params, full_ohlcv, output_file="backtest_report.html"):
    """
    Generates an HTML report from the backtest results including a chart.
    """
    # Prepare data for Lightweight Charts
    chart_data = []
    for bar in full_ohlcv:
        chart_data.append({
            "time": int(pd.to_datetime(bar['time']).timestamp()),
            "open": float(bar['open']),
            "high": float(bar['high']),
            "low": float(bar['low']),
            "close": float(bar['close'])
        })
    
    # Sort chart data by time
    chart_data.sort(key=lambda x: x['time'])

    template_str = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>S/R Detection Backtest Report (W:{{ params.window }}, N:{{ params.nbars }}, T:{{ params.threshold }}, C:{{ params.confirm }}, ATR:{{ params.atr_period }}, Wick:{{ params.wick }})</title>
        <script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
        <style>
            body { font-family: sans-serif; margin: 20px; background-color: #f4f4f9; }
            h1 { color: #333; }
            h2 { color: #555; font-size: 1.2em; }
            #chart-container { 
                width: 100%; 
                height: 500px; 
                margin-top: 20px; 
                background: white; 
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; background: white; }
            th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
            th { background-color: #007bff; color: white; }
            tr:nth-child(even) { background-color: #f2f2f2; }
            .support { color: green; font-weight: bold; }
            .resistance { color: red; font-weight: bold; }
            .price-range { font-size: 0.9em; color: #666; }
            .prev-matches { font-size: 0.8em; }
        </style>
    </head>
    <body>
        <h1>S/R Detection Backtest Report</h1>
        <h2>Parameters: Window Size: {{ params.window }}, N-Bars: {{ params.nbars }}, ATR Threshold: {{ params.threshold }}, Confirmation: {{ params.confirm*100 }}%, ATR Period: {{ params.atr_period }}, Min Wick: {{ params.wick*100 }}%</h2>
        <p>Generated at: {{ now }}</p>
        
        <div id="chart-container"></div>

        <table>
            <thead>
                <tr>
                    <th>Detected At</th>
                    <th>Type</th>
                    <th>Price Range</th>
                    <th>S/R Start Bar</th>
                    <th>S/R End Bar</th>
                    <th>False Breakout %</th>
                    <th>Previous Matches</th>
                </tr>
            </thead>
            <tbody>
                {% for res in results %}
                <tr onclick="zoomTo('{{ res.detected_at }}')" style="cursor: pointer;">
                    <td>{{ res.detected_at }}</td>
                    <td class="{{ res.result }}">{{ res.result.upper() }}</td>
                    <td class="price-range">
                        Low: {{ "%.2f"|format(res.price_range.low) }}<br>
                        High: {{ "%.2f"|format(res.price_range.high) }}
                    </td>
                    <td>{{ res.start_time }}</td>
                    <td>{{ res.end_time }}</td>
                    <td>{{ "%.2f"|format(res.false_breakout_pct) }}%</td>
                    <td class="prev-matches">
                        <ul>
                        {% for match in res.prev_matches %}
                            <li>{{ match.type }} at {{ match.datetime }} ({{ "%.2f"|format(match.price) }})</li>
                        {% endfor %}
                        </ul>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        <script>
            const chartData = {{ chart_data_json }};
            const srResults = {{ sr_results_json }};

            const chart = LightweightCharts.createChart(document.getElementById('chart-container'), {
                layout: {
                    backgroundColor: '#ffffff',
                    textColor: '#333',
                },
                grid: {
                    vertLines: { color: '#f0f0f0' },
                    horzLines: { color: '#f0f0f0' },
                },
                timeScale: {
                    timeVisible: true,
                    secondsVisible: false,
                },
            });

            const candleSeries = chart.addCandlestickSeries({
                upColor: '#26a69a',
                downColor: '#ef5350',
                borderVisible: false,
                wickUpColor: '#26a69a',
                wickDownColor: '#ef5350',
            });

            candleSeries.setData(chartData);

            // Add S/R zones as horizontal price lines or markers
            // For visualization, we'll draw lines for the ranges
            srResults.forEach(res => {
                const color = res.result === 'support' ? 'rgba(38, 166, 154, 0.2)' : 'rgba(239, 83, 80, 0.2)';
                const lineColor = res.result === 'support' ? '#26a69a' : '#ef5350';
                
                // Add price lines for the range
                candleSeries.createPriceLine({
                    price: res.price_range.low,
                    color: lineColor,
                    lineWidth: 1,
                    lineStyle: 2, // Dashed
                    axisLabelVisible: true,
                    title: res.result.toUpperCase() + ' LOW',
                });
                candleSeries.createPriceLine({
                    price: res.price_range.high,
                    color: lineColor,
                    lineWidth: 1,
                    lineStyle: 2, // Dashed
                    axisLabelVisible: true,
                    title: res.result.toUpperCase() + ' HIGH',
                });
            });

            function zoomTo(dateStr) {
                const timestamp = Math.floor(new Date(dateStr).getTime() / 1000);
                chart.timeScale().setVisibleRange({
                    from: timestamp - (24 * 3600 * 2), // 2 days before
                    to: timestamp + (24 * 3600 * 2),   // 2 days after
                });
            }
        </script>
    </body>
    </html>
    """
    
    template = Template(template_str)
    html_content = template.render(
        results=results, 
        params=params, 
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        chart_data_json=json.dumps(chart_data),
        sr_results_json=json.dumps(results)
    )
    
    with open(output_file, "w") as f:
        f.write(html_content)
    
    print(f"Report generated: {output_file}")
    return output_file

if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Sliding Window Backtest for S/R Detection")
    parser.add_argument("--window", type=int, default=200, help="Sliding window size")
    parser.add_argument("--nbars", type=int, default=20, help="Lookback period for detection")
    parser.add_argument("--threshold", type=float, default=0.3, help="ATR multiplier for range")
    parser.add_argument("--confirm", type=float, default=0.5, help="Confirmation percentage (0.0 to 1.0)")
    parser.add_argument("--atr_period", type=int, default=14, help="ATR window size")
    parser.add_argument("--wick", type=float, default=0.4, help="Minimum wick percentage")
    parser.add_argument("--input", type=str, help="Path to OHLCV JSON file")
    
    args = parser.parse_args()
    
    # Use the latest file in data folder by default if --input not provided
    input_file = args.input
    if not input_file:
        if os.path.exists("data"):
            files = [f for f in os.listdir("data") if f.endswith(".json")]
            if files:
                # Sort by modification time to get the latest
                files.sort(key=lambda x: os.path.getmtime(os.path.join("data", x)), reverse=True)
                input_file = os.path.join("data", files[0])
            
    if not input_file:
        print("No data files found in 'data/' folder.")
        sys.exit(1)
        
    # Load full OHLCV for charting
    with open(input_file, "r") as f:
        full_ohlcv_data = json.load(f)

    backtest_results = run_backtest(
        input_file, 
        window_size=args.window, 
        nbars=args.nbars, 
        threshold=args.threshold, 
        confirm=args.confirm,
        atr_period=args.atr_period,
        wick_percentage=args.wick
    )
    if backtest_results:
        # Pass all args and full data to report generator
        generate_html_report(backtest_results, vars(args), full_ohlcv_data)
    else:
        print("No results to report.")
