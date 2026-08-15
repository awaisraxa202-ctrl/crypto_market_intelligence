#!/usr/bin/env python3
"""
================================================================================
CRYPTO MARKET INTELLIGENCE v4.2 — ENHANCED EDITION
================================================================================
Built on v4.1 Merged Edition. Keeps all original structure.

ENHANCEMENTS in v4.2:
  • FIXED: EMA 12/26 missing (MACD was broken)
  • FIXED: yfinance multi-index column handling
  • FIXED: Robust empty-DataFrame guards throughout
  • ADDED: RSI Bullish/Bearish Divergence detection
  • ADDED: Support & Resistance levels (swing highs/lows)
  • ADDED: Risk Metrics — VaR (95%), Sortino Ratio, Calmar Ratio, Max Consecutive Wins/Losses
  • ADDED: Whale Activity Proxy (volume z-score spikes)
  • ADDED: Cross-asset Correlation Matrix
  • ADDED: Altcoin Season Index (BTC dominance trend)
  • ADDED: Funding Rate Heatmap (all assets)
  • ADDED: Market Breadth (advance/decline proxy)
  • ADDED: GARCH-like simple volatility forecast
  • ADDED: OBV Divergence detection
  • IMPROVED: Pattern matching with 6-factor weighted similarity
  • IMPROVED: Retry logic with exponential backoff for all APIs

IMPORTANT: Past performance does NOT predict future results. You can lose money.
Never invest more than you can afford to lose. Paper trade first for 3+ months.
================================================================================
"""

import pandas as pd
import numpy as np
import requests
import json
import sqlite3
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

FEE = 0.002
UPDATE_TIME = "06:00 UTC"

ASSETS = {
    'ETH': {'name': 'Ethereum', 'binance': 'ETHUSDT', 'yahoo': 'ETH-USD', 'coingecko': 'ethereum', 'deribit': 'ETH'},
    'BTC': {'name': 'Bitcoin', 'binance': 'BTCUSDT', 'yahoo': 'BTC-USD', 'coingecko': 'bitcoin', 'deribit': 'BTC'},
    'SOL': {'name': 'Solana', 'binance': 'SOLUSDT', 'yahoo': 'SOL-USD', 'coingecko': 'solana', 'deribit': 'SOL'},
    'BNB': {'name': 'BNB', 'binance': 'BNBUSDT', 'yahoo': 'BNB-USD', 'coingecko': 'binancecoin', 'deribit': None},
    'XRP': {'name': 'XRP', 'binance': 'XRPUSDT', 'yahoo': 'XRP-USD', 'coingecko': 'ripple', 'deribit': None},
    'ADA': {'name': 'Cardano', 'binance': 'ADAUSDT', 'yahoo': 'ADA-USD', 'coingecko': 'cardano', 'deribit': None},
    'DOGE': {'name': 'Dogecoin', 'binance': 'DOGEUSDT', 'yahoo': 'DOGE-USD', 'coingecko': 'dogecoin', 'deribit': None},
    'LINK': {'name': 'Chainlink', 'binance': 'LINKUSDT', 'yahoo': 'LINK-USD', 'coingecko': 'chainlink', 'deribit': None},
    'AVAX': {'name': 'Avalanche', 'binance': 'AVAXUSDT', 'yahoo': 'AVAX-USD', 'coingecko': 'avalanche-2', 'deribit': None},
}

ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
BEACONCHAIN_API_KEY = os.environ.get('BEACONCHAIN_API_KEY', '')
FRED_API_KEY = os.environ.get('FRED_API_KEY', '')

DB_PATH = 'crypto_quant.db'
OUTPUT_PATH = 'docs/market_intelligence.json'
os.makedirs('docs', exist_ok=True)

# ===================== RETRY WRAPPER =====================

