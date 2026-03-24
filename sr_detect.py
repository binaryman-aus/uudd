import json
import pandas as pd
import numpy as np
import os
from datetime import datetime

def calculate_atr(df, period=14):
    """
    Calculates Average True Range (ATR).
    """
    prev_close = df['close'].shift(1)
    tr1 = df['high'] - df['low']
    tr2 = abs(df['high'] - prev_close)
    tr3 = abs(df['low'] - prev_close)
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def detect_sr(ohlcv_data, n_bars=20, threshold_factor=0.3, confirm_percentage=0.5):
    """
    Detects Support and Resistance levels based on Kevin Yu's theory.
    
    ohlcv_data: List of dicts (already sorted by time ascending)
    n_bars: Lookback period for detection
    threshold_factor: Multiplier for ATR to define 'narrow range'
    confirm_percentage: Percentage of highs/lows that must fall within the range
    """
    if not ohlcv_data or len(ohlcv_data) < max(n_bars, 14):
        return {"result": "nil", "reason": "Insufficient data"}

    df = pd.DataFrame(ohlcv_data)
    # Ensure columns are numeric
    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col])
    
    # Sort by time ascending for calculation
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)
    
    df['atr'] = calculate_atr(df)
    
    # Use the most recent ATR for range definition
    current_atr = df['atr'].iloc[-1]
    if pd.isna(current_atr):
        return {"result": "nil", "reason": "ATR calculation failed"}
        
    y_range = current_atr * threshold_factor
    
    # Detection window (last N bars)
    recent_df = df.tail(n_bars)
    
    results = []
    
    # 1. Resistance detection (check highs)
    best_resistance = None
    max_high_count = 0
    
    for i in range(len(recent_df)):
        level = recent_df['high'].iloc[i]
        range_low = level - y_range
        range_high = level + y_range
        
        # Count highs in range
        count = ((recent_df['high'] >= range_low) & (recent_df['high'] <= range_high)).sum()
        
        if count >= n_bars * confirm_percentage:
            if count > max_high_count:
                max_high_count = count
                best_resistance = {
                    "type": "resistance",
                    "level": level,
                    "range_low": float(range_low),
                    "range_high": float(range_high),
                    "count": int(count),
                    "start_time": recent_df['time'].iloc[0].isoformat(),
                    "end_time": recent_df['time'].iloc[-1].isoformat()
                }

    # 2. Support detection (check lows)
    best_support = None
    max_low_count = 0
    
    for i in range(len(recent_df)):
        level = recent_df['low'].iloc[i]
        range_low = level - y_range
        range_high = level + y_range
        
        # Count lows in range
        count = ((recent_df['low'] >= range_low) & (recent_df['low'] <= range_high)).sum()
        
        if count >= n_bars * confirm_percentage:
            if count > max_low_count:
                max_low_count = count
                best_support = {
                    "type": "support",
                    "level": level,
                    "range_low": float(range_low),
                    "range_high": float(range_high),
                    "count": int(count),
                    "start_time": recent_df['time'].iloc[0].isoformat(),
                    "end_time": recent_df['time'].iloc[-1].isoformat()
                }

    # Decide which one to report (or both)
    # Preference to the one with higher count, then more recent
    sr_found = None
    if best_resistance and best_support:
        if best_resistance['count'] >= best_support['count']:
            sr_found = best_resistance
        else:
            sr_found = best_support
    elif best_resistance:
        sr_found = best_resistance
    elif best_support:
        sr_found = best_support
        
    if not sr_found:
        return {"result": "nil"}
    
    # 3. False Breakout Calculation
    # For resistance: max high of consecutive bars in range vs range_high
    # For simplicity, we check all bars in the recent window that are part of this detection
    if sr_found['type'] == "resistance":
        # Find highest high in the window that might have broken out
        highest_high = recent_df['high'].max()
        fb_val = (highest_high - sr_found['range_high']) / current_atr
        sr_found['false_breakout_pct'] = float(max(0, fb_val) * 100)
    else: # support
        lowest_low = recent_df['low'].min()
        fb_val = (sr_found['range_low'] - lowest_low) / current_atr
        sr_found['false_breakout_pct'] = float(max(0, fb_val) * 100)

    # 4. S/R Flip and Previous Levels Check
    # Look back before the current window
    history_df = df.iloc[:-n_bars]
    sr_found['prev_matches'] = []
    
    # Check for S/R Flip: 
    # If currently resistance, check if it was support (lows in range)
    # If currently support, check if it was resistance (highs in range)
    match_type = 'low' if sr_found['type'] == 'resistance' else 'high'
    
    # Check for previous Highs/Lows that match
    for i in range(len(history_df)):
        price_val = history_df[match_type].iloc[i]
        if sr_found['range_low'] <= price_val <= sr_found['range_high']:
            sr_found['prev_matches'].append({
                "type": f"prev_{match_type}_match",
                "datetime": history_df['time'].iloc[i].isoformat(),
                "price": float(price_val)
            })
            
    # Also check for same-type historical matches (e.g. prev resistance if current is resistance)
    same_type = 'high' if sr_found['type'] == 'resistance' else 'low'
    for i in range(len(history_df)):
        price_val = history_df[same_type].iloc[i]
        if sr_found['range_low'] <= price_val <= sr_found['range_high']:
            sr_found['prev_matches'].append({
                "type": f"prev_{same_type}_match",
                "datetime": history_df['time'].iloc[i].isoformat(),
                "price": float(price_val)
            })

    # Limit prev_matches to a reasonable number
    sr_found['prev_matches'] = sr_found['prev_matches'][-5:] 
    
    return {
        "result": sr_found['type'],
        "start_time": sr_found['start_time'],
        "end_time": sr_found['end_time'],
        "price_range": {
            "low": sr_found['range_low'],
            "high": sr_found['range_high']
        },
        "false_breakout_pct": sr_found['false_breakout_pct'],
        "prev_matches": sr_found['prev_matches']
    }

if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Support and Resistance Detection")
    parser.add_argument("input_file", nargs="?", help="Path to OHLCV JSON file")
    parser.add_argument("--nbars", type=int, default=20, help="Lookback period for detection")
    parser.add_argument("--threshold", type=float, default=0.3, help="ATR multiplier for range")
    parser.add_argument("--confirm", type=float, default=0.5, help="Confirmation percentage (0.0 to 1.0)")
    
    args = parser.parse_args()
    
    input_file = args.input_file
    if not input_file:
        # Search for any file in data folder
        if os.path.exists("data"):
            files = [f for f in os.listdir("data") if f.endswith(".json")]
            if files:
                input_file = os.path.join("data", files[0])
    
    if not input_file or not os.path.exists(input_file):
        print(json.dumps({"result": "error", "message": "Input file not found"}))
        sys.exit(1)
        
    try:
        with open(input_file, "r") as f:
            ohlcv_data = json.load(f)
            
        # Detect S/R
        result = detect_sr(
            ohlcv_data, 
            n_bars=args.nbars, 
            threshold_factor=args.threshold, 
            confirm_percentage=args.confirm
        )
        print(json.dumps(result, indent=4))
        
    except Exception as e:
        print(json.dumps({"result": "error", "message": str(e)}))
