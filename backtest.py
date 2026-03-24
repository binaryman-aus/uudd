import json
import os
import pandas as pd
from sr_detect import detect_sr
from jinja2 import Template
from datetime import datetime

def run_backtest(input_file, window_size=200):
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

    # Sliding window
    for i in range(total_bars - window_size + 1):
        window_data = df.iloc[i : i + window_size].to_dict('records')
        
        # Call detection logic
        # We can pass parameters here if needed
        sr_result = detect_sr(window_data)
        
        if sr_result['result'] != 'nil':
            # Add the last bar's time in the window as the detection timestamp
            sr_result['detected_at'] = window_data[-1]['time'].isoformat()
            results.append(sr_result)

    print(f"Backtest complete. Found {len(results)} detections.")
    return results

def generate_html_report(results, output_file="backtest_report.html"):
    """
    Generates an HTML report from the backtest results.
    """
    template_str = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>S/R Detection Backtest Report</title>
        <style>
            body { font-family: sans-serif; margin: 20px; background-color: #f4f4f9; }
            h1 { color: #333; }
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
        <p>Generated at: {{ now }}</p>
        <table>
            <thead>
                <tr>
                    <th>Detected At</th>
                    <th>Type</th>
                    <th>Price Range</th>
                    <th>False Breakout %</th>
                    <th>Previous Matches</th>
                </tr>
            </thead>
            <tbody>
                {% for res in results %}
                <tr>
                    <td>{{ res.detected_at }}</td>
                    <td class="{{ res.result }}">{{ res.result.upper() }}</td>
                    <td class="price-range">
                        Low: {{ "%.2f"|format(res.price_range.low) }}<br>
                        High: {{ "%.2f"|format(res.price_range.high) }}
                    </td>
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
    </body>
    </html>
    """
    
    template = Template(template_str)
    html_content = template.render(results=results, now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    with open(output_file, "w") as f:
        f.write(html_content)
    
    print(f"Report generated: {output_file}")
    return output_file

if __name__ == "__main__":
    import sys
    
    # Use the latest file in data folder by default
    input_file = None
    if os.path.exists("data"):
        files = [f for f in os.listdir("data") if f.endswith(".json")]
        if files:
            # Sort by modification time to get the latest
            files.sort(key=lambda x: os.path.getmtime(os.path.join("data", x)), reverse=True)
            input_file = os.path.join("data", files[0])
            
    if not input_file:
        print("No data files found in 'data/' folder.")
        sys.exit(1)
        
    backtest_results = run_backtest(input_file)
    if backtest_results:
        generate_html_report(backtest_results)
    else:
        print("No results to report.")
