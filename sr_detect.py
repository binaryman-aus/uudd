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

def detect_sr(ohlcv_data, n_bars=20, threshold_factor=0.3, min_bars=5, atr_period=14, wick_percentage=0.4, min_wick_bars=2, debug=False):
    """
    Detects Support and Resistance levels based on Kevin Yu's theory.

    ohlcv_data: List of dicts (already sorted by time ascending)
    n_bars: Lookback period for detection
    threshold_factor: Multiplier for ATR to define 'narrow range'
    min_bars: Minimum number of bars that must fall within the range
    atr_period: Period for ATR calculation
    wick_percentage: Minimum wick size as percentage of total bar range for bars touching the level
    min_wick_bars: Minimum number of in-range bars that must satisfy the wick requirement
    debug: If True, returns detailed calculation data
    """
    if not ohlcv_data or len(ohlcv_data) < max(n_bars, atr_period):
        return {"result": "nil", "reason": "Insufficient data"}

    df = pd.DataFrame(ohlcv_data)
    # Ensure columns are numeric
    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col])
    
    # Sort by time ascending for calculation
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)
    
    df['atr'] = calculate_atr(df, period=atr_period)
    
    # Use the most recent ATR for range definition
    current_atr = df['atr'].iloc[-1]
    if pd.isna(current_atr):
        return {"result": "nil", "reason": "ATR calculation failed"}
        
    y_range = current_atr * threshold_factor
    
    # Detection window (last N bars)
    recent_df = df.tail(n_bars)
    
    debug_info = {
        "current_atr": float(current_atr),
        "y_range": float(y_range),
        "n_bars": n_bars,
        "min_bars": min_bars,
        "bars": []
    }
    
    if debug:
        # Pre-populate bars for debugging
        for i in range(len(recent_df)):
            bar = recent_df.iloc[i]
            # Body boundaries for wick calculation
            body_top = max(bar['open'], bar['close'])
            body_bottom = min(bar['open'], bar['close'])
            total_range = bar['high'] - bar['low']
            
            upper_wick = bar['high'] - body_top
            lower_wick = body_bottom - bar['low']
            
            debug_info['bars'].append({
                "time": bar['time'].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "high": float(bar['high']),
                "low": float(bar['low']),
                "open": float(bar['open']),
                "close": float(bar['close']),
                "upper_wick_pct": float(upper_wick / total_range) if total_range > 0 else 0,
                "lower_wick_pct": float(lower_wick / total_range) if total_range > 0 else 0
            })
            
    results = []
    
    # 1. Resistance detection (check highs)
    best_resistance = None
    max_high_count = 0
    
    for i in range(len(recent_df)):
        level = recent_df['high'].iloc[i]
        range_low = level - y_range
        range_high = level + y_range
        
        # Count highs in range
        in_range_mask = (recent_df['high'] >= range_low) & (recent_df['high'] <= range_high)
        count = in_range_mask.sum()
        
        # Validation: No bar between start and end should close ABOVE range_high
        invalid = (recent_df['close'] > range_high).any()
        
        # New Constraint: No more than one bar high is BELOW the price range (a "miss")
        miss_count = (recent_df['high'] < range_low).sum()
        
        # New Requirement: The last bar's high must be in the range
        last_high = recent_df['high'].iloc[-1]
        last_in_range = (last_high >= range_low) and (last_high <= range_high)
        
        # Rejection Wick Validation for Resistance (Upper Wicks)
        wick_valid = True
        price_spread = float('inf')
        if count > 0:
            in_range_bars = recent_df[in_range_mask]
            upper_wicks = (in_range_bars['high'] - in_range_bars[['open', 'close']].max(axis=1))
            total_ranges = (in_range_bars['high'] - in_range_bars['low']).replace(0, 0.0001)
            wick_ratios = upper_wicks / total_ranges
            # Relaxed: At least 50% of bars touching the level must have the required wick
            wick_met_count = (wick_ratios >= wick_percentage).sum()
            wick_valid = (wick_met_count >= min_wick_bars)
            # Tiebreaker: spread of in-zone Highs, dropping the single highest outlier
            highs = in_range_bars['high'].sort_values()
            trimmed_highs = highs.iloc[:-1] if len(highs) > 1 else highs
            price_spread = float(trimmed_highs.max() - trimmed_highs.min())

        if count >= min_bars and not invalid and last_in_range and wick_valid and miss_count <= 1:
            if count > max_high_count:
                in_range_times = recent_df[in_range_mask]['time']
                max_high_count = count
                best_resistance = {
                    "type": "resistance",
                    "level": level,
                    "range_low": float(range_low),
                    "range_high": float(range_high),
                    "count": int(count),
                    "price_spread": price_spread,
                    "start_time": in_range_times.iloc[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_time": in_range_times.iloc[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "wick_valid": bool(wick_valid),
                    "invalid_close": bool(invalid),
                    "last_in_range": bool(last_in_range)
                }

    # 2. Support detection (check lows)
    best_support = None
    max_low_count = 0
    
    for i in range(len(recent_df)):
        level = recent_df['low'].iloc[i]
        range_low = level - y_range
        range_high = level + y_range
        
        # Count lows in range
        in_range_mask = (recent_df['low'] >= range_low) & (recent_df['low'] <= range_high)
        count = in_range_mask.sum()
        
        # Validation: No bar between start and end should close BELOW range_low
        invalid = (recent_df['close'] < range_low).any()
        
        # New Constraint: No more than one bar low is ABOVE the price range (a "miss")
        miss_count = (recent_df['low'] > range_high).sum()
        
        # New Requirement: The last bar's low must be in the range
        last_low = recent_df['low'].iloc[-1]
        last_in_range = (last_low >= range_low) and (last_low <= range_high)
        
        # Rejection Wick Validation for Support (Lower Wicks)
        wick_valid = True
        price_spread = float('inf')
        if count > 0:
            in_range_bars = recent_df[in_range_mask]
            lower_wicks = (in_range_bars[['open', 'close']].min(axis=1) - in_range_bars['low'])
            total_ranges = (in_range_bars['high'] - in_range_bars['low']).replace(0, 0.0001)
            wick_ratios = lower_wicks / total_ranges
            # Relaxed: At least 50% of bars touching the level must have the required wick
            wick_met_count = (wick_ratios >= wick_percentage).sum()
            wick_valid = (wick_met_count >= min_wick_bars)
            # Tiebreaker: spread of in-zone Lows, dropping the single lowest outlier
            lows = in_range_bars['low'].sort_values()
            trimmed_lows = lows.iloc[1:] if len(lows) > 1 else lows
            price_spread = float(trimmed_lows.max() - trimmed_lows.min())

        if count >= min_bars and not invalid and last_in_range and wick_valid and miss_count <= 1:
            if count > max_low_count:
                in_range_times = recent_df[in_range_mask]['time']
                max_low_count = count
                best_support = {
                    "type": "support",
                    "level": level,
                    "range_low": float(range_low),
                    "range_high": float(range_high),
                    "count": int(count),
                    "price_spread": price_spread,
                    "start_time": in_range_times.iloc[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_time": in_range_times.iloc[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "wick_valid": bool(wick_valid),
                    "invalid_close": bool(invalid),
                    "last_in_range": bool(last_in_range)
                }

    # Decide which one to report: higher count wins; on a tie, tighter price cluster wins
    # (smaller spread of Highs/Lows after dropping one outlier); if still tied, resistance wins.
    sr_found = None
    if best_resistance and best_support:
        if best_resistance['count'] > best_support['count']:
            sr_found = best_resistance
        elif best_support['count'] > best_resistance['count']:
            sr_found = best_support
        elif best_resistance['price_spread'] <= best_support['price_spread']:
            sr_found = best_resistance
        else:
            sr_found = best_support
    elif best_resistance:
        sr_found = best_resistance
    elif best_support:
        sr_found = best_support
        
    if not sr_found:
        if debug:
            # If no S/R found, return info about why
            # We can pick a candidate level (e.g. the last bar's high) to show why it failed
            return {"result": "nil", "debug": debug_info}
        return {"result": "nil"}
    
    # ... rest of the function for False Breakout and matches ...
    # (I'll skip the matches part for debug or just include it)
    
    # 3. False Breakout Calculation
    if sr_found['type'] == "resistance":
        highest_high = recent_df['high'].max()
        fb_val = (highest_high - sr_found['range_high']) / current_atr
        sr_found['false_breakout_pct'] = float(max(0, fb_val) * 100)
    else: # support
        lowest_low = recent_df['low'].min()
        fb_val = (sr_found['range_low'] - lowest_low) / current_atr
        sr_found['false_breakout_pct'] = float(max(0, fb_val) * 100)

    # 4. S/R Flip and Previous Levels Check
    history_df = df.iloc[:-n_bars]
    sr_found['prev_matches'] = []
    match_type = 'low' if sr_found['type'] == 'resistance' else 'high'
    for i in range(len(history_df)):
        price_val = history_df[match_type].iloc[i]
        if sr_found['range_low'] <= price_val <= sr_found['range_high']:
            sr_found['prev_matches'].append({
                "type": f"prev_{match_type}_match",
                "datetime": history_df['time'].iloc[i].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "price": float(price_val)
            })
    same_type = 'high' if sr_found['type'] == 'resistance' else 'low'
    for i in range(len(history_df)):
        price_val = history_df[same_type].iloc[i]
        if sr_found['range_low'] <= price_val <= sr_found['range_high']:
            sr_found['prev_matches'].append({
                "type": f"prev_{same_type}_match",
                "datetime": history_df['time'].iloc[i].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "price": float(price_val)
            })
    sr_found['prev_matches'] = sr_found['prev_matches'][-5:] 

    final_result = {
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
    
    if debug:
        final_result['debug'] = debug_info
            
    return final_result

def load_config(config_file="config.json"):
    """
    Loads configuration from a JSON file.
    """
    defaults = {
        "nbars": 7,
        "min_bars": 5,
        "atr_period": 21,
        "threshold": 0.5,
        "wick": 0.1,
        "min_wick_bars": 2,
    }
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                config = json.load(f)
            defaults.update(config)
        except Exception as e:
            import sys
            print(f"Warning: Could not load config file: {e}", file=sys.stderr)
    return defaults

if __name__ == "__main__":
    import sys
    import argparse
    
    # Load defaults from config file first
    config = load_config()

    parser = argparse.ArgumentParser(description="Support and Resistance Detection")
    parser.add_argument("input_file", nargs="?", help="Path to OHLCV JSON file")
    parser.add_argument("--nbars", type=int, default=config["nbars"], help="Lookback period for detection")
    parser.add_argument("--threshold", type=float, default=config["threshold"], help="ATR multiplier for range")
    parser.add_argument("--min_bars", type=int, default=config["min_bars"], help="Minimum bars touching level")
    parser.add_argument("--atr_period", type=int, default=config["atr_period"], help="ATR period")
    parser.add_argument("--wick", type=float, default=config["wick"], help="Minimum wick percentage")
    parser.add_argument("--min_wick_bars", type=int, default=config["min_wick_bars"], help="Minimum number of in-range bars that must satisfy the wick requirement")
    parser.add_argument("--debug_time", type=str, help="Datetime for debug mode (ISO format)")
    
    args = parser.parse_args()
    
    input_file = args.input_file
    if not input_file:
        # Search for any file in data folder
        if os.path.exists("data"):
            files = [f for f in os.listdir("data") if f.endswith(".json")]
            if files:
                # Sort by modification time to get the latest
                files.sort(key=lambda x: os.path.getmtime(os.path.join("data", x)), reverse=True)
                input_file = os.path.join("data", files[0])
    
    if not input_file or not os.path.exists(input_file):
        print(json.dumps({"result": "error", "message": "Input file not found"}))
        sys.exit(1)
        
    try:
        with open(input_file, "r") as f:
            ohlcv_data = json.load(f)
            
        if args.debug_time:
            # Debug mode: Find the window ending at debug_time
            df = pd.DataFrame(ohlcv_data)
            df['time'] = pd.to_datetime(df['time'])
            df = df.sort_values('time').reset_index(drop=True)
            
            # Find the index of the bar with target time
            target_time = pd.to_datetime(args.debug_time)
            idx_list = df.index[df['time'] == target_time].tolist()
            
            if not idx_list:
                print(json.dumps({"result": "error", "message": f"Time {args.debug_time} not found in data"}))
                sys.exit(1)
            
            idx = idx_list[0]
            if idx < args.nbars:
                print(json.dumps({"result": "error", "message": f"Not enough history before {args.debug_time}"}))
                sys.exit(1)
                
            # Slice the data up to the target index
            debug_ohlcv = df.iloc[:idx+1].to_dict('records')
            
            result = detect_sr(
                debug_ohlcv,
                n_bars=args.nbars,
                threshold_factor=args.threshold,
                min_bars=args.min_bars,
                atr_period=args.atr_period,
                wick_percentage=args.wick,
                min_wick_bars=args.min_wick_bars,
                debug=True
            )
        else:
            # Normal mode
            result = detect_sr(
                ohlcv_data,
                n_bars=args.nbars,
                threshold_factor=args.threshold,
                min_bars=args.min_bars,
                atr_period=args.atr_period,
                wick_percentage=args.wick,
                min_wick_bars=args.min_wick_bars
            )
        print(json.dumps(result, indent=4))
        
    except Exception as e:
        print(json.dumps({"result": "error", "message": str(e)}))
