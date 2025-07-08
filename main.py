# main.py

import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime
import os
import telegram
from telegram.constants import ParseMode
import asyncio
import html
import schedule  # <-- کتابخانه برای زمان‌بندی

# --- وارد کردن تنظیمات از فایل config ---
import config

# Helper class for colors
class Color:
    red = 'red'
    green = 'green'
    blue = 'blue'

color = Color()

# --- توابع محاسبه اندیکاتور ---
# (این توابع بدون تغییر باقی می‌مانند)
def calculate_heikin_ashi(df):
    """Calculates Heikin Ashi candles from a DataFrame using OHLC."""
    ha_close = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
    ha_open = pd.Series(0.0, index=df.index)
    ha_open.iloc[0] = (df['Open'].iloc[0] + df['Close'].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2
    ha_high = pd.DataFrame({'High': df['High'], 'HA_Open': ha_open, 'HA_Close': ha_close}).max(axis=1)
    ha_low = pd.DataFrame({'Low': df['Low'], 'HA_Open': ha_open, 'HA_Close': ha_close}).min(axis=1)
    return pd.DataFrame({
        'HA_Open': ha_open, 'HA_High': ha_high, 'HA_Low': ha_low, 'HA_Close': ha_close
    })

def calculate_atr(df, period):
    """Calculates the Average True Range (ATR) using High, Low, and Close."""
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift(1))
    low_close = np.abs(df['Low'] - df['Close'].shift(1))
    tr = pd.DataFrame({'HL': high_low, 'HC': high_close, 'LC': low_close}).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def calculate_ema(series, period):
    """Calculates the Exponential Moving Average (EMA)."""
    return series.ewm(span=period, adjust=False).mean()

def future_monster_indicator(df, key_value, atr_period, use_heikin_ashi):
    """Calculates the Future Monster indicator signals."""
    df_copy = df.copy()
    if use_heikin_ashi:
        ha_df = calculate_heikin_ashi(df_copy)
        df_for_atr = ha_df.rename(columns={'HA_High': 'High', 'HA_Low': 'Low', 'HA_Close': 'Close'})
        src = ha_df['HA_Close']
    else:
        df_for_atr = df_copy
        src = df_copy['Close']
    df_copy['xATR'] = calculate_atr(df_for_atr, atr_period)
    df_copy['nLoss'] = key_value * df_copy['xATR']
    xATRTrailingStop = pd.Series(0.0, index=df_copy.index)
    pos = pd.Series(0, index=df_copy.index)
    for i in range(len(df_copy)):
        current_src = src.iloc[i]
        current_nLoss = df_copy['nLoss'].iloc[i]
        if i == 0:
            xATRTrailingStop.iloc[i] = current_src - current_nLoss
            pos.iloc[i] = 1
        else:
            prev_xATRTrailingStop_val = xATRTrailingStop.iloc[i-1]
            prev_src_val = src.iloc[i-1]
            prev_pos_val = pos.iloc[i-1]
            if current_src > prev_xATRTrailingStop_val and prev_src_val > prev_xATRTrailingStop_val:
                xATRTrailingStop.iloc[i] = max(prev_xATRTrailingStop_val, current_src - current_nLoss)
            elif current_src < prev_xATRTrailingStop_val and prev_src_val < prev_xATRTrailingStop_val:
                xATRTrailingStop.iloc[i] = min(prev_xATRTrailingStop_val, current_src + current_nLoss)
            elif current_src > prev_xATRTrailingStop_val:
                xATRTrailingStop.iloc[i] = current_src - current_nLoss
            else:
                xATRTrailingStop.iloc[i] = current_src + current_nLoss
            if prev_src_val < prev_xATRTrailingStop_val and current_src > prev_xATRTrailingStop_val:
                pos.iloc[i] = 1
            elif prev_src_val > prev_xATRTrailingStop_val and current_src < prev_xATRTrailingStop_val:
                pos.iloc[i] = -1
            else:
                pos.iloc[i] = prev_pos_val
    df_copy['xATRTrailingStop'] = xATRTrailingStop
    df_copy['pos'] = pos
    df_copy['ema'] = calculate_ema(src, 1)
    df_copy['above'] = (df_copy['ema'].shift(1) < df_copy['xATRTrailingStop'].shift(1)) & (df_copy['ema'] > df_copy['xATRTrailingStop'])
    df_copy['below'] = (df_copy['xATRTrailingStop'].shift(1) < df_copy['ema'].shift(1)) & (df_copy['xATRTrailingStop'] > df_copy['ema'])
    df_copy['buy_signal'] = (src > df_copy['xATRTrailingStop']) & df_copy['above']
    df_copy['sell_signal'] = (src < df_copy['xATRTrailingStop']) & df_copy['below']
    return df_copy

# --- توابع مربوط به API و تلگرام ---
# (این توابع بدون تغییر باقی می‌مانند)
def get_wallex_markets():
    """Retrieves a list of all active market symbols from Wallex API."""
    url = "https://api.wallex.ir/v1/markets"
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get('success') is True and 'result' in data:
            market_data_container = data['result']
            if isinstance(market_data_container, dict) and 'symbols' in market_data_container:
                symbols = list(market_data_container['symbols'].keys())
                return [s for s in symbols if (s.endswith("TMN") or s.endswith("USDT")) and len(s) >= 5 and s.isupper()]
        print("Error: Unexpected API response structure from Wallex.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"API ERROR fetching markets: {e}")
        return None

def get_wallex_candles(symbol, resolution, from_time, to_time):
    """Fetches market candles (OHLC) from Wallex API."""
    base_url = "https://api.wallex.ir/v1/udf/history"
    params = {"symbol": symbol, "resolution": resolution, "from": from_time, "to": to_time}
    try:
        response = requests.get(base_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('s') == 'ok':
            if not all(key in data and data[key] for key in ['t', 'o', 'h', 'l', 'c']):
                return None
            return pd.DataFrame({
                'Time': pd.to_datetime(data['t'], unit='s'),
                'Open': [float(o) for o in data['o']],
                'High': [float(h) for h in data['h']],
                'Low': [float(l) for l in data['l']],
                'Close': [float(c) for c in data['c']]
            }).set_index('Time')
        return None
    except requests.exceptions.RequestException as e:
        print(f"API ERROR for {symbol}: {e}")
        return None

def escape_html_chars(text: str) -> str:
    """Escapes HTML special characters."""
    return html.escape(str(text))

async def send_telegram_message(message: str):
    """Sends a message to a specific Telegram chat and topic."""
    bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            message_thread_id=config.TELEGRAM_MESSAGE_THREAD_ID
        )
        print(f"[TELEGRAM] Message sent successfully.")
    except telegram.error.TelegramError as e:
        print(f"[TELEGRAM ERROR] Failed to send message: {e}")

# --- تابع اصلی برنامه ---
def run_analysis():
    """
    منطق اصلی برنامه برای تحلیل بازار و ارسال سیگنال.
    """
    print(f"\n{'='*20} | شروع تحلیل جدید | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {'='*20}")
    
    all_symbols = get_wallex_markets()
    if not all_symbols:
        print("Could not retrieve market symbols. Skipping this run.")
        return

    print(f"Found {len(all_symbols)} markets. Starting analysis...")
    print("-" * 70)

    for symbol in all_symbols:
        time.sleep(0.5)  # To avoid hitting API rate limits
        print(f"Analyzing: {symbol}")
        
        df_wallex = get_wallex_candles(symbol, config.RESOLUTION_TO_USE, config.START_TIME, config.END_TIME)

        if df_wallex is None or df_wallex.empty:
            print(f"-> No data for {symbol}. Skipping.")
            continue

        if len(df_wallex) < config.ATR_PERIOD + 2:
            print(f"-> Not enough data for {symbol} ({len(df_wallex)} candles). Skipping.")
            continue

        results_df = future_monster_indicator(
            df_wallex.copy(), config.KEY_VALUE, config.ATR_PERIOD, config.USE_HEIKIN_ASHI
        )
        
        # فقط کندل آخر (جدیدترین کندل) را برای سیگنال بررسی می‌کنیم
        last_candle = results_df.iloc[-1]
        
        signal_type = None
        if last_candle['buy_signal']:
            signal_type = 'BUY'
        elif last_candle['sell_signal']:
            signal_type = 'SELL'
            
        if signal_type:
            current_price = last_candle['Close']
            emoji = "🟢" if signal_type == 'BUY' else "🔴"
            escaped_symbol = escape_html_chars(symbol)
            escaped_price = escape_html_chars(f"{current_price:.8f}")
            
            message = (
                f"{emoji} <b>{signal_type} SIGNAL!</b>\n\n"
                f"<b>Symbol:</b> #{escaped_symbol}\n"
                f"<b>Timeframe:</b> {config.RESOLUTION_TO_USE} Minute\n"
                f"<b>Price:</b> {escaped_price}\n"
            )
            
            try:
                print(f"-> SIGNAL FOUND! Sending notification for {symbol}...")
                asyncio.run(send_telegram_message(message))
            except Exception as e:
                print(f"[CRITICAL] Error sending Telegram message for {symbol}: {e}")
    
    print(f"\n{'='*20} | تحلیل تمام شد | منتظر اجرای بعدی... | {'='*20}")

# --- بلوک اصلی اجرا و زمان‌بندی ---
if __name__ == "__main__":
    print("🚀 Bot started! Running first analysis...")
    # اجرای تابع برای اولین بار بلافاصله پس از شروع اسکریپت
    run_analysis()

    # تنظیم زمان‌بندی برای اجرای تابع هر 1 ساعت
    schedule.every(1).hour.do(run_analysis)
    
    print("\n✅ Analysis scheduled successfully. Will run every hour.")
    print("Press Ctrl+C to stop the bot.")

    while True:
        schedule.run_pending()
        time.sleep(1)