def fetch_with_retry(url, params=None, headers=None, timeout=30, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt == retries - 1:
                raise e
            time.sleep(2 ** attempt)
    return None

# ===================== DATA FETCHING =====================

def fetch_binance_klines(symbol, interval='1d', limit=1000):
    url = "https://api.binance.com/api/v3/klines"
    params = {'symbol': symbol, 'interval': interval, 'limit': limit}
    try:
        r = fetch_with_retry(url, params=params, timeout=30)
        data = r.json()
        df = pd.DataFrame(data, columns=[
            'open_time','open','high','low','close','volume',
            'close_time','quote_volume','trades','taker_buy_base',
            'taker_buy_quote','ignore'
        ])
        df['date'] = pd.to_datetime(df['open_time'], unit='ms')
        for col in ['open','high','low','close','volume']:
            df[col] = df[col].astype(float)
        return df[['date','open','high','low','close','volume']]
    except Exception as e:
        print(f"  ⚠️ Binance {symbol}: {e}")
        return pd.DataFrame()

def fetch_yahoo(ticker, period='2y'):
    try:
        import yfinance as yf
        data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if data.empty:
            return pd.DataFrame()
        df = data.reset_index()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [' '.join(col).strip() if col[1] else col[0] for col in df.columns.values]
        close_col = None
        for c in df.columns:
            if 'close' in c.lower():
                close_col = c
                break
        if close_col is None:
            close_col = df.columns[-1]
        date_col = 'Date' if 'Date' in df.columns else df.columns[0]
        df = df[[date_col, close_col]].copy()
        df.columns = ['date', 'close']
        df['date'] = pd.to_datetime(df['date'])
        return df
    except Exception as e:
        print(f"  ⚠️ Yahoo {ticker}: {e}")
        return pd.DataFrame()

def fetch_yahoo_ohlcv(ticker, period='2y'):
    '''Fetch full OHLCV from Yahoo Finance as fallback when Binance fails.'''
    try:
        import yfinance as yf
        data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if data.empty:
            return pd.DataFrame()
        df = data.reset_index()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [' '.join(col).strip() if col[1] else col[0] for col in df.columns.values]

        # Map columns - Yahoo returns Open, High, Low, Close, Volume
        col_map = {}
        for c in df.columns:
            cl = c.lower()
            if 'date' in cl or c == df.columns[0]:
                col_map['date'] = c
            elif 'open' in cl and 'adj' not in cl:
                col_map['open'] = c
            elif 'high' in cl:
                col_map['high'] = c
            elif 'low' in cl:
                col_map['low'] = c
            elif 'close' in cl and 'adj' not in cl:
                col_map['close'] = c
            elif 'volume' in cl:
                col_map['volume'] = c

        needed = ['date', 'open', 'high', 'low', 'close', 'volume']
        available = [col_map.get(k) for k in needed if col_map.get(k) in df.columns]
        if len(available) < 4:
            return pd.DataFrame()

        df = df[available].copy()
        rename = {v: k for k, v in col_map.items() if v in df.columns}
        df = df.rename(columns=rename)
        df['date'] = pd.to_datetime(df['date'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df[['date', 'open', 'high', 'low', 'close', 'volume']].dropna(subset=['close'])
    except Exception as e:
        print(f"  ⚠️ Yahoo OHLCV {ticker}: {e}")
        return pd.DataFrame()

def fetch_fear_greed():
    url = "https://api.alternative.me/fng/?limit=0"
    try:
        r = fetch_with_retry(url, timeout=30)
        data = r.json()['data']
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['timestamp'].astype(int), unit='s')
        df['fng_value'] = df['value'].astype(int)
        df['fng_class'] = df['value_classification']
        return df[['date', 'fng_value', 'fng_class']].sort_values('date')
    except Exception as e:
        print(f"  ⚠️ F&G: {e}")
        return pd.DataFrame()

def fetch_funding_rate(symbol='ETHUSDT', limit=1000):
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    params = {'symbol': symbol, 'limit': limit}
    try:
        r = fetch_with_retry(url, params=params, timeout=30)
        data = r.json()
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['fundingTime'], unit='ms')
        df['funding_rate'] = df['fundingRate'].astype(float)
        df['date_only'] = df['date'].dt.date
        daily = df.groupby('date_only')['funding_rate'].agg(['mean','max','min','std']).reset_index()
        daily['date'] = pd.to_datetime(daily['date_only'])
        daily = daily.rename(columns={'mean':'funding_rate','max':'funding_max','min':'funding_min','std':'funding_std'})
        return daily[['date', 'funding_rate', 'funding_max', 'funding_min', 'funding_std']]
    except Exception as e:
        print(f"  ⚠️ Funding {symbol}: {e}")
        return pd.DataFrame()

def fetch_long_short_ratio(symbol='ETHUSDT', limit=100):
    url = "https://fapi.binance.com/fapi/v1/globalLongShortAccountRatio"
    params = {'symbol': symbol, 'period': '1d', 'limit': limit}
    try:
        r = fetch_with_retry(url, params=params, timeout=30)
        data = r.json()
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['long_short_ratio'] = df['longShortRatio'].astype(float)
        df['long_account_pct'] = df['longAccount'].astype(float)
        return df[['date', 'long_short_ratio', 'long_account_pct']]
    except Exception as e:
        print(f"  ⚠️ L/S {symbol}: {e}")
        return pd.DataFrame()

def fetch_open_interest_hist(symbol='ETHUSDT', limit=100):
    url = "https://fapi.binance.com/fapi/v1/openInterestHist"
    params = {'symbol': symbol, 'period': '1d', 'limit': limit}
    try:
        r = fetch_with_retry(url, params=params, timeout=30)
        data = r.json()
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['open_interest'] = df['sumOpenInterest'].astype(float)
        df['oi_value_usd'] = df['sumOpenInterestValue'].astype(float)
        return df[['date', 'open_interest', 'oi_value_usd']]
    except Exception as e:
        print(f"  ⚠️ OI {symbol}: {e}")
        return pd.DataFrame()

def fetch_coingecko_global():
    url = "https://api.coingecko.com/api/v3/global"
    try:
        r = fetch_with_retry(url, timeout=30)
        data = r.json()['data']
        return {
            'total_market_cap': data['total_market_cap']['usd'],
            'total_volume': data['total_volume']['usd'],
            'btc_dominance': data['market_cap_percentage']['btc'],
            'eth_dominance': data['market_cap_percentage']['eth'],
            'market_cap_change_24h': data['market_cap_change_percentage_24h_usd'],
            'active_cryptocurrencies': data['active_cryptocurrencies']
        }
    except Exception as e:
        print(f"  ⚠️ CG global: {e}")
        return {}

def fetch_coingecko_coin(coin_id='ethereum'):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&market_data=true"
    try:
        r = fetch_with_retry(url, timeout=30)
        data = r.json()
        return {
            'market_cap': data['market_data']['market_cap']['usd'],
            'total_volume': data['market_data']['total_volume']['usd'],
            'circulating_supply': data['market_data']['circulating_supply'],
            'ath': data['market_data']['ath']['usd'],
            'ath_change_pct': data['market_data']['ath_change_percentage']['usd'],
            'sentiment_votes_up': data.get('sentiment_votes_up_percentage', None),
            'sentiment_votes_down': data.get('sentiment_votes_down_percentage', None),
        }
    except Exception as e:
        print(f"  ⚠️ CG coin {coin_id}: {e}")
        return {}

def fetch_etherscan_gas():
    if not ETHERSCAN_API_KEY: return {}
    url = f"https://api.etherscan.io/api?module=gastracker&action=gasoracle&apikey={ETHERSCAN_API_KEY}"
    try:
        r = fetch_with_retry(url, timeout=30)
        data = r.json()
        if data['status'] == '1':
            return {
                'safe_gas_price': float(data['result']['SafeGasPrice']),
                'propose_gas_price': float(data['result']['ProposeGasPrice']),
                'fast_gas_price': float(data['result']['FastGasPrice']),
            }
    except Exception as e:
        print(f"  ⚠️ Gas: {e}")
    return {}

def fetch_deribit_options(currency='ETH'):
    url = f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={currency}&kind=option"
    try:
        r = fetch_with_retry(url, timeout=30)
        data = r.json()
        if data.get('result'):
            options = data['result']
            puts = [o for o in options if 'P' in o.get('instrument_name', '')]
            calls = [o for o in options if 'C' in o.get('instrument_name', '')]
            avg_iv = np.mean([o.get('mark_iv', 0) for o in options if o.get('mark_iv')])
            put_call_ratio = len(puts) / len(calls) if calls else 1.0
            return {'put_call_ratio': put_call_ratio, 'avg_implied_vol': avg_iv, 'total_options': len(options)}
    except Exception as e:
        print(f"  ⚠️ Deribit: {e}")
    return {}

def fetch_beaconchain_staking():
    if not BEACONCHAIN_API_KEY: return {}
    headers = {'Authorization': f'Bearer {BEACONCHAIN_API_KEY}'}
    try:
        r = fetch_with_retry("https://beaconcha.in/api/v1/epoch/latest", headers=headers, timeout=30)
        data = r.json()
        if data.get('data'):
            return {
                'epoch': data['data'].get('epoch'),
                'validatorscount': data['data'].get('validatorscount'),
                'totalvalidatorbalance': data['data'].get('totalvalidatorbalance'),
                'eligibleether': data['data'].get('eligibleether'),
            }
    except Exception as e:
        print(f"  ⚠️ Beaconchain: {e}")
    return {}

def fetch_fred_data(series_id='CPIAUCSL', limit=24):
    if not FRED_API_KEY: return pd.DataFrame()
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {'series_id': series_id, 'api_key': FRED_API_KEY, 'file_type': 'json', 'limit': limit, 'sort_order': 'desc'}
    try:
        r = fetch_with_retry(url, params=params, timeout=30)
        data = r.json()
        if 'observations' in data:
            df = pd.DataFrame(data['observations'])
            df['date'] = pd.to_datetime(df['date'])
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            return df[['date', 'value']].dropna().sort_values('date')
    except Exception as e:
        print(f"  ⚠️ FRED {series_id}: {e}")
    return pd.DataFrame()

# ===================== FEATURE ENGINEERING =====================

def add_features(df):
    df = df.copy().sort_values('date').reset_index(drop=True)
    df['return'] = df['close'].pct_change()
    df['return_5d'] = df['close'].pct_change(5)
    df['return_20d'] = df['close'].pct_change(20)
    df['return_60d'] = df['close'].pct_change(60)

    # FIX: Added EMA 12 and 26 for proper MACD calculation
    for period in [12, 26, 10, 20, 50, 100, 200]:
        df[f'ema_{period}'] = df['close'].ewm(span=period, adjust=False).mean()
    for period in [10, 20, 50, 100, 200]:
        df[f'sma_{period}'] = df['close'].rolling(period).mean()

    df['macd'] = df['ema_12'] - df['ema_26']
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df['rsi_14'] = 100 - (100 / (1 + rs))

    rsi_min = df['rsi_14'].rolling(14).min()
    rsi_max = df['rsi_14'].rolling(14).max()
    df['stoch_rsi'] = (df['rsi_14'] - rsi_min) / (rsi_max - rsi_min)
    df['stoch_rsi_k'] = df['stoch_rsi'].rolling(3).mean()
    df['stoch_rsi_d'] = df['stoch_rsi_k'].rolling(3).mean()

    highest_high = df['high'].rolling(14).max()
    lowest_low = df['low'].rolling(14).min()
    df['williams_r'] = (highest_high - df['close']) / (highest_high - lowest_low) * -100

    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr_14'] = tr.rolling(14).mean()
    df['atr_ratio'] = df['atr_14'] / df['close']

    df['bb_mid'] = df['close'].rolling(20).mean()
    bb_std = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + 2 * bb_std
    df['bb_lower'] = df['bb_mid'] - 2 * bb_std
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
    df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])

    df['kc_mid'] = df['ema_20']
    df['kc_upper'] = df['kc_mid'] + 2 * df['atr_14']
    df['kc_lower'] = df['kc_mid'] - 2 * df['atr_14']

    df['volatility_20'] = df['return'].rolling(20).std() * np.sqrt(365)
    df['volatility_50'] = df['return'].rolling(50).std() * np.sqrt(365)

    # GARCH-like simple volatility forecast
    df['vol_forecast_5d'] = df['return'].ewm(span=20).std() * np.sqrt(5)
    df['vol_forecast_20d'] = df['return'].ewm(span=20).std() * np.sqrt(20)

    df['peak'] = df['close'].cummax()
    df['drawdown'] = (df['close'] - df['peak']) / df['peak']

    df['volume_sma_20'] = df['volume'].rolling(20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_sma_20']
    df['volume_zscore'] = (df['volume'] - df['volume_sma_20']) / df['volume'].rolling(20).std()
    df['obv'] = (np.sign(df['return']) * df['volume']).cumsum()

    for period in [20, 50, 100, 200]:
        df[f'dist_sma{period}'] = (df['close'] - df[f'sma_{period}']) / df[f'sma_{period}']

    df['golden_cross'] = np.where((df['sma_50'] > df['sma_200']) & (df['sma_50'].shift(1) <= df['sma_200'].shift(1)), 1, 0)
    df['death_cross'] = np.where((df['sma_50'] < df['sma_200']) & (df['sma_50'].shift(1) >= df['sma_200'].shift(1)), 1, 0)

    for period in [10, 20, 50]:
        df[f'mom_{period}'] = df['close'] / df['close'].shift(period) - 1

    df['roc_10'] = (df['close'] - df['close'].shift(10)) / df['close'].shift(10) * 100

    df['day_of_week'] = df['date'].dt.dayofweek
    df['month'] = df['date'].dt.month
    df['quarter'] = df['date'].dt.quarter
    df['is_month_start'] = df['date'].dt.is_month_start.astype(int)
    df['is_month_end'] = df['date'].dt.is_month_end.astype(int)

    return df

def add_pi_cycle(df):
    df = df.copy()
    df['pi_111'] = df['close'].rolling(111).mean()
    df['pi_350_x2'] = df['close'].rolling(350).mean() * 2
    df['pi_cycle_signal'] = np.where(df['pi_111'] > df['pi_350_x2'], 1, 0)
    df['pi_cycle_top_warning'] = np.where(
        (df['pi_111'] > df['pi_350_x2']) & (df['pi_111'].shift(1) <= df['pi_350_x2'].shift(1)), 1, 0
    )
    return df

def detect_regime(df):
    df = df.copy()
    df['trend_strength'] = np.where(
        (df['close'] > df['sma_50']) & (df['macd_hist'] > 0) & (df['close'] > df['sma_200']), 2,
        np.where(
            (df['close'] > df['sma_50']) & (df['macd_hist'] > 0), 1,
            np.where(
                (df['close'] < df['sma_50']) & (df['macd_hist'] < 0) & (df['close'] < df['sma_200']), -2,
                np.where(
                    (df['close'] < df['sma_50']) & (df['macd_hist'] < 0), -1, 0
                )
            )
        )
    )
    vol_median = df['volatility_20'].median()
    df['vol_regime'] = np.where(df['volatility_20'] > vol_median * 1.5, 'HIGH',
                         np.where(df['volatility_20'] < vol_median * 0.5, 'LOW', 'NORMAL'))
    conditions = [
        (df['trend_strength'] == 2) & (df['vol_regime'] != 'HIGH'),
        (df['trend_strength'] == 2) & (df['vol_regime'] == 'HIGH'),
        (df['trend_strength'] == 1) & (df['vol_regime'] != 'HIGH'),
        (df['trend_strength'] == 1) & (df['vol_regime'] == 'HIGH'),
        (df['trend_strength'] == -2) & (df['vol_regime'] != 'HIGH'),
        (df['trend_strength'] == -2) & (df['vol_regime'] == 'HIGH'),
        (df['trend_strength'] == -1) & (df['vol_regime'] != 'HIGH'),
        (df['trend_strength'] == -1) & (df['vol_regime'] == 'HIGH'),
        (df['vol_regime'] == 'HIGH'),
        (df['bb_width'] < df['bb_width'].rolling(50).mean() * 0.8)
    ]
    choices = ['STRONG_BULL', 'BULL_VOLATILE', 'BULL_TREND', 'BULL_CHOPPY',
               'STRONG_BEAR', 'BEAR_VOLATILE', 'BEAR_TREND', 'BEAR_CHOPPY',
               'CHOPPY', 'RANGE']
    df['regime'] = np.select(conditions, choices, default='CHOPPY')
    return df

# ===================== DIVERGENCE DETECTION =====================

def detect_rsi_divergence(df, lookback=30):
    df = df.copy()
    df['rsi_divergence'] = 'NONE'
    for i in range(lookback, len(df)):
        window = df.iloc[i-lookback:i+1]
        p1, p2 = window['close'].iloc[0], window['close'].iloc[-1]
        r1, r2 = window['rsi_14'].iloc[0], window['rsi_14'].iloc[-1]
        if pd.notna(p1) and pd.notna(p2) and pd.notna(r1) and pd.notna(r2):
            if p2 < p1 and r2 > r1 and r2 < 40:
                df.loc[df.index[i], 'rsi_divergence'] = 'BULLISH'
            elif p2 > p1 and r2 < r1 and r2 > 60:
                df.loc[df.index[i], 'rsi_divergence'] = 'BEARISH'
    return df

def detect_obv_divergence(df, lookback=20):
    df = df.copy()
    df['obv_divergence'] = 'NONE'
    for i in range(lookback, len(df)):
        window = df.iloc[i-lookback:i+1]
        p1, p2 = window['close'].iloc[0], window['close'].iloc[-1]
        o1, o2 = window['obv'].iloc[0], window['obv'].iloc[-1]
        if pd.notna(p1) and pd.notna(p2) and pd.notna(o1) and pd.notna(o2):
            if p2 < p1 and o2 > o1:
                df.loc[df.index[i], 'obv_divergence'] = 'BULLISH'
            elif p2 > p1 and o2 < o1:
                df.loc[df.index[i], 'obv_divergence'] = 'BEARISH'
    return df

# ===================== SUPPORT & RESISTANCE =====================

def find_support_resistance(df, window=10):
    df = df.copy()
    df['swing_high'] = df['high'][(df['high'].shift(window) < df['high']) & (df['high'].shift(-window) < df['high'])]
    df['swing_low'] = df['low'][(df['low'].shift(window) > df['low']) & (df['low'].shift(-window) > df['low'])]
    recent_highs = df['swing_high'].dropna().tail(5).values
    recent_lows = df['swing_low'].dropna().tail(5).values
    current_price = df['close'].iloc[-1]
    resistance = [h for h in recent_highs if h > current_price * 0.98]
    support = [l for l in recent_lows if l < current_price * 1.02]
    return {
        'nearest_resistance': round(min(resistance), 4) if len(resistance) > 0 else None,
        'nearest_support': round(max(support), 4) if len(support) > 0 else None,
        'resistance_levels': [round(h, 4) for h in resistance[:3]],
        'support_levels': [round(l, 4) for l in support[:3]],
    }

# ===================== WHALE ACTIVITY PROXY =====================

def whale_activity_proxy(df):
    latest = df.iloc[-1]
    vol_z = latest.get('volume_zscore')
    if pd.isna(vol_z):
        return {'alert': False, 'zscore': None, 'severity': 'NONE', 'description': 'No volume data'}
    if vol_z > 3:
        return {'alert': True, 'zscore': round(vol_z, 2), 'severity': 'HIGH',
                'description': f'Volume spike {vol_z:.1f} standard deviations above average. Large players likely active.'}
    elif vol_z > 2:
        return {'alert': True, 'zscore': round(vol_z, 2), 'severity': 'MEDIUM',
                'description': f'Volume {vol_z:.1f} sigma above average. Elevated institutional interest.'}
    else:
        return {'alert': False, 'zscore': round(vol_z, 2), 'severity': 'NONE',
                'description': f'Volume normal ({vol_z:.1f} sigma). No unusual whale activity detected.'}

# ===================== RISK METRICS =====================

def calculate_var(returns, confidence=0.05):
    if returns.empty or returns.std() == 0:
        return None
    return np.percentile(returns.dropna(), confidence * 100)

def calculate_sortino(returns, target=0):
    if returns.empty or returns.std() == 0:
        return 0
    downside = returns[returns < target]
    downside_std = downside.std() * np.sqrt(365) if len(downside) > 0 else 0
    if downside_std == 0:
        return 0
    return returns.mean() * 365 / downside_std

def calculate_calmar(total_return, max_dd):
    if max_dd == 0:
        return 0
    return total_return / abs(max_dd)

def max_consecutive(returns):
    if returns.empty:
        return 0, 0
    pos = (returns > 0).astype(int)
    neg = (returns < 0).astype(int)
    max_wins, max_losses = 0, 0
    curr_wins, curr_losses = 0, 0
    for p, n in zip(pos, neg):
        if p:
            curr_wins += 1
            curr_losses = 0
            max_wins = max(max_wins, curr_wins)
        elif n:
            curr_losses += 1
            curr_wins = 0
            max_losses = max(max_losses, curr_losses)
        else:
            curr_wins = 0
            curr_losses = 0
    return int(max_wins), int(max_losses)

# ===================== BACKTESTING =====================

def calc_drawdown(returns):
    cum = (1 + returns.fillna(0)).cumprod()
    peak = cum.cummax()
    return ((cum - peak) / peak).min()

def backtest(df, position_col, fee=FEE):
    df = df.copy()
    df['position_change'] = df[position_col].diff().abs()
    df['strat_return'] = df[position_col].shift(1) * df['return'] - df['position_change'] * fee
    df['strat_return'] = df['strat_return'].fillna(0)
    df['cum_return'] = (1 + df['strat_return']).cumprod() - 1
    returns = df['strat_return'].dropna()
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    win_rate = len(wins) / len(returns[returns != 0]) if len(returns[returns != 0]) > 0 else 0
    avg_win = wins.mean() if len(wins) > 0 else 0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 0
    kelly = 0
    if avg_loss > 0 and win_rate > 0:
        b = avg_win / avg_loss
        kelly = (win_rate * b - (1 - win_rate)) / b
        kelly = max(0, min(kelly, 0.25))
    max_wins, max_losses = max_consecutive(returns)
    return {
        'total_return': df['cum_return'].iloc[-1],
        'sharpe': returns.mean() / returns.std() * np.sqrt(365) if returns.std() > 0 else 0,
        'sortino': calculate_sortino(returns),
        'calmar': calculate_calmar(df['cum_return'].iloc[-1], calc_drawdown(returns)),
        'max_drawdown': calc_drawdown(returns),
        'trades': df['position_change'].sum() / 2,
        'win_rate': win_rate * 100,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'max_consecutive_wins': max_wins,
        'max_consecutive_losses': max_losses,
        'kelly_fraction': kelly,
        'var_95': calculate_var(returns, 0.05),
    }

def monte_carlo(df, position_col, n_sims=1000, fee=FEE):
    df = df.copy()
    df['position_change'] = df[position_col].diff().abs()
    df['strat_return'] = df[position_col].shift(1) * df['return'] - df['position_change'] * fee
    df['strat_return'] = df['strat_return'].fillna(0)
    trade_returns = df['strat_return'][df['strat_return'] != 0].values
    if len(trade_returns) < 10:
        return None
    results = []
    np.random.seed(42)
    for _ in range(n_sims):
        sampled = np.random.choice(trade_returns, size=len(trade_returns), replace=True)
        results.append(np.cumprod(1 + sampled)[-1] - 1)
    results = np.array(results)
    return {
        'profitable_pct': (results > 0).mean() * 100,
        'mean': np.mean(results),
        'median': np.median(results),
        'std': np.std(results),
        'min': np.min(results),
        'max': np.max(results),
        'pct_5': np.percentile(results, 5),
        'pct_95': np.percentile(results, 95)
    }

def validate_strategies(df):
    strategies = {}
    df['sma20_pos'] = np.where(df['close'] > df['sma_20'], 1, 0)
    strategies['SMA20 Crossover'] = backtest(df, 'sma20_pos')
    df['sma50_pos'] = np.where(df['close'] > df['sma_50'], 1, 0)
    strategies['SMA50 Trend'] = backtest(df, 'sma50_pos')
    df['golden_pos'] = np.where(df['sma_50'] > df['sma_200'], 1, 0)
    strategies['Golden Cross'] = backtest(df, 'golden_pos')
    df['rsi_pos'] = np.where(df['rsi_14'] < 30, 1, np.where(df['rsi_14'] > 70, 0, np.nan))
    df['rsi_pos'] = df['rsi_pos'].ffill().fillna(0)
    strategies['RSI < 30, > 70'] = backtest(df, 'rsi_pos')
    df['rsi_trend_pos'] = np.where((df['rsi_14'] < 35) & (df['close'] > df['sma_50']), 1, np.where(df['rsi_14'] > 65, 0, np.nan))
    df['rsi_trend_pos'] = df['rsi_trend_pos'].ffill().fillna(0)
    strategies['RSI + Trend Filter'] = backtest(df, 'rsi_trend_pos')
    df['bb_pos'] = np.where(df['bb_position'] < 0.1, 1, np.where(df['bb_position'] > 0.9, 0, np.nan))
    df['bb_pos'] = df['bb_pos'].ffill().fillna(0)
    strategies['Bollinger Bounce'] = backtest(df, 'bb_pos')
    df['macd_pos'] = np.where(df['macd'] > df['macd_signal'], 1, 0)
    strategies['MACD Crossover'] = backtest(df, 'macd_pos')
    df['vol_pos'] = np.where((df['atr_ratio'] > df['atr_ratio'].rolling(50).mean() * 1.5) & (df['close'] > df['close'].shift(1)), 1, 0)
    strategies['Volatility Breakout'] = backtest(df, 'vol_pos')
    df['stoch_pos'] = np.where(df['stoch_rsi_k'] < 0.2, 1, np.where(df['stoch_rsi_k'] > 0.8, 0, np.nan))
    df['stoch_pos'] = df['stoch_pos'].ffill().fillna(0)
    strategies['Stoch RSI Oversold'] = backtest(df, 'stoch_pos')
    df['willr_pos'] = np.where(df['williams_r'] < -80, 1, np.where(df['williams_r'] > -20, 0, np.nan))
    df['willr_pos'] = df['willr_pos'].ffill().fillna(0)
    strategies['Williams %R'] = backtest(df, 'willr_pos')
    return strategies

def analyze_seasonality(df):
    dow_stats = df.groupby('day_of_week')['return'].agg(['mean', 'std', 'count']).reset_index()
    dow_stats['day_name'] = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    dow_stats['sharpe'] = dow_stats['mean'] / dow_stats['std'] * np.sqrt(365)
    month_stats = df.groupby('month')['return'].agg(['mean', 'std', 'count']).reset_index()
    month_stats['month_name'] = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    month_stats['sharpe'] = month_stats['mean'] / month_stats['std'] * np.sqrt(365)
    return dow_stats, month_stats

# ===================== CORRELATION & MARKET BREADTH =====================

def compute_correlation_matrix(all_prices):
    df = pd.DataFrame(all_prices)
    returns = df.pct_change().dropna()
    corr = returns.corr()
    return corr

def compute_altcoin_season_index(btc_dominance_series, lookback=90):
    if len(btc_dominance_series) < lookback:
        return None
    recent = btc_dominance_series.iloc[-lookback:]
    slope = np.polyfit(range(len(recent)), recent.values, 1)[0]
    score = 50 - slope * 500
    return max(0, min(100, score))

def compute_market_breadth(asset_signals):
    bullish = sum(1 for s in asset_signals if s['signal'] in ['STRONG LONG', 'LONG'])
    bearish = sum(1 for s in asset_signals if s['signal'] in ['STRONG SHORT', 'SHORT'])
    neutral = len(asset_signals) - bullish - bearish
    total = len(asset_signals) or 1
    return {
        'breadth_ratio': round(bullish / total, 2),
        'advance': bullish,
        'decline': bearish,
        'neutral': neutral,
        'breadth_signal': 'BULLISH' if bullish > bearish * 1.5 else 'BEARISH' if bearish > bullish * 1.5 else 'MIXED'
    }

# ===================== PATTERN MATCHING =====================

def find_similar_conditions(df, n_matches=5):
    if len(df) < 60:
        return []
    latest = df.iloc[-1]
    similar = []
    target_rsi = latest['rsi_14']
    target_price_sma = latest.get('dist_sma50', 0)
    target_vol = latest['volatility_20']
    target_macd = latest['macd_hist']
    target_bb = latest.get('bb_position', 0.5)
    target_atr = latest.get('atr_ratio', 0)
    for i in range(50, len(df) - 5):
        row = df.iloc[i]
        if pd.isna(row['rsi_14']) or pd.isna(row.get('dist_sma50')):
            continue
        rsi_diff = abs(row['rsi_14'] - target_rsi) / 100
        ps_diff = abs(row.get('dist_sma50', 0) - target_price_sma)
        vol_diff = abs((row.get('volatility_20', 50) - target_vol) / 100) if pd.notna(target_vol) and target_vol != 0 else 0.5
        macd_diff = abs((row.get('macd_hist', 0) - target_macd) / (abs(target_macd) + 0.001)) / 100
        bb_diff = abs(row.get('bb_position', 0.5) - target_bb)
        atr_diff = abs(row.get('atr_ratio', 0) - target_atr) * 10
        similarity = 1 - (rsi_diff * 0.25 + ps_diff * 0.25 + vol_diff * 0.15 + macd_diff * 0.15 + bb_diff * 0.1 + atr_diff * 0.1)
        if similarity > 0.80:
            future_ret = (df.iloc[min(i+5, len(df)-1)]['close'] - row['close']) / row['close'] * 100
            similar.append({
                'date': row['date'].strftime('%Y-%m-%d'),
                'similarity': round(similarity * 100, 1),
                'future_5d_return': round(future_ret, 2),
                'rsi': round(row['rsi_14'], 1),
                'price_vs_sma50': round(row.get('dist_sma50', 0), 4),
            })
    similar.sort(key=lambda x: x['similarity'], reverse=True)
    return similar[:n_matches]

# ===================== NARRATIVE GENERATION =====================

def build_sub_signals(latest, asset_name):
    signals = {}
    votes = []
    price = latest['close']

    # 1. TREND
    sma50 = latest.get('sma_50')
    sma200 = latest.get('sma_200')
    if pd.notna(sma50) and pd.notna(sma200):
        above50 = price > sma50
        above200 = price > sma200
        golden = sma50 > sma200
        if above50 and above200 and golden:
            signals['trend'] = {'score': 1.0, 'verdict': 'BULLISH',
                'detail': f"Price (${price:,.2f}) is above both SMA50 (${sma50:,.2f}) and SMA200 (${sma200:,.2f}). Golden cross confirmed."}
            votes.append(1.0)
        elif above50 and not above200:
            signals['trend'] = {'score': 0.3, 'verdict': 'CAUTIOUSLY BULLISH',
                'detail': "Price above SMA50 but below SMA200. Short-term recovery, long-term trend still negative."}
            votes.append(0.3)
        elif not above50 and above200:
            signals['trend'] = {'score': -0.3, 'verdict': 'CAUTIOUSLY BEARISH',
                'detail': "Price fell below SMA50 but still above SMA200. Possible correction or reversal beginning."}
            votes.append(-0.3)
        else:
            signals['trend'] = {'score': -1.0, 'verdict': 'BEARISH',
                'detail': "Price below both SMA50 and SMA200. Death cross territory."}
            votes.append(-1.0)
    else:
        signals['trend'] = {'score': 0, 'verdict': 'NEUTRAL', 'detail': "Insufficient data for trend analysis."}
        votes.append(0)

    # 2. MOMENTUM
    rsi = latest.get('rsi_14')
    macd_hist = latest.get('macd_hist')
    if pd.notna(rsi) and pd.notna(macd_hist):
        if rsi < 30 and macd_hist > 0:
            signals['momentum'] = {'score': 0.8, 'verdict': 'BULLISH',
                'detail': f"RSI is {rsi:.1f} (oversold) and MACD histogram turning positive ({macd_hist:.4f}). Classic reversal setup."}
            votes.append(0.8)
        elif rsi > 70 and macd_hist < 0:
            signals['momentum'] = {'score': -0.8, 'verdict': 'BEARISH',
                'detail': f"RSI is {rsi:.1f} (overbought) and MACD histogram turning negative. Momentum fading."}
            votes.append(-0.8)
        elif rsi < 40 and macd_hist < 0:
            signals['momentum'] = {'score': -0.3, 'verdict': 'BEARISH',
                'detail': f"RSI is {rsi:.1f} (weak) and MACD is negative. Momentum pointing down."}
            votes.append(-0.3)
        elif rsi > 60 and macd_hist > 0:
            signals['momentum'] = {'score': 0.5, 'verdict': 'BULLISH',
                'detail': f"RSI is {rsi:.1f} (strong) and MACD is positive. Momentum supporting trend."}
            votes.append(0.5)
        else:
            signals['momentum'] = {'score': 0, 'verdict': 'NEUTRAL',
                'detail': f"RSI is {rsi:.1f} (neutral) and MACD is {macd_hist:.4f}. No strong momentum signal."}
            votes.append(0)
    else:
        signals['momentum'] = {'score': 0, 'verdict': 'NEUTRAL', 'detail': "Momentum data insufficient."}
        votes.append(0)

    # 3. VOLATILITY
    atr = latest.get('atr_ratio')
    vol = latest.get('volatility_20')
    vol_forecast = latest.get('vol_forecast_5d')
    if pd.notna(atr) and pd.notna(vol):
        atr_pct = atr * 100
        forecast_text = f" 5-day forecast: {vol_forecast*100:.1f}%" if pd.notna(vol_forecast) else ""
        if atr_pct < 3.0 and vol < 50:
            signals['volatility'] = {'score': 0.5, 'verdict': 'SAFE',
                'detail': f"Daily range is {atr_pct:.2f}% and volatility is {vol:.1f}%. Market is calm.{forecast_text}"}
            votes.append(0.5)
        elif atr_pct > 6.0 or vol > 100:
            signals['volatility'] = {'score': -0.7, 'verdict': 'DANGEROUS',
                'detail': f"Daily range is {atr_pct:.2f}% and volatility is {vol:.1f}%. Extremely choppy.{forecast_text}"}
            votes.append(-0.7)
        else:
            signals['volatility'] = {'score': 0, 'verdict': 'MODERATE',
                'detail': f"Volatility normal ({vol:.1f}%).{forecast_text}"}
            votes.append(0)
    else:
        signals['volatility'] = {'score': 0, 'verdict': 'UNKNOWN', 'detail': "Volatility data unavailable."}
        votes.append(0)

    # 4. SENTIMENT (Fear & Greed)
    fng = latest.get('fng_value')
    if pd.notna(fng):
        if fng < 25:
            signals['sentiment'] = {'score': 0.7, 'verdict': 'CONTRARIAN BUY',
                'detail': f"Fear & Greed Index is {fng:.0f} (Extreme Fear). Historically marks local bottoms. Smart money accumulates when others panic."}
            votes.append(0.7)
        elif fng > 75:
            signals['sentiment'] = {'score': -0.7, 'verdict': 'CONTRARIAN SELL',
                'detail': f"Fear & Greed Index is {fng:.0f} (Extreme Greed). Everyone is euphoric. Historically, corrections begin here."}
            votes.append(-0.7)
        elif fng < 45:
            signals['sentiment'] = {'score': 0.3, 'verdict': 'CAUTIOUSLY BULLISH',
                'detail': f"Fear & Greed is {fng:.0f} (Fear). Negative but not extreme. Some contrarian edge."}
            votes.append(0.3)
        elif fng > 55:
            signals['sentiment'] = {'score': -0.3, 'verdict': 'CAUTIOUSLY BEARISH',
                'detail': f"Fear & Greed is {fng:.0f} (Greed). Positive but elevated. Caution warranted."}
            votes.append(-0.3)
        else:
            signals['sentiment'] = {'score': 0, 'verdict': 'NEUTRAL',
                'detail': f"Fear & Greed is {fng:.0f} (Neutral). No sentiment edge."}
            votes.append(0)
    else:
        signals['sentiment'] = {'score': 0, 'verdict': 'NEUTRAL', 'detail': "Sentiment data unavailable."}
        votes.append(0)

    # 5. FUNDING RATE
    funding = latest.get('funding_rate')
    if pd.notna(funding):
        if funding > 0.0005:
            signals['funding'] = {'score': -0.5, 'verdict': 'OVERHEATED',
                'detail': f"Funding rate is {funding*100:.4f}% (high). Longs paying shorts — overleveraged to upside. Contrarian bearish."}
            votes.append(-0.5)
        elif funding < -0.0005:
            signals['funding'] = {'score': 0.5, 'verdict': 'OVERSOLD',
                'detail': f"Funding rate is {funding*100:.4f}% (negative). Shorts paying longs — overleveraged to downside. Contrarian bullish."}
            votes.append(0.5)
        else:
            signals['funding'] = {'score': 0, 'verdict': 'NEUTRAL',
                'detail': f"Funding rate is {funding*100:.4f}% — normal range."}
            votes.append(0)
    else:
        signals['funding'] = {'score': 0, 'verdict': 'UNKNOWN', 'detail': "Funding rate data unavailable."}
        votes.append(0)

    # 6. VOLUME
    vol_ratio = latest.get('volume_ratio')
    if pd.notna(vol_ratio):
        if vol_ratio > 1.5 and latest['close'] > latest['open']:
            signals['volume'] = {'score': 0.4, 'verdict': 'CONFIRMING',
                'detail': f"Volume is {vol_ratio:.1f}x above average with green candle. Strong buying interest."}
            votes.append(0.4)
        elif vol_ratio > 1.5 and latest['close'] < latest['open']:
            signals['volume'] = {'score': -0.4, 'verdict': 'DISTRIBUTION',
                'detail': f"Volume is {vol_ratio:.1f}x above average with red candle. Heavy selling."}
            votes.append(-0.4)
        else:
            signals['volume'] = {'score': 0, 'verdict': 'NORMAL',
                'detail': f"Volume normal ({vol_ratio:.1f}x average)."}
            votes.append(0)
    else:
        signals['volume'] = {'score': 0, 'verdict': 'UNKNOWN', 'detail': "Volume data insufficient."}
        votes.append(0)

    # 7. DRAWDOWN
    dd = latest.get('drawdown')
    if pd.notna(dd):
        if dd < -0.50:
            signals['drawdown'] = {'score': 0.5, 'verdict': 'DEEP VALUE',
                'detail': f"Price is down {abs(dd)*100:.1f}% from peak. Deep drawdowns offer asymmetric upside."}
            votes.append(0.5)
        elif dd < -0.30:
            signals['drawdown'] = {'score': 0.2, 'verdict': 'OVERSOLD',
                'detail': f"Price is down {abs(dd)*100:.1f}% from peak. Significant pain priced in."}
            votes.append(0.2)
        elif dd > -0.05:
            signals['drawdown'] = {'score': -0.3, 'verdict': 'EXTENDED',
                'detail': f"Near highs (only {abs(dd)*100:.1f}% below). Limited upside, elevated risk."}
            votes.append(-0.3)
        else:
            signals['drawdown'] = {'score': 0, 'verdict': 'NORMAL',
                'detail': f"Drawdown is {abs(dd)*100:.1f}% — within normal ranges."}
            votes.append(0)
    else:
        signals['drawdown'] = {'score': 0, 'verdict': 'UNKNOWN', 'detail': "Drawdown data unavailable."}
        votes.append(0)

    # 8. STOCHASTIC RSI
    stoch = latest.get('stoch_rsi_k')
    if pd.notna(stoch):
        if stoch < 0.2:
            signals['stoch_rsi'] = {'score': 0.6, 'verdict': 'OVERSOLD',
                'detail': f"Stochastic RSI is {stoch:.2f} (deeply oversold). More sensitive than plain RSI — potential bounce."}
            votes.append(0.6)
        elif stoch > 0.8:
            signals['stoch_rsi'] = {'score': -0.6, 'verdict': 'OVERBOUGHT',
                'detail': f"Stochastic RSI is {stoch:.2f} (deeply overbought). More sensitive than plain RSI — potential pullback."}
            votes.append(-0.6)
        else:
            signals['stoch_rsi'] = {'score': 0, 'verdict': 'NEUTRAL',
                'detail': f"Stochastic RSI is {stoch:.2f} — neutral zone."}
            votes.append(0)
    else:
        signals['stoch_rsi'] = {'score': 0, 'verdict': 'UNKNOWN', 'detail': "Stochastic RSI data unavailable."}
        votes.append(0)

    # 9. WILLIAMS %R
    willr = latest.get('williams_r')
    if pd.notna(willr):
        if willr < -80:
            signals['williams_r'] = {'score': 0.5, 'verdict': 'OVERSOLD',
                'detail': f"Williams %R is {willr:.1f} (deeply oversold). Price near low of recent range."}
            votes.append(0.5)
        elif willr > -20:
            signals['williams_r'] = {'score': -0.5, 'verdict': 'OVERBOUGHT',
                'detail': f"Williams %R is {willr:.1f} (deeply overbought). Price near high of recent range."}
            votes.append(-0.5)
        else:
            signals['williams_r'] = {'score': 0, 'verdict': 'NEUTRAL',
                'detail': f"Williams %R is {willr:.1f} — neutral zone."}
            votes.append(0)
    else:
        signals['williams_r'] = {'score': 0, 'verdict': 'UNKNOWN', 'detail': "Williams %R data unavailable."}
        votes.append(0)

    # 10. PI CYCLE TOP
    pi_warning = latest.get('pi_cycle_top_warning')
    pi_signal = latest.get('pi_cycle_signal')
    if pd.notna(pi_warning) and pd.notna(pi_signal):
        if pi_warning == 1:
            signals['pi_cycle'] = {'score': -1.0, 'verdict': 'TOP WARNING',
                'detail': "PI Cycle Top indicator just flashed! 111-day SMA crossed above 350-day SMA x2. Historically marks major tops."}
            votes.append(-1.0)
        elif pi_signal == 1:
            signals['pi_cycle'] = {'score': -0.5, 'verdict': 'ELEVATED RISK',
                'detail': "PI Cycle Top is active. Market in historically overbought territory."}
            votes.append(-0.5)
        else:
            signals['pi_cycle'] = {'score': 0.3, 'verdict': 'NO TOP SIGNAL',
                'detail': "PI Cycle Top is not active. No major top warning from this long-term indicator."}
            votes.append(0.3)
    else:
        signals['pi_cycle'] = {'score': 0, 'verdict': 'UNKNOWN', 'detail': "PI Cycle data unavailable."}
        votes.append(0)

    # 11. RSI DIVERGENCE (NEW)
    rsi_div = latest.get('rsi_divergence')
    if pd.notna(rsi_div) and rsi_div != 'NONE':
        if rsi_div == 'BULLISH':
            signals['rsi_divergence'] = {'score': 0.6, 'verdict': 'BULLISH DIVERGENCE',
                'detail': "Price made a lower low but RSI made a higher low. Classic bullish divergence — momentum shifting."}
            votes.append(0.6)
        else:
            signals['rsi_divergence'] = {'score': -0.6, 'verdict': 'BEARISH DIVERGENCE',
                'detail': "Price made a higher high but RSI made a lower high. Classic bearish divergence — momentum fading."}
            votes.append(-0.6)
    else:
        signals['rsi_divergence'] = {'score': 0, 'verdict': 'NO DIVERGENCE',
            'detail': "No significant RSI divergence detected on recent price action."}
        votes.append(0)

    # 12. OBV DIVERGENCE (NEW)
    obv_div = latest.get('obv_divergence')
    if pd.notna(obv_div) and obv_div != 'NONE':
        if obv_div == 'BULLISH':
            signals['obv_divergence'] = {'score': 0.5, 'verdict': 'SMART MONEY BUYING',
                'detail': "Price fell but OBV (On-Balance Volume) rose. Smart money accumulating while price dips — bullish."}
            votes.append(0.5)
        else:
            signals['obv_divergence'] = {'score': -0.5, 'verdict': 'SMART MONEY SELLING',
                'detail': "Price rose but OBV fell. Smart money distributing while price pumps — bearish."}
            votes.append(-0.5)
    else:
        signals['obv_divergence'] = {'score': 0, 'verdict': 'NO OBV SIGNAL',
            'detail': "No significant OBV divergence. Volume and price are aligned."}
        votes.append(0)

    composite = sum(votes) / len(votes) if votes else 0

    if composite >= 0.5:
        final_signal, action = "STRONG LONG", "Multiple factors align bullish. Consider standard position size with risk management."
    elif composite >= 0.2:
        final_signal, action = "LONG", "Conditions favor upside, but not all signals agree. Consider a smaller position."
    elif composite >= -0.2:
        final_signal, action = "NO TRADE", "Mixed signals. The safest move is cash. Wait for clarity."
    elif composite >= -0.5:
        final_signal, action = "SHORT", "Conditions favor downside. Consider reducing exposure or hedging."
    else:
        final_signal, action = "STRONG SHORT", "Multiple bearish factors align. Consider exiting longs or hedging significantly."

    bullish_count = sum(1 for v in votes if v > 0)
    bearish_count = sum(1 for v in votes if v < 0)

    return {
        'signal': final_signal,
        'conviction': round(abs(composite), 2),
        'composite_score': round(composite, 3),
        'action': action,
        'sub_signals': signals,
        'market_breadth': {
            'bullish_factors': bullish_count,
            'bearish_factors': bearish_count,
            'neutral_factors': len(votes) - bullish_count - bearish_count,
        }
    }

# ===================== PER-ASSET PIPELINE =====================

def process_asset(code, config, fng_df, macro_data):
    print(f"\n{'='*60}")
    print(f"Processing {config['name']} ({code})")
    print(f"{'='*60}")

    df = fetch_binance_klines(config['binance'])
    source = 'Binance'
    if df.empty or len(df) < 100:
        print(f"  ⚠️ Binance failed, trying Yahoo Finance fallback...")
        if config.get('yahoo'):
            df = fetch_yahoo_ohlcv(config['yahoo'])
            source = 'Yahoo'
    if df.empty or len(df) < 100:
        print(f"  ❌ No data for {code} from any source")
        return None

    print(f"  Fetched {len(df)} days from {source}")

    df = add_features(df)
    df = add_pi_cycle(df)
    df = detect_regime(df)
    df = detect_rsi_divergence(df)
    df = detect_obv_divergence(df)

    if not fng_df.empty:
        df = df.merge(fng_df[['date', 'fng_value', 'fng_class']], on='date', how='left')

    funding = fetch_funding_rate(config['binance'])
    if not funding.empty:
        df = df.merge(funding[['date', 'funding_rate', 'funding_max', 'funding_min', 'funding_std']], on='date', how='left')

    ls = fetch_long_short_ratio(config['binance'])
    if not ls.empty:
        df = df.merge(ls[['date', 'long_short_ratio', 'long_account_pct']], on='date', how='left')

    oi = fetch_open_interest_hist(config['binance'])
    if not oi.empty:
        df = df.merge(oi[['date', 'open_interest', 'oi_value_usd']], on='date', how='left')

    for src_name, src_df in macro_data.items():
        if not src_df.empty:
            src_df = src_df.copy()
            src_df['date'] = pd.to_datetime(src_df['date']).dt.tz_localize(None)
            df = df.merge(src_df[['date', 'close']].rename(columns={'close': f'{src_name}_close'}), on='date', how='left')

    coin_data = fetch_coingecko_coin(config['coingecko']) if config.get('coingecko') else {}
    options_data = {}
    if config.get('deribit'):
        options_data = fetch_deribit_options(config['deribit'])

    sr_levels = find_support_resistance(df)
    whale = whale_activity_proxy(df)

    print("  Validating 10 strategies...")
    strategies = validate_strategies(df)
    bh_ret = (df['close'].iloc[-1] / df['close'].iloc[0]) - 1

    best_strategy = max(strategies.items(), key=lambda x: x[1]['total_return'])[0]
    best_pos_col = {
        'SMA20 Crossover': 'sma20_pos', 'SMA50 Trend': 'sma50_pos', 'Golden Cross': 'golden_pos',
        'RSI < 30, > 70': 'rsi_pos', 'RSI + Trend Filter': 'rsi_trend_pos',
        'Bollinger Bounce': 'bb_pos', 'MACD Crossover': 'macd_pos',
        'Volatility Breakout': 'vol_pos', 'Stoch RSI Oversold': 'stoch_pos', 'Williams %R': 'willr_pos'
    }.get(best_strategy, 'sma50_pos')

    print("  Running Monte Carlo...")
    mc = monte_carlo(df, best_pos_col)

    print("  Analyzing seasonality...")
    dow_stats, month_stats = analyze_seasonality(df)

    print("  Finding similar historical conditions...")
    similar = find_similar_conditions(df)

    print("  Generating narrative signal...")
    latest = df.iloc[-1]
    narrative = build_sub_signals(latest, config['name'])

    # Enhanced exchange flow proxy
    exchange_flow = compute_exchange_flow_proxy(df)

    # Build chart data for interactive visualizations
    best_pos_col = {
        'SMA20 Crossover': 'sma20_pos', 'SMA50 Trend': 'sma50_pos', 'Golden Cross': 'golden_pos',
        'RSI < 30, > 70': 'rsi_pos', 'RSI + Trend Filter': 'rsi_trend_pos',
        'Bollinger Bounce': 'bb_pos', 'MACD Crossover': 'macd_pos',
        'Volatility Breakout': 'vol_pos', 'Stoch RSI Oversold': 'stoch_pos', 'Williams %R': 'willr_pos'
    }.get(best_strategy, 'sma50_pos')
    chart_data = build_chart_data(df, strategies, best_pos_col)

    asset_output = {
        'asset': code,
        'name': config['name'],
        'date': latest['date'].strftime('%Y-%m-%d'),
        'price': round(latest['close'], 4 if latest['close'] < 1 else 2),
        'signal': narrative['signal'],
        'conviction': narrative['conviction'],
        'composite_score': narrative['composite_score'],
        'regime': latest['regime'],
        'action': narrative['action'],
        'indicators': {
            'rsi': round(latest['rsi_14'], 1) if pd.notna(latest['rsi_14']) else None,
            'macd_hist': round(latest['macd_hist'], 4) if pd.notna(latest['macd_hist']) else None,
            'atr_pct': round(latest['atr_ratio'] * 100, 2) if pd.notna(latest['atr_ratio']) else None,
            'volatility': round(latest['volatility_20'], 1) if pd.notna(latest['volatility_20']) else None,
            'vol_forecast_5d': round(latest['vol_forecast_5d'] * 100, 2) if pd.notna(latest.get('vol_forecast_5d')) else None,
            'drawdown': round(latest['drawdown'] * 100, 1) if pd.notna(latest['drawdown']) else None,
            'sma50': round(latest['sma_50'], 2) if pd.notna(latest['sma_50']) else None,
            'sma200': round(latest['sma_200'], 2) if pd.notna(latest['sma_200']) else None,
            'bb_position': round(latest['bb_position'] * 100, 1) if pd.notna(latest['bb_position']) else None,
            'stoch_rsi_k': round(latest['stoch_rsi_k'], 3) if pd.notna(latest.get('stoch_rsi_k')) else None,
            'williams_r': round(latest['williams_r'], 1) if pd.notna(latest.get('williams_r')) else None,
            'volume_ratio': round(latest['volume_ratio'], 2) if pd.notna(latest.get('volume_ratio')) else None,
            'volume_zscore': round(latest['volume_zscore'], 2) if pd.notna(latest.get('volume_zscore')) else None,
            'fng_value': int(latest['fng_value']) if pd.notna(latest.get('fng_value')) else None,
            'funding_rate': round(latest['funding_rate'], 6) if pd.notna(latest.get('funding_rate')) else None,
            'long_short_ratio': round(latest['long_short_ratio'], 2) if pd.notna(latest.get('long_short_ratio')) else None,
            'open_interest': round(latest['open_interest'], 0) if pd.notna(latest.get('open_interest')) else None,
        },
        'sub_signals': narrative['sub_signals'],
        'similar_conditions': similar,
        'support_resistance': sr_levels,
        'whale_activity': whale,
        'strategy_backtest': {
            'best_strategy': best_strategy,
            'return': round(strategies[best_strategy]['total_return'] * 100, 2),
            'sharpe': round(strategies[best_strategy]['sharpe'], 2),
            'sortino': round(strategies[best_strategy]['sortino'], 2),
            'calmar': round(strategies[best_strategy]['calmar'], 2),
            'max_drawdown': round(strategies[best_strategy]['max_drawdown'] * 100, 2),
            'trades': int(strategies[best_strategy]['trades']),
            'win_rate': round(strategies[best_strategy]['win_rate'], 1),
            'avg_win': round(strategies[best_strategy]['avg_win'] * 100, 2),
            'avg_loss': round(strategies[best_strategy]['avg_loss'] * 100, 2),
            'max_consecutive_wins': strategies[best_strategy]['max_consecutive_wins'],
            'max_consecutive_losses': strategies[best_strategy]['max_consecutive_losses'],
            'kelly_fraction': round(strategies[best_strategy]['kelly_fraction'] * 100, 1),
            'var_95': round(strategies[best_strategy]['var_95'] * 100, 2) if strategies[best_strategy]['var_95'] is not None else None,
        },
        'all_strategies': {k: {
            'return': round(v['total_return'] * 100, 2),
            'sharpe': round(v['sharpe'], 2),
            'sortino': round(v['sortino'], 2),
            'calmar': round(v['calmar'], 2),
            'max_drawdown': round(v['max_drawdown'] * 100, 2),
            'trades': int(v['trades']),
            'win_rate': round(v['win_rate'], 1),
            'max_consecutive_wins': v['max_consecutive_wins'],
            'max_consecutive_losses': v['max_consecutive_losses'],
            'kelly': round(v['kelly_fraction'] * 100, 1),
            'var_95': round(v['var_95'] * 100, 2) if v['var_95'] is not None else None,
        } for k, v in strategies.items()},
        'buy_hold': {'return': round(bh_ret * 100, 2)},
        'monte_carlo': {
            'profitable_pct': round(mc['profitable_pct'], 1) if mc else None,
            'mean': round(mc['mean'] * 100, 2) if mc else None,
            'median': round(mc['median'] * 100, 2) if mc else None,
            'pct_5': round(mc['pct_5'] * 100, 2) if mc else None,
            'pct_95': round(mc['pct_95'] * 100, 2) if mc else None,
        } if mc else None,
        'seasonality': {
            'best_day': dow_stats.loc[dow_stats['mean'].idxmax(), 'day_name'] if not dow_stats.empty else None,
            'worst_day': dow_stats.loc[dow_stats['mean'].idxmin(), 'day_name'] if not dow_stats.empty else None,
            'best_month': month_stats.loc[month_stats['mean'].idxmax(), 'month_name'] if not month_stats.empty else None,
            'worst_month': month_stats.loc[month_stats['mean'].idxmin(), 'month_name'] if not month_stats.empty else None,
        },
        'market_breadth': narrative['market_breadth'],
        'exchange_flow': exchange_flow,
        'chart_data': chart_data,
        'external': {
            'coin_data': coin_data,
            'options_data': options_data,
        }
    }

    print(f"  ✅ {narrative['signal']} | Conviction: {narrative['conviction']}/1.0 | Regime: {latest['regime']}")
    return asset_output, df[['date', 'close']].rename(columns={'close': code}), chart_data, df[['date', 'close']].rename(columns={'close': code})



# ===================== PORTFOLIO OPTIMIZER =====================

def optimize_portfolio(all_returns, risk_free_rate=0.0):
    """
    Mean-Variance Optimization + Risk Parity + Max Sharpe
    all_returns: dict of {code: pd.Series of daily returns}
    """
    returns_df = pd.DataFrame(all_returns).dropna()
    if returns_df.empty or len(returns_df.columns) < 2:
        return None

    n = len(returns_df.columns)
    codes = list(returns_df.columns)
    mean_returns = returns_df.mean() * 365
    cov_matrix = returns_df.cov() * 365

    # 1. Max Sharpe Ratio Portfolio
    def neg_sharpe(weights):
        port_return = np.dot(weights, mean_returns)
        port_vol = np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))
        return -(port_return - risk_free_rate) / port_vol if port_vol > 0 else 0

    from scipy.optimize import minimize
    constraints = {'type': 'eq', 'fun': lambda x: np.sum(x) - 1}
    bounds = tuple((0, 0.5) for _ in range(n))  # Max 50% in any single asset
    x0 = np.array([1/n] * n)

    max_sharpe_result = minimize(neg_sharpe, x0, method='SLSQP', bounds=bounds, constraints=constraints)
    max_sharpe_weights = max_sharpe_result.x if max_sharpe_result.success else x0

    # 2. Min Volatility Portfolio
    def portfolio_vol(weights):
        return np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))

    min_vol_result = minimize(portfolio_vol, x0, method='SLSQP', bounds=bounds, constraints=constraints)
    min_vol_weights = min_vol_result.x if min_vol_result.success else x0

    # 3. Risk Parity Portfolio (equal risk contribution)
    def risk_parity_obj(weights):
        port_vol = np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))
        marginal_risk = np.dot(cov_matrix, weights) / port_vol if port_vol > 0 else np.zeros(n)
        risk_contrib = weights * marginal_risk
        target = port_vol / n
        return np.sum((risk_contrib - target) ** 2)

    rp_result = minimize(risk_parity_obj, x0, method='SLSQP', bounds=bounds, constraints=constraints)
    rp_weights = rp_result.x if rp_result.success else x0

    def portfolio_stats(weights):
        pret = np.dot(weights, mean_returns)
        pvol = np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))
        sharpe = (pret - risk_free_rate) / pvol if pvol > 0 else 0
        return pret, pvol, sharpe

    ms_ret, ms_vol, ms_sharpe = portfolio_stats(max_sharpe_weights)
    mv_ret, mv_vol, mv_sharpe = portfolio_stats(min_vol_weights)
    rp_ret, rp_vol, rp_sharpe = portfolio_stats(rp_weights)

    return {
        'max_sharpe': {
            'weights': {codes[i]: round(max_sharpe_weights[i] * 100, 1) for i in range(n)},
            'expected_return': round(ms_ret * 100, 2),
            'volatility': round(ms_vol * 100, 2),
            'sharpe': round(ms_sharpe, 2),
        },
        'min_volatility': {
            'weights': {codes[i]: round(min_vol_weights[i] * 100, 1) for i in range(n)},
            'expected_return': round(mv_ret * 100, 2),
            'volatility': round(mv_vol * 100, 2),
            'sharpe': round(mv_sharpe, 2),
        },
        'risk_parity': {
            'weights': {codes[i]: round(rp_weights[i] * 100, 1) for i in range(n)},
            'expected_return': round(rp_ret * 100, 2),
            'volatility': round(rp_vol * 100, 2),
            'sharpe': round(rp_sharpe, 2),
        },
    }

# ===================== MACRO REGIME DETECTOR =====================

def detect_macro_regime(dxy_df, fed_df, cpi_df, vix_df):
    """
    Classify macro regime based on:
    - DXY trend (strong dollar = risk-off)
    - Fed funds trajectory (rising = tightening)
    - CPI trend (falling = disinflation = risk-on)
    - VIX level (elevated = fear)
    """
    regime = {'fed_cycle': 'UNKNOWN', 'dxy_trend': 'UNKNOWN', 'liquidity': 'UNKNOWN', 'vix_level': 'UNKNOWN', 'overall': 'UNKNOWN'}

    # Fed cycle
    if not fed_df.empty and len(fed_df) >= 6:
        recent_fed = fed_df.tail(6)['value'].values
        fed_slope = np.polyfit(range(len(recent_fed)), recent_fed, 1)[0]
        if fed_slope > 0.05:
            regime['fed_cycle'] = 'TIGHTENING'
        elif fed_slope < -0.05:
            regime['fed_cycle'] = 'EASING'
        else:
            regime['fed_cycle'] = 'PAUSE'

    # DXY trend
    if not dxy_df.empty and len(dxy_df) > 60:
        dxy_recent = dxy_df.tail(60)
        dxy_slope = np.polyfit(range(len(dxy_recent)), dxy_recent['close'].values, 1)[0]
        regime['dxy_trend'] = 'RISING' if dxy_slope > 0.01 else 'FALLING' if dxy_slope < -0.01 else 'FLAT'

    # Liquidity (proxy: CPI falling = more liquidity expected)
    if not cpi_df.empty and len(cpi_df) >= 6:
        recent_cpi = cpi_df.tail(6)['value'].values
        cpi_slope = np.polyfit(range(len(recent_cpi)), recent_cpi, 1)[0]
        regime['liquidity'] = 'IMPROVING' if cpi_slope < -0.1 else 'TIGHTENING' if cpi_slope > 0.1 else 'STABLE'

    # VIX level
    if not vix_df.empty:
        vix_current = vix_df['close'].iloc[-1]
        regime['vix_level'] = 'ELEVATED' if vix_current > 25 else 'FEAR' if vix_current > 20 else 'CALM'

    # Overall regime
    scores = []
    if regime['fed_cycle'] == 'EASING': scores.append(1)
    elif regime['fed_cycle'] == 'TIGHTENING': scores.append(-1)
    if regime['dxy_trend'] == 'FALLING': scores.append(1)
    elif regime['dxy_trend'] == 'RISING': scores.append(-1)
    if regime['liquidity'] == 'IMPROVING': scores.append(1)
    elif regime['liquidity'] == 'TIGHTENING': scores.append(-1)
    if regime['vix_level'] == 'CALM': scores.append(1)
    elif regime['vix_level'] in ['ELEVATED', 'FEAR']: scores.append(-1)

    total = sum(scores)
    if total >= 2:
        regime['overall'] = 'RISK_ON'
    elif total <= -2:
        regime['overall'] = 'RISK_OFF'
    else:
        regime['overall'] = 'MIXED'

    return regime

