import os
import json
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables from .env
load_dotenv()

def get_supabase_client() -> Client:
    url: str = os.getenv("SUPABASE_URL")
    key: str = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL or SUPABASE_KEY not found in environment variables")
    return create_client(url, key)

def save_to_data_folder(data, symbol, timeframe):
    """
    Saves the fetched data to the 'data' folder as a JSON file.
    """
    if not os.path.exists("data"):
        os.makedirs("data")
    
    # Clean symbol for filename (replace '/' with '_')
    safe_symbol = symbol.replace("/", "_")
    filename = f"data/{safe_symbol}_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    try:
        with open(filename, "w") as f:
            json.dump(data, f, indent=4)
        print(f"Data saved to {filename}")
        return filename
    except Exception as e:
        print(f"Error saving data: {e}")
        return None

def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 1000):
    """
    Fetches OHLCV data from the 'ohlcv' table in Supabase.
    """
    supabase = get_supabase_client()
    
    try:
        response = supabase.table("ohlcv") \
            .select("*") \
            .eq("symbol", symbol) \
            .eq("timeframe", timeframe) \
            .order("time", desc=True) \
            .limit(limit) \
            .execute()
        
        return response.data
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Fetch OHLCV data from Supabase")
    parser.add_argument("--symbol", type=str, default="US500", help="Trading symbol")
    parser.add_argument("--timeframe", type=str, default="H1", help="Timeframe (e.g., H1, D1)")
    parser.add_argument("--limit", type=int, default=1000, help="Number of bars to fetch")
    
    args = parser.parse_args()
    
    data = fetch_ohlcv(args.symbol, args.timeframe, limit=args.limit)
    
    if data:
        print(f"Successfully fetched {len(data)} records for {args.symbol} ({args.timeframe}):")
        filename = save_to_data_folder(data, args.symbol, args.timeframe)
        for record in data[:5]:
            print(record)
    else:
        print(f"No data found for {args.symbol} ({args.timeframe}) or an error occurred.")
