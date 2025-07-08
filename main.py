# main.py

import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime
import os
import xlsxwriter
import telegram
from telegram.constants import ParseMode
import asyncio
import html

# --- وارد کردن تنظیمات از فایل config ---
import config

# Helper class for colors
class Color:
    red = 'red'
    green = 'green'
    blue = 'blue'

color = Color()

# --- Indicator Calculation Functions ---
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
        'HA_Open': ha_open,
        'HA_High': ha_high,
        'HA_Low': ha_low,
        'HA_Close': ha_close
    })

def calculate_atr(df, period):
    """Calculates the Average True Range (ATR) using High, Low, and Close."""
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift(1))
    low_close = np.abs(df['Low'] - df['Close'].shift(1))
    tr = pd.DataFrame({'HL': high_low, 'HC': high_close, 'LC': low_close}).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr

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

    df_copy['xcolor'] = np.select(
        [df_copy['pos'] == -1, df_copy['pos'] == 1],
        [str(color.red), str(color.green)],
        default=str(color.blue)
    )

    df_copy['ema'] = calculate_ema(src, 1)

    df_copy['above'] = (df_copy['ema'].shift(1) < df_copy['xATRTrailingStop'].shift(1)) & \
                         (df_copy['ema'] > df_copy['xATRTrailingStop'])

    df_copy['below'] = (df_copy['xATRTrailingStop'].shift(1) < df_copy['ema'].shift(1)) & \
                         (df_copy['xATRTrailingStop'] > df_copy['ema'])

    df_copy['buy_signal'] = (src > df_copy['xATRTrailingStop']) & df_copy['above'] 
    df_copy['sell_signal'] = (src < df_copy['xATRTrailingStop']) & df_copy['below']

    df_copy['barbuy'] = src > df_copy['xATRTrailingStop'] 
    df_copy['barsell'] = src < df_copy['xATRTrailingStop'] 

    return df_copy

