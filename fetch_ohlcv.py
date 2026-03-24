import os
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

def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 100):
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
    # Example usage:
    # Change 'US500' and 'H1' to your actual symbol and timeframe
    symbol = "US500"
    timeframe = "H1"
    
    data = fetch_ohlcv(symbol, timeframe)
    
    if data:
        print(f"Successfully fetched {len(data)} records for {symbol} ({timeframe}):")
        for record in data[:5]:
            print(record)
    else:
        print(f"No data found for {symbol} ({timeframe}) or an error occurred.")
