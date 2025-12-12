"""
Bollinger Band Squeeze Alert System
Designed for execution via GitHub Actions Cron Job (runs every 5 minutes)
Zero phone battery usage - runs entirely in cloud
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import json
import os
import sys # Added for clean exit on error

# ============================================================================
# CONFIGURATION (Set in GitHub Actions Secrets)
# ============================================================================

# These variables are automatically loaded by os.getenv() when set in GitHub Secrets
TWELVE_API_KEY = os.getenv('TWELVE_API_KEY', 'demo')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Strategy parameters
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2.0
SQUEEZE_THRESHOLD = 0.0002
# Example: Exclude hours 7 AM to 10 AM, and 12 PM to 3 PM UTC
EXCLUDED_HOURS = [7, 8, 9, 12, 13, 14] 

# State management (using Vercel KV or simple file)
# NOTE: Cooldown logic requires persistent storage (like a database or Vercel KV).
# Since GitHub Actions runs are isolated, this simplified state management will 
# NOT work reliably for cooldown unless you integrate a database.
LAST_ALERT_KEY = 'last_alert_time'
ALERT_COOLDOWN = 3600  # 1 hour

# ============================================================================
# STATE MANAGEMENT (Simplified for GHA)
# ============================================================================

def get_last_alert_time():
    """Get last alert timestamp from environment or storage"""
    # Placeholder: Always returns 0 (allow first alert) because state cannot 
    # be easily persisted across isolated GitHub Action runs.
    return 0

def set_last_alert_time(timestamp):
    """Store last alert timestamp"""
    # Placeholder: Does nothing unless integrated with a proper database (e.g., Redis, PostgreSQL).
    pass

# ============================================================================
# DATA FETCHING
# ============================================================================

def fetch_twelve_data():
    """Fetch real-time forex data from Twelve Data"""
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": "EUR/USD",
        "interval": "1min",
        "outputsize": 30,
        "apikey": TWELVE_API_KEY
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
        data = response.json()
        
        if "values" not in data:
            return None, f"API Error: {data.get('message', 'Unknown')}"
        
        df = pd.DataFrame(data["values"])
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.set_index('datetime')
        df = df.sort_index()
        df = df.rename(columns={
            'open': 'Open', 
            'high': 'High', 
            'low': 'Low', 
            'close': 'Close'
        })
        df = df[['Open', 'High', 'Low', 'Close']].astype(float)
        
        return df, None
        
    except requests.RequestException as e:
        return None, f"Request failed: {e}"
    except Exception as e:
        return None, str(e)

def fetch_yahoo_fallback():
    """Fallback to Yahoo Finance"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
        params = {"interval": "1m", "range": "1d"}
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        quotes = result["indicators"]["quote"][0]
        
        df = pd.DataFrame({
            'Open': quotes['open'],
            'High': quotes['high'],
            'Low': quotes['low'],
            'Close': quotes['close']
        })
        df.index = pd.to_datetime(timestamps, unit='s')
        df = df.dropna().sort_index()
        
        return df, None
        
    except Exception as e:
        return None, f"Yahoo fetch failed: {str(e)}"

# ============================================================================
# TECHNICAL ANALYSIS
# ============================================================================

def calculate_bollinger_bands(df, period=20, std=2.0):
    """Calculate Bollinger Bands"""
    df['SMA'] = df['Close'].rolling(window=period).mean()
    df['STD'] = df['Close'].rolling(window=period).std()
    df['Upper_Band'] = df['SMA'] + (std * df['STD'])
    df['Lower_Band'] = df['SMA'] - (std * df['STD'])
    df['Band_Width'] = (df['Upper_Band'] - df['Lower_Band']) / df['SMA']
    return df

def analyze_market():
    """Main analysis function"""
    result = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'status': 'error',
        'message': '',
        'data': {}
    }
    
    # Fetch data
    df, error = fetch_twelve_data()
    if df is None:
        print(f"Twelve Data failed. Trying Yahoo Fallback. Error: {error}")
        df, error = fetch_yahoo_fallback()
    
    if df is None:
        result['message'] = f"Data fetch failed: {error}"
        return result
    
    if len(df) < BOLLINGER_PERIOD:
        result['message'] = f"Insufficient data: {len(df)} bars"
        return result
    
    # Calculate Bollinger Bands
    df = calculate_bollinger_bands(df, BOLLINGER_PERIOD, BOLLINGER_STD)
    
    # Get latest values
    latest = df.iloc[-1]
    current_price = latest['Close']
    band_width = latest['Band_Width']
    
    # Check conditions
    is_squeeze = band_width < SQUEEZE_THRESHOLD
    current_hour = datetime.now(timezone.utc).hour
    is_valid_hour = current_hour not in EXCLUDED_HOURS
    
    # Populate result
    result['status'] = 'success'
    result['data'] = {
        'price': float(current_price),
        'band_width': float(band_width),
        'threshold': SQUEEZE_THRESHOLD,
        'is_squeeze': is_squeeze,
        'current_hour': current_hour,
        'is_valid_hour': is_valid_hour,
        'signal': is_squeeze and is_valid_hour,
        'bars_analyzed': len(df)
    }
    
    return result

# ============================================================================
# TELEGRAM NOTIFICATIONS
# ============================================================================

def send_telegram_message(message, parse_mode='HTML'):
    """Send message via Telegram bot"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram configuration missing. Cannot send message.")
        return False, "Telegram not configured"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True, "Sent"
    except requests.RequestException as e:
        return False, f"Telegram request failed: {e} - Response: {response.text if 'response' in locals() else 'N/A'}"
    except Exception as e:
        return False, str(e)

def send_trade_alert(data):
    """Send formatted trade alert via Telegram"""
    price = data['price']
    band_width = data['band_width']
    
    # Calculate trade parameters
    # Note: Assuming 4-digit pips (0.0001) for EUR/USD. +4 pips = 0.0004
    stop_loss = price + 0.0004
    take_profit = price - 0.0020
    
    current_time = datetime.now(timezone.utc).strftime('%H:%M GMT')
    
    message = f"""
🎯 <b>EURUSD TRADE SIGNAL!</b>

⏰ <b>Time:</b> {current_time}
💰 <b>Price:</b> {price:.5f}
📊 <b>Band Width:</b> {band_width:.6f}

📉 <b>TRADE SETUP:</b>
▫️ Direction: SELL
▫️ Size: 0.01 lots
▫️ Stop Loss: {stop_loss:.5f} (+4 pips)
▫️ Take Profit: {take_profit:.5f} (-20 pips)

🚀 <b>Open Exness NOW and execute!</b>

Strategy: Bollinger Squeeze
Risk/Reward: 1:5
"""
    
    return send_telegram_message(message)

# ============================================================================
# MAIN EXECUTION FUNCTION (Standalone Entry Point)
# ============================================================================

def main_execution():
    """Main function to be called by GitHub Actions on schedule."""
    
    # --- CHECK ENVIRONMENT ---
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("CRITICAL ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing. Check GitHub Secrets setup.")
        sys.exit(1) # Exit with an error code
        
    print(f"[{datetime.now(timezone.utc).isoformat()}] Cron job triggered")
    
    # --- ANALYZE MARKET ---
    result = analyze_market()
    
    if result['status'] == 'error':
        print(f"❌ Execution failed: {result['message']}")
        # send_telegram_message(f"Alert System Data Error: {result['message']}") # Optional error notification
        return # Exit the function on data error
        
    # --- CHECK SIGNAL & COOLDOWN ---
    data = result['data']
    
    if data['signal']:
        print(f"🎯 SIGNAL DETECTED! Price: {data['price']:.5f}, BW: {data['band_width']:.6f}")
        
        # Cooldown check logic (will not work without a database!)
        last_alert = get_last_alert_time() 
        time_since = datetime.now(timezone.utc).timestamp() - last_alert
        
        if time_since >= ALERT_COOLDOWN:
            # Send alert
            success, msg = send_trade_alert(data)
            
            if success:
                set_last_alert_time(datetime.now(timezone.utc).timestamp())
                print("✅ Alert sent successfully")
            else:
                print(f"❌ Alert failed: {msg}")
        else:
            remaining = int((ALERT_COOLDOWN - time_since) / 60)
            print(f"⏳ Cooldown active: {remaining} min remaining. Alert skipped.")
    else:
        # No signal
        reasons = []
        if not data['is_squeeze']:
            reasons.append("No squeeze")
        if not data['is_valid_hour']:
            reasons.append("Invalid hour")
            
        print(f"⏸️ No signal: {', '.join(reasons)}")

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    main_execution()