# --- Wallex API Functions ---
def get_wallex_markets():
    """Retrieves a list of all active market symbols from Wallex API."""
    url = "https://api.wallex.ir/v1/markets"
    print(f"\n--- DEBUG get_wallex_markets start ---")
    print(f"DEBUG: Attempting to fetch from URL: {url}")
    try:
        response = requests.get(url, timeout=20)
        print(f"DEBUG: HTTP Status Code received: {response.status_code}")
        response.raise_for_status()
        data = response.json()
        
        symbols = []
        if isinstance(data, dict) and data.get('success') is True and 'result' in data:
            market_data_container = data['result']
            if isinstance(market_data_container, dict) and 'symbols' in market_data_container:
                market_data_dict = market_data_container['symbols'] 
                if isinstance(market_data_dict, dict):
                    symbols = list(market_data_dict.keys())
                    filtered_symbols = [s for s in symbols if (s.endswith("TMN") or s.endswith("USDT")) and len(s) >= 5 and s.isupper()]
                    print(f"--- DEBUG get_wallex_markets end (Success) ---")
                    return filtered_symbols
        print(f"Error: Unexpected API response structure from Wallex.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"API ERROR: An HTTP error occurred: {e}")
        return None
    except Exception as e:
        print(f"UNEXPECTED ERROR: An unhandled exception occurred when fetching markets: {e}")
        return None

def get_wallex_candles(symbol, resolution, from_time, to_time):
    """Fetches market candles (OHLC) from Wallex API."""
    base_url = "https://api.wallex.ir/v1/udf/history"
    params = {"symbol": symbol, "resolution": resolution, "from": from_time, "to": to_time}
    print(f"\n--- DEBUG get_wallex_candles for {symbol} start ---")
    try:
        response = requests.get(base_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get('s') == 'ok':
            required_keys = ['t', 'o', 'h', 'l', 'c']
            if not all(key in data and data[key] and len(data[key]) > 0 for key in required_keys):
                print(f"Warning: Missing or empty OHLC data for {symbol}.")
                return None
            
            df = pd.DataFrame({
                'Time': pd.to_datetime(data['t'], unit='s'),
                'Open': [float(o) for o in data['o']],
                'High': [float(h) for h in data['h']],
                'Low': [float(l) for l in data['l']],
                'Close': [float(c) for c in data['c']]
            })
            df.set_index('Time', inplace=True)
            print(f"--- DEBUG get_wallex_candles for {symbol} end (Success) ---")
            return df
        elif data.get('s') == 'no_data':
            print(f"No data available for {symbol} with resolution {resolution}.")
            return None
        else:
            print(f"Error fetching data for {symbol}: {data.get('errmsg', 'Unknown error')}.")
            return None
    except requests.exceptions.RequestException as e:
        print(f"API ERROR for {symbol}: {e}")
        return None
    except Exception as e:
        print(f"UNEXPECTED ERROR for {symbol}: {e}")
        return None

# --- Telegram Notification Function ---
def escape_html_chars(text: str) -> str:
    """Escapes HTML special characters."""
    return html.escape(str(text))

async def send_telegram_message(message: str, chat_id: int, message_thread_id: int):
    """Sends a message to a specific Telegram chat and topic."""
    bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.HTML, 
            message_thread_id=message_thread_id
        )
        print(f"[TELEGRAM] Message sent successfully.")
    except telegram.error.TelegramError as e:
        print(f"[TELEGRAM ERROR] Failed to send message: {e}")

# --- Main Execution Block ---
if __name__ == "__main__":
    output_excel_file = os.path.join(
        config.OUTPUT_DIRECTORY, 
        f"wallex_analysis_{config.RESOLUTION_TO_USE}h_last_{config.RECENT_CANDLES_TO_CHECK}_candles.xlsx"
    )

    if not os.path.exists(config.OUTPUT_DIRECTORY):
        os.makedirs(config.OUTPUT_DIRECTORY)
        print(f"[INFO] Created output directory: {config.OUTPUT_DIRECTORY}")

    print("Fetching all available market symbols from Wallex...")
    all_symbols = get_wallex_markets()

    if all_symbols:
        print(f"\nFound {len(all_symbols)} valid markets to analyze. Starting analysis...")
        print("-" * 50)

        try:
            writer = pd.ExcelWriter(output_excel_file, engine='xlsxwriter')
        except Exception as e:
            print(f"[CRITICAL] Failed to initialize Excel writer: {e}. Exiting.")
            exit()
        
        overall_signal_summary = []
        
        for symbol in all_symbols:
            time.sleep(0.5)
            print(f"\nAnalyzing market: {symbol}")
            
            df_wallex = get_wallex_candles(symbol, config.RESOLUTION_TO_USE, config.START_TIME, config.END_TIME)

            if df_wallex is not None and not df_wallex.empty:
                if len(df_wallex) < config.ATR_PERIOD + config.RECENT_CANDLES_TO_CHECK:
                    print(f"  [INFO] Not enough data for {symbol} ({len(df_wallex)} candles). Skipping.")
                    overall_signal_summary.append({
                        'Symbol': symbol, 'Status': 'Insufficient Data', 'Buy Signals (Last 10)': 0,
                        'Sell Signals (Last 10)': 0, 'Last Signal (Type)': 'N/A', 'Last Signal (Bar Index)': 'N/A',
                        'Current Price': 'N/A'
                    })
                    continue

                results_df = future_monster_indicator(
                    df_wallex.copy(), config.KEY_VALUE, config.ATR_PERIOD, config.USE_HEIKIN_ASHI
                )
                
                recent_signals_df = results_df.tail(config.RECENT_CANDLES_TO_CHECK)
                recent_buy_signals = int(recent_signals_df['buy_signal'].sum())
                recent_sell_signals = int(recent_signals_df['sell_signal'].sum())
                
                last_signal_type = 'None'
                last_signal_bar_index = 'N/A'
                current_price = results_df['Close'].iloc[-1]

                signal_on_current_bar = False
                if results_df['buy_signal'].iloc[-1]:
                    last_signal_type = 'BUY (Current Bar)'
                    signal_on_current_bar = True
                elif results_df['sell_signal'].iloc[-1]:
                    last_signal_type = 'SELL (Current Bar)'
                    signal_on_current_bar = True

                if signal_on_current_bar:
                    emoji = "🟢" if "BUY" in last_signal_type else "🔴"
                    escaped_symbol = escape_html_chars(symbol)
                    escaped_price = escape_html_chars(f"{current_price:.8f}")
                    
                    market_message = (
                        f"{emoji} <b>{last_signal_type.upper()}</b>\n\n"
                        f"<b>Symbol:</b> #{escaped_symbol}\n"
                        f"<b>Timeframe:</b> {config.RESOLUTION_TO_USE} Minute\n"
                        f"<b>Entry Price:</b> {escaped_price}\n"
                    )
                    
                    try:
                        asyncio.run(send_telegram_message(
                            message=market_message,
                            chat_id=config.TELEGRAM_CHAT_ID,
                            message_thread_id=config.TELEGRAM_MESSAGE_THREAD_ID
                        ))
                        time.sleep(1)
                    except Exception as e:
                        print(f"[CRITICAL] Error sending Telegram message for {symbol}: {e}")

                status_text = 'Signals Found' if recent_buy_signals > 0 or recent_sell_signals > 0 else 'No Signals'
                overall_signal_summary.append({
                    'Symbol': symbol, 'Status': status_text, 'Buy Signals (Last 10)': recent_buy_signals,
                    'Sell Signals (Last 10)': recent_sell_signals, 'Last Signal (Type)': last_signal_type,
                    'Last Signal (Bar Index)': 'Current' if signal_on_current_bar else 'N/A',
                    'Current Price': current_price
                })

                try:
                    safe_sheet_name = ''.join(c for c in symbol if c.isalnum())[:31]
                    results_df.to_excel(writer, sheet_name=safe_sheet_name, index=True)
                    # Add formatting if needed
                    print(f"  Successfully saved analysis for {symbol} to sheet '{safe_sheet_name}'.")
                except Exception as e:
                    print(f"  ERROR: Could not save data for {symbol} to Excel: {e}")

            else:
                print(f"  Could not retrieve data for {symbol}. Moving to next market.")
                overall_signal_summary.append({
                    'Symbol': symbol, 'Status': 'Data Fetch Failed', 'Buy Signals (Last 10)': 0,
                    'Sell Signals (Last 10)': 0, 'Last Signal (Type)': 'N/A', 'Last Signal (Bar Index)': 'N/A',
                    'Current Price': 'N/A'
                })
            print("-" * 50)
        
        summary_df = pd.DataFrame(overall_signal_summary)
        if not summary_df.empty:
            summary_df.to_excel(writer, sheet_name='Signal_Summary', index=False)
            print("\n[INFO] Overall signal summary saved to 'Signal_Summary' sheet.")

        writer.close()
        print(f"\nAnalysis complete. All results saved to: {output_excel_file}")

    else:
        print("Failed to retrieve market symbols. Cannot proceed.")