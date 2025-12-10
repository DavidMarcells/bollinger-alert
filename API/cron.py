# api/cron.py - Vercel Serverless Function
"""
Bollinger Band Squeeze Alert System
Serverless deployment on Vercel with Telegram notifications
Zero phone battery usage - runs entirely in cloud
"""

from http.server import BaseHTTPRequestHandler
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import json
import os

# ============================================================================
# CONFIGURATION (Set in Vercel Environment Variables)
# ============================================================================

TWELVE_API_KEY = os.getenv('TWELVE_API_KEY', 'demo')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Strategy parameters
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2.0
SQUEEZE_THRESHOLD = 0.002
EXCLUDED_HOURS = [7, 8, 9, 12, 13, 14]

# State management (using Vercel KV or simple file)
LAST_ALERT_KEY = 'last_alert_time'
ALERT_COOLDOWN = 3600  # 1 hour

# ============================================================================
# STATE MANAGEMENT (Vercel Edge Config / KV)
# ============================================================================

def get_last_alert_time():
    """Get last alert timestamp from environment or storage"""
    # For now, using a simple approach
    # In production, use Vercel KV or external DB
    try:
        # You can integrate Vercel KV here
        return 0  # Simplified - always allow first alert
    except:
        return 0

def set_last_alert_time(timestamp):
    """Store last alert timestamp"""
    # In production, store to Vercel KV or external DB
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
        
    except Exception as e:
        return None, str(e)

def fetch_yahoo_fallback():
    """Fallback to Yahoo Finance"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
        params = {"interval": "1m", "range": "1d"}
        
        response = requests.get(url, params=params, timeout=10)
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
        return None, str(e)

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
        return False, "Telegram not configured"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True, "Sent"
        else:
            return False, response.text
    except Exception as e:
        return False, str(e)

def send_trade_alert(data):
    """Send formatted trade alert via Telegram"""
    price = data['price']
    band_width = data['band_width']
    
    # Calculate trade parameters
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

def send_status_update(result):
    """Send status update (no signal)"""
    data = result['data']
    current_time = datetime.now(timezone.utc).strftime('%H:%M GMT')
    
    squeeze_status = "✅ YES" if data['is_squeeze'] else "❌ NO"
    hour_status = "✅ VALID" if data['is_valid_hour'] else "❌ EXCLUDED"
    
    message = f"""
ℹ️ <b>Market Check</b> ({current_time})

💹 Price: {data['price']:.5f}
📊 Band Width: {data['band_width']:.6f}
🔍 Squeeze: {squeeze_status}
⏰ Hour: {data['current_hour']}:00 GMT {hour_status}

<i>No signal - conditions not met</i>
"""
    
    return send_telegram_message(message)

# ============================================================================
# MAIN HANDLER (Vercel Serverless Function)
# ============================================================================

class handler(BaseHTTPRequestHandler):
    """Vercel serverless function handler"""
    
    def do_GET(self):
        """Handle cron job trigger"""
        
        # Log start
        print(f"[{datetime.now(timezone.utc).isoformat()}] Cron job triggered")
        
        # Analyze market
        result = analyze_market()
        
        if result['status'] == 'error':
            # Send error response
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
            return
        
        # Check if signal detected
        data = result['data']
        
        if data['signal']:
            # TRADE SIGNAL!
            print(f"🎯 SIGNAL DETECTED! Price: {data['price']:.5f}, BW: {data['band_width']:.6f}")
            
            # Check cooldown
            last_alert = get_last_alert_time()
            time_since = datetime.now(timezone.utc).timestamp() - last_alert
            
            if time_since >= ALERT_COOLDOWN:
                # Send alert
                success, msg = send_trade_alert(data)
                
                if success:
                    set_last_alert_time(datetime.now(timezone.utc).timestamp())
                    result['alert_sent'] = True
                    print("✅ Alert sent successfully")
                else:
                    result['alert_error'] = msg
                    print(f"❌ Alert failed: {msg}")
            else:
                remaining = int((ALERT_COOLDOWN - time_since) / 60)
                result['cooldown_active'] = True
                result['cooldown_remaining'] = f"{remaining} minutes"
                print(f"⏳ Cooldown active: {remaining} min remaining")
        else:
            # No signal
            reasons = []
            if not data['is_squeeze']:
                reasons.append("No squeeze")
            if not data['is_valid_hour']:
                reasons.append("Invalid hour")
            
            print(f"⏸️ No signal: {', '.join(reasons)}")
        
        # Send success response
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result, indent=2).encode())
        
    def do_POST(self):
        """Handle manual trigger or status check"""
        self.do_GET()

# For local testing
if __name__ == "__main__":
    print("Running local test...")
    result = analyze_market()
    print(json.dumps(result, indent=2))
    
    if result['status'] == 'success' and result['data']['signal']:
        success, msg = send_trade_alert(result['data'])
        print(f"Alert sent: {success} - {msg}")