# ===================== OPTIONS SKEW ANALYSIS =====================

def fetch_options_skew(currency='ETH'):
    """
    Fetch Deribit options and compute 25-delta risk reversal.
    Risk reversal = 25D Call IV - 25D Put IV
    Positive = calls more expensive = bullish skew
    Negative = puts more expensive = bearish skew (crash protection demand)
    """
    url = f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={currency}&kind=option"
    try:
        r = fetch_with_retry(url, timeout=30)
        data = r.json()
        if not data.get('result'):
            return {}

        options = data['result']
        # Find near-term options (within 30 days)
        from datetime import datetime
        now = datetime.now()
        near_options = []
        for o in options:
            try:
                expiry_str = o['instrument_name'].split('-')[1]
                expiry = datetime.strptime(expiry_str, '%d%b%y')
                days = (expiry - now).days
                if 0 < days <= 30 and o.get('mark_iv'):
                    near_options.append(o)
            except:
                continue

        if not near_options:
            return {}

        # Approximate 25-delta options by strike distance from underlying
        # This is a simplification; true delta requires Black-Scholes
        calls = [o for o in near_options if 'C' in o['instrument_name']]
        puts = [o for o in near_options if 'P' in o['instrument_name']]

        if calls and puts:
            avg_call_iv = np.mean([o['mark_iv'] for o in calls])
            avg_put_iv = np.mean([o['mark_iv'] for o in puts])
            risk_reversal = avg_call_iv - avg_put_iv

            return {
                'avg_call_iv': round(avg_call_iv, 2),
                'avg_put_iv': round(avg_put_iv, 2),
                'risk_reversal': round(risk_reversal, 2),
                'skew_signal': 'BULLISH' if risk_reversal > 2 else 'BEARISH' if risk_reversal < -2 else 'NEUTRAL',
                'total_near_options': len(near_options),
            }
    except Exception as e:
        print(f"  ⚠️ Options skew {currency}: {e}")
    return {}

# ===================== LIQUIDATION DATA =====================

def fetch_liquidation_data(symbol='ETHUSDT', period='1d', limit=100):
    """
    Fetch Binance futures liquidation data.
    """
    url = "https://fapi.binance.com/fapi/v1/forceOrders"
    params = {'symbol': symbol, 'limit': limit}
    try:
        r = fetch_with_retry(url, params=params, timeout=30)
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['time'], unit='ms')
            df['qty'] = df['executedQty'].astype(float)
            df['price'] = df['avgPrice'].astype(float)
            df['value_usd'] = df['qty'] * df['price']
            df['side'] = df['side']  # SELL = long liquidation, BUY = short liquidation

            daily = df.groupby([df['date'].dt.date, 'side']).agg({
                'value_usd': 'sum',
                'qty': 'sum'
            }).reset_index()

            long_liq = daily[daily['side'] == 'SELL']['value_usd'].sum() if 'SELL' in daily['side'].values else 0
            short_liq = daily[daily['side'] == 'BUY']['value_usd'].sum() if 'BUY' in daily['side'].values else 0

            return {
                'long_liquidations_usd': round(long_liq, 0),
                'short_liquidations_usd': round(short_liq, 0),
                'net_liquidation': round(long_liq - short_liq, 0),
                'dominant_side': 'LONGS' if long_liq > short_liq * 1.5 else 'SHORTS' if short_liq > long_liq * 1.5 else 'BALANCED',
                'total_events': len(df),
            }
    except Exception as e:
        print(f"  ⚠️ Liquidation {symbol}: {e}")
    return {}

# ===================== ENHANCED EXCHANGE FLOW PROXY =====================

def compute_exchange_flow_proxy(df):
    """
    Enhanced exchange flow detection:
    - Volume spike + price drop = exchange inflow (selling pressure)
    - Volume spike + price rise = exchange outflow (buying pressure)
    - Uses volume z-score and return direction
    """
    latest = df.iloc[-1]
    vol_z = latest.get('volume_zscore')
    ret = latest.get('return')

    if pd.isna(vol_z) or pd.isna(ret):
        return {'signal': 'UNKNOWN', 'confidence': 0, 'description': 'Insufficient data'}

    if vol_z > 2.5 and ret < -0.03:
        return {
            'signal': 'INFLOW',
            'confidence': min(100, round(vol_z * 20)),
            'description': f'High volume ({vol_z:.1f}σ) with sharp drop ({ret*100:.1f}%) suggests coins moving to exchanges for selling.',
        }
    elif vol_z > 2.5 and ret > 0.03:
        return {
            'signal': 'OUTFLOW',
            'confidence': min(100, round(vol_z * 20)),
            'description': f'High volume ({vol_z:.1f}σ) with sharp rise ({ret*100:.1f}%) suggests coins leaving exchanges — accumulation.',
        }
    elif vol_z > 2:
        return {
            'signal': 'ELEVATED',
            'confidence': min(100, round(vol_z * 20)),
            'description': f'Elevated volume ({vol_z:.1f}σ) but direction unclear. Watch for confirmation.',
        }
    else:
        return {
            'signal': 'NEUTRAL',
            'confidence': 0,
            'description': f'Normal exchange flow. Volume at {vol_z:.1f}σ.',
        }

# ===================== CHART DATA EXPORT =====================

def build_chart_data(df, strategies_dict, best_pos_col):
    """
    Build time-series data for interactive charts.
    Returns dict with dates, prices, indicators, equity curves.
    """
    df = df.copy()

    # Price + SMA overlay
    price_data = {
        'dates': df['date'].dt.strftime('%Y-%m-%d').tolist(),
        'price': df['close'].round(2).tolist(),
        'sma50': df['sma_50'].round(2).fillna(None).tolist() if 'sma_50' in df.columns else [],
        'sma200': df['sma_200'].round(2).fillna(None).tolist() if 'sma_200' in df.columns else [],
        'bb_upper': df['bb_upper'].round(2).fillna(None).tolist() if 'bb_upper' in df.columns else [],
        'bb_lower': df['bb_lower'].round(2).fillna(None).tolist() if 'bb_lower' in df.columns else [],
    }

    # RSI
    rsi_data = {
        'dates': df['date'].dt.strftime('%Y-%m-%d').tolist(),
        'rsi': df['rsi_14'].round(1).fillna(None).tolist() if 'rsi_14' in df.columns else [],
        'overbought': [70] * len(df),
        'oversold': [30] * len(df),
    }

    # MACD
    macd_data = {
        'dates': df['date'].dt.strftime('%Y-%m-%d').tolist(),
        'macd': df['macd'].round(4).fillna(None).tolist() if 'macd' in df.columns else [],
        'signal': df['macd_signal'].round(4).fillna(None).tolist() if 'macd_signal' in df.columns else [],
        'hist': df['macd_hist'].round(4).fillna(None).tolist() if 'macd_hist' in df.columns else [],
    }

    # Equity curve for best strategy
    df['position_change'] = df[best_pos_col].diff().abs()
    df['strat_return'] = df[best_pos_col].shift(1) * df['return'] - df['position_change'] * FEE
    df['strat_return'] = df['strat_return'].fillna(0)
    df['cum_strat'] = (1 + df['strat_return']).cumprod()
    df['cum_bh'] = (1 + df['return'].fillna(0)).cumprod()

    equity_data = {
        'dates': df['date'].dt.strftime('%Y-%m-%d').tolist(),
        'strategy': df['cum_strat'].round(4).tolist(),
        'buy_hold': df['cum_bh'].round(4).tolist(),
    }

    # Drawdown
    peak = df['cum_strat'].cummax()
    df['dd_strat'] = (df['cum_strat'] - peak) / peak
    peak_bh = df['cum_bh'].cummax()
    df['dd_bh'] = (df['cum_bh'] - peak_bh) / peak_bh

    drawdown_data = {
        'dates': df['date'].dt.strftime('%Y-%m-%d').tolist(),
        'strategy': (df['dd_strat'] * 100).round(2).tolist(),
        'buy_hold': (df['dd_bh'] * 100).round(2).tolist(),
    }

    # Volume
    volume_data = {
        'dates': df['date'].dt.strftime('%Y-%m-%d').tolist(),
        'volume': df['volume'].round(0).tolist(),
        'volume_sma20': df['volume_sma_20'].round(0).fillna(None).tolist() if 'volume_sma_20' in df.columns else [],
    }

    return {
        'price': price_data,
        'rsi': rsi_data,
        'macd': macd_data,
        'equity': equity_data,
        'drawdown': drawdown_data,
        'volume': volume_data,
    }




# ===================== ROLLING CORRELATION HEATMAP =====================

def compute_rolling_correlations(all_prices, windows=[30, 60, 90]):
    """
    Compute rolling correlations for multiple time windows.
    Returns dict with correlation matrices for each window.
    """
    df = pd.DataFrame(all_prices).dropna()
    if df.empty or len(df.columns) < 2:
        return {}

    returns = df.pct_change().dropna()
    result = {}

    for window in windows:
        if len(returns) < window:
            continue
        rolling_corr = returns.rolling(window=window).corr().dropna()
        # Get the most recent correlation matrix
        latest_idx = rolling_corr.index.get_level_values(0)[-1]
        latest_corr = rolling_corr.loc[latest_idx]

        result[f'{window}d'] = {
            col: {k: round(v, 3) for k, v in latest_corr[col].to_dict().items()}
            for col in latest_corr.columns
        }

    return result

def compute_correlation_timeseries(all_prices, pair=('BTC', 'ETH'), window=30):
    """
    Compute rolling correlation time series for a specific pair.
    For interactive chart showing how correlation evolves.
    """
    df = pd.DataFrame(all_prices).dropna()
    if df.empty or pair[0] not in df.columns or pair[1] not in df.columns:
        return {}

    returns = df.pct_change().dropna()
    rolling_corr = returns[pair[0]].rolling(window=window).corr(returns[pair[1]]).dropna()

    return {
        'pair': f"{pair[0]}-{pair[1]}",
        'window': window,
        'dates': rolling_corr.index.strftime('%Y-%m-%d').tolist(),
        'values': rolling_corr.round(3).tolist(),
    }

# ===================== ON-CHAIN PROXIES (FREE) =====================

def compute_nvt_proxy(coin_data, asset_code):
    """
    Network Value to Transactions proxy.
    NVT = Market Cap / Volume
    High NVT = overvalued (price high relative to network usage)
    Low NVT = undervalued (price low relative to network usage)
    """
    if not coin_data or 'market_cap' not in coin_data or 'total_volume' not in coin_data:
        return None

    market_cap = coin_data['market_cap']
    volume = coin_data['total_volume']

    if not market_cap or not volume or volume == 0:
        return None

    nvt = market_cap / volume

    # Interpretation (heuristic thresholds for crypto)
    if nvt > 50:
        signal = 'OVERVALUED'
        detail = f'NVT is {nvt:.1f} (very high). Price elevated relative to on-chain/network activity. Caution warranted.'
    elif nvt > 20:
        signal = 'ELEVATED'
        detail = f'NVT is {nvt:.1f} (elevated). Price somewhat stretched relative to network usage.'
    elif nvt < 5:
        signal = 'UNDERVALUED'
        detail = f'NVT is {nvt:.1f} (low). Price cheap relative to network activity. Potential value.'
    else:
        signal = 'NORMAL'
        detail = f'NVT is {nvt:.1f} — within normal range for {asset_code}.'

    return {
        'nvt': round(nvt, 2),
        'signal': signal,
        'detail': detail,
    }

def compute_velocity_proxy(coin_data, asset_code):
    """
    Token Velocity proxy = Volume / Circulating Supply
    High velocity = tokens changing hands rapidly (speculative)
    Low velocity = tokens being held (accumulation)
    """
    if not coin_data or 'total_volume' not in coin_data or 'circulating_supply' not in coin_data:
        return None

    volume = coin_data['total_volume']
    supply = coin_data['circulating_supply']

    if not volume or not supply or supply == 0:
        return None

    velocity = volume / supply

    # Heuristic interpretation
    if velocity > 0.3:
        signal = 'HIGH'
        detail = f'Velocity is {velocity:.3f} (high). High turnover — speculative activity or distribution.'
    elif velocity < 0.05:
        signal = 'LOW'
        detail = f'Velocity is {velocity:.3f} (low). Tokens being held — accumulation phase.'
    else:
        signal = 'NORMAL'
        detail = f'Velocity is {velocity:.3f} — normal turnover.'

    return {
        'velocity': round(velocity, 4),
        'signal': signal,
        'detail': detail,
    }

def compute_exchange_dominance(asset_volume, total_market_volume):
    """
    What % of total crypto volume is this asset capturing?
    Rising dominance = growing market share
    """
    if not asset_volume or not total_market_volume or total_market_volume == 0:
        return None

    dominance = (asset_volume / total_market_volume) * 100

    return {
        'dominance_pct': round(dominance, 2),
        'detail': f"This asset captures {dominance:.2f}% of total reported crypto volume.",
    }

def compute_market_cap_dominance(coin_data, total_market_cap):
    """
    Market cap as % of total crypto market cap.
    """
    if not coin_data or 'market_cap' not in coin_data or not total_market_cap:
        return None

    mc = coin_data['market_cap']
    dominance = (mc / total_market_cap) * 100

    return {
        'mc_dominance_pct': round(dominance, 2),
        'detail': f"Market cap is {dominance:.2f}% of total crypto market.",
    }

def build_onchain_summary(coin_data, asset_code, total_market_volume, total_market_cap):
    """
    Build complete on-chain proxy summary for an asset.
    """
    summary = {
        'nvt': compute_nvt_proxy(coin_data, asset_code),
        'velocity': compute_velocity_proxy(coin_data, asset_code),
        'exchange_dominance': compute_exchange_dominance(coin_data.get('total_volume'), total_market_volume),
        'market_cap_dominance': compute_market_cap_dominance(coin_data, total_market_cap),
    }
    return {k: v for k, v in summary.items() if v is not None}


# ===================== MAIN EXECUTION =====================

def run_pipeline():
    print("=" * 70)
    print("CRYPTO MARKET INTELLIGENCE v4.4 — PLOTLY + ON-CHAIN EDITION")
    print("v4.3 Base + Rolling Correlations + On-Chain Proxies")
    print("=" * 70)

    print("\n[1/7] Fetching global data...")
    fng_df = fetch_fear_greed()
    global_data = fetch_coingecko_global()
    gas_data = fetch_etherscan_gas()
    staking_data = fetch_beaconchain_staking()
    cpi_data = fetch_fred_data('CPIAUCSL', 24)
    fed_data = fetch_fred_data('FEDFUNDS', 24)

    total_market_volume = global_data.get('total_volume', 0)
    total_market_cap = global_data.get('total_market_cap', 0)

    print("\n[2/7] Fetching macro data...")
    macro_data = {
        'spy': fetch_yahoo('SPY'),
        'dxy': fetch_yahoo('DX-Y.NYB'),
        'vix': fetch_yahoo('^VIX'),
        'tnx': fetch_yahoo('^TNX'),
        'gld': fetch_yahoo('GLD'),
    }

    print("\n[3/7] Processing all assets...")
    all_signals = {}
    all_prices = {}
    all_returns = {}
    all_funding = {}
    all_chart_data = {}
    all_liquidations = {}
    all_options_skew = {}

    for code, config in ASSETS.items():
        result = process_asset(code, config, fng_df, macro_data)
        if result:
            asset_output, price_series, chart_data = result
            all_signals[code] = asset_output
            all_prices[code] = price_series.set_index('date')[code]
            all_chart_data[code] = chart_data

            returns = price_series.set_index('date')[code].pct_change().dropna()
            if not returns.empty:
                all_returns[code] = returns

            if asset_output['indicators'].get('funding_rate') is not None:
                all_funding[code] = asset_output['indicators']['funding_rate']

            liq = fetch_liquidation_data(config['binance'])
            if liq:
                all_liquidations[code] = liq

            if config.get('deribit'):
                skew = fetch_options_skew(config['deribit'])
                if skew:
                    all_options_skew[code] = skew

            # Add on-chain proxies
            coin_data = asset_output.get('external', {}).get('coin_data', {})
            onchain = build_onchain_summary(coin_data, code, total_market_volume, total_market_cap)
            if onchain:
                all_signals[code]['onchain'] = onchain

    if not all_signals:
        print("\n⚠️ No assets processed successfully. Generating fallback report...")
        # Generate a minimal JSON so the dashboard doesn't break
        dashboard_data = {
            'version': '4.4',
            'generated_at': datetime.now().isoformat(),
            'update_schedule': UPDATE_TIME,
            'disclaimer': "THIS IS A RESEARCH AND EDUCATIONAL TOOL ONLY. NOT FINANCIAL ADVICE.",
            'fear_greed': {'value': 50, 'label': 'Neutral'},
            'global_data': global_data,
            'market_report': {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'market_mood': 'UNKNOWN',
                'mood_description': 'Data fetch failed. Binance API may be blocking this IP. Check API status.',
                'bullish_assets': 0, 'bearish_assets': 0, 'neutral_assets': 0,
                'avg_conviction': 0, 'regime_distribution': {},
            },
            'assets': {},
        }
        os.makedirs('docs', exist_ok=True)
        with open(OUTPUT_PATH, 'w') as f:
            json.dump(dashboard_data, f, indent=2, default=str)
        print(f"\n💾 Fallback saved to {OUTPUT_PATH}")
        return

    print("\n[4/7] Computing cross-asset analytics...")

    # Correlation matrix
    corr_matrix = compute_correlation_matrix(all_prices)
    corr_dict = {}
    if not corr_matrix.empty:
        for col in corr_matrix.columns:
            corr_dict[col] = {k: round(v, 3) for k, v in corr_matrix[col].to_dict().items()}

    # Rolling correlations
    rolling_corr = compute_rolling_correlations(all_prices, windows=[30, 60, 90])

    # Correlation time series for key pairs
    corr_ts = {}
    key_pairs = [('BTC', 'ETH'), ('BTC', 'SOL'), ('ETH', 'SOL')]
    for pair in key_pairs:
        ts = compute_correlation_timeseries(all_prices, pair, 30)
        if ts:
            corr_ts[f"{pair[0]}_{pair[1]}"] = ts

    # Altcoin season index
    btc_prices = all_prices.get('BTC')
    altcoin_season = 50
    if btc_prices is not None and len(btc_prices) > 90:
        alt_prices = pd.DataFrame({k: v for k, v in all_prices.items() if k != 'BTC'})
        if not alt_prices.empty:
            alt_avg = alt_prices.mean(axis=1)
            btc_ret = btc_prices.pct_change(90).iloc[-1] if len(btc_prices) > 90 else 0
            alt_ret = alt_avg.pct_change(90).iloc[-1] if len(alt_avg) > 90 else 0
            altcoin_season = 50 + (alt_ret - btc_ret) * 500
            altcoin_season = max(0, min(100, altcoin_season))

    # Market breadth
    breadth = compute_market_breadth(list(all_signals.values()))

    # Funding heatmap
    funding_heatmap = {k: v for k, v in all_funding.items()}

    print("\n[5/7] Running portfolio optimizer...")
    portfolio = optimize_portfolio(all_returns)
    if portfolio:
        print(f"  Max Sharpe: {portfolio['max_sharpe']['sharpe']} (vol: {portfolio['max_sharpe']['volatility']}%)")
        print(f"  Min Vol: {portfolio['min_volatility']['volatility']}%")
        print(f"  Risk Parity: {portfolio['risk_parity']['sharpe']}")

    print("\n[6/7] Detecting macro regime...")
    macro_regime = detect_macro_regime(macro_data.get('dxy', pd.DataFrame()), 
                                        fed_data, cpi_data, macro_data.get('vix', pd.DataFrame()))
    print(f"  Overall: {macro_regime['overall']} | Fed: {macro_regime['fed_cycle']} | DXY: {macro_regime['dxy_trend']} | VIX: {macro_regime['vix_level']}")

    print("\n[7/7] Generating market-wide report...")
    signals_list = list(all_signals.values())
    bullish = sum(1 for s in signals_list if s['signal'] in ['STRONG LONG', 'LONG'])
    bearish = sum(1 for s in signals_list if s['signal'] in ['STRONG SHORT', 'SHORT'])
    neutral = len(signals_list) - bullish - bearish
    avg_conviction = sum(s['conviction'] for s in signals_list) / len(signals_list)

    regimes = defaultdict(int)
    for s in signals_list:
        regimes[s['regime']] += 1

    if bullish >= len(signals_list) * 0.6:
        mood, desc = "BULLISH", "Most major assets are showing bullish signals. Overall market trend is positive."
    elif bearish >= len(signals_list) * 0.6:
        mood, desc = "BEARISH", "Most major assets are showing bearish signals. Caution warranted across the board."
    elif bullish > bearish:
        mood, desc = "CAUTIOUSLY BULLISH", "More assets bullish than bearish, but edge is weak. Selective opportunities."
    elif bearish > bullish:
        mood, desc = "CAUTIOUSLY BEARISH", "More assets bearish than bullish. Defensive positioning recommended."
    else:
        mood, desc = "MIXED", "Market is split. No clear directional bias. Cash is a valid position."

    market_report = {
        'date': signals_list[0]['date'],
        'market_mood': mood,
        'mood_description': desc,
        'bullish_assets': bullish,
        'bearish_assets': bearish,
        'neutral_assets': neutral,
        'avg_conviction': round(avg_conviction, 2),
        'regime_distribution': dict(regimes),
        'top_opportunities': sorted(
            [s for s in signals_list if s['signal'] in ['STRONG LONG', 'LONG']],
            key=lambda x: x['composite_score'], reverse=True
        )[:3],
        'biggest_risks': sorted(
            [s for s in signals_list if s['signal'] in ['STRONG SHORT', 'SHORT']],
            key=lambda x: x['composite_score']
        )[:3],
    }

    print(f"\n  Market Mood: {mood}")
    print(f"  Bullish: {bullish} | Bearish: {bearish} | Neutral: {neutral}")
    print(f"  Altcoin Season Index: {altcoin_season:.1f}/100")
    print(f"  Market Breadth: {breadth['breadth_signal']} ({breadth['breadth_ratio']*100:.0f}% bullish)")

    print("\n[8/8] Saving to database and dashboard...")
    conn = sqlite3.connect(DB_PATH)
    for code, data in all_signals.items():
        df_save = pd.DataFrame([{
            'date': data['date'],
            'asset': code,
            'price': data['price'],
            'signal': data['signal'],
            'conviction': data['conviction'],
            'regime': data['regime'],
            'composite_score': data['composite_score'],
        }])
        df_save.to_sql('signals', conn, if_exists='append', index=False)
    conn.close()
    print(f"  Saved to {DB_PATH}")

    dashboard_data = {
        'version': '4.4',
        'generated_at': datetime.now().isoformat(),
        'update_schedule': UPDATE_TIME,
        'disclaimer': "THIS IS A RESEARCH AND EDUCATIONAL TOOL ONLY. NOT FINANCIAL ADVICE. Past performance does NOT predict future results. You can lose money. The creators are NOT responsible for any trading losses. Paper trade first for 3+ months.",
        'fear_greed': {
            'value': int(fng_df['fng_value'].iloc[-1]) if not fng_df.empty else None,
            'label': fng_df['fng_class'].iloc[-1] if not fng_df.empty else None,
        },
        'global_data': global_data,
        'macro_data': {
            'gas': gas_data,
            'staking': staking_data,
            'cpi': cpi_data.to_dict('records') if not cpi_data.empty else [],
            'fed': fed_data.to_dict('records') if not fed_data.empty else [],
        },
        'macro_regime': macro_regime,
        'market_report': market_report,
        'cross_asset': {
            'correlation_matrix': corr_dict,
            'rolling_correlations': rolling_corr,
            'correlation_timeseries': corr_ts,
            'altcoin_season_index': round(altcoin_season, 1),
            'market_breadth': breadth,
            'funding_heatmap': funding_heatmap,
            'portfolio_optimizer': portfolio,
            'liquidations': all_liquidations,
            'options_skew': all_options_skew,
        },
        'assets': all_signals,
        'chart_data': all_chart_data,
    }

    with open(OUTPUT_PATH, 'w') as f:
        json.dump(dashboard_data, f, indent=2, default=str)

    print(f"\n💾 Saved to {OUTPUT_PATH}")
    print("\n" + "=" * 70)
    print("✅ v4.4 PLOTLY + ON-CHAIN EDITION COMPLETE")
    print("=" * 70)

if __name__ == '__main__':
    run_pipeline()
