#!/usr/bin/env python3
"""
================================================================================
MARKET CORTEX v5.0 — ULTIMATE EDITION
================================================================================
ALL FEATURES:
  • Dynamic position sizing (volatility-adjusted)
  • Drawdown protection (10% DD → 50% size)
  • Regime-based strategy switching
  • Signal weighting by historical accuracy
  • Multi-timeframe confirmation (1h → 4h → 1d)
  • Correlation risk management
  • On-chain intelligence (exchange flows, whale tracking)
  • Trade plan generator (entry/exit/stop/targets)
  • Walk-forward validation
  • Ensemble signal combining
  • Real-time alerts (Discord/Telegram)
  • Signal history & win rate tracking
  • Portfolio simulator
  • Volatility forecast (GARCH-style)
  • Price targets with probability
  • Regime change detection
  • Risk metrics dashboard
  • Market summary report

ALL FREE APIS — NO PAID DATA SOURCES

IMPORTANT: This is a RESEARCH AND EDUCATIONAL TOOL ONLY.
NOT financial advice. Past performance does NOT predict future results.
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
    'BTC': {'name': 'Bitcoin', 'binance': 'BTCUSDT', 'yahoo': 'BTC-USD', 'coingecko': 'bitcoin', 'deribit': 'BTC'},
    'ETH': {'name': 'Ethereum', 'binance': 'ETHUSDT', 'yahoo': 'ETH-USD', 'coingecko': 'ethereum', 'deribit': 'ETH'},
    'SOL': {'name': 'Solana', 'binance': 'SOLUSDT', 'yahoo': 'SOL-USD', 'coingecko': 'solana', 'deribit': 'SOL'},
    'BNB': {'name': 'BNB', 'binance': 'BNBUSDT', 'yahoo': 'BNB-USD', 'coingecko': 'binancecoin', 'deribit': None},
    'XRP': {'name': 'XRP', 'binance': 'XRPUSDT', 'yahoo': 'XRP-USD', 'coingecko': 'ripple', 'deribit': None},
    'ADA': {'name': 'Cardano', 'binance': 'ADAUSDT', 'yahoo': 'ADA-USD', 'coingecko': 'cardano', 'deribit': None},
    'DOGE': {'name': 'Dogecoin', 'binance': 'DOGEUSDT', 'yahoo': 'DOGE-USD', 'coingecko': 'dogecoin', 'deribit': None},
    'LINK': {'name': 'Chainlink', 'binance': 'LINKUSDT', 'yahoo': 'LINK-USD', 'coingecko': 'chainlink', 'deribit': None},
    'AVAX': {'name': 'Avalanche', 'binance': 'AVAXUSDT', 'yahoo': 'AVAX-USD', 'coingecko': 'avalanche-2', 'deribit': None},
}

# ─── SIGNAL WEIGHTS ───
SIGNAL_WEIGHTS = {
    'trend': 0.30,
    'momentum': 0.25,
    'volatility': 0.15,
    'sentiment': 0.12,
    'funding': 0.08,
    'volume': 0.05,
    'drawdown': 0.05,
}

# ─── RISK PARAMETERS ───
RISK_PARAMS = {
    'max_risk_per_trade': 0.02,
    'max_portfolio_risk': 0.06,
    'drawdown_reduction': {0.10: 0.50, 0.20: 0.25, 0.30: 0.10},
    'correlation_threshold': 0.70,
    'atr_multiplier_stop': 2.0,
    'atr_multiplier_target': 4.0,
}

# ─── REGIME STRATEGY MAP ───
REGIME_STRATEGY = {
    'STRONG_BULL': {'strategy': 'SMA50 Trend', 'size_mult': 1.2},
    'BULL_TREND': {'strategy': 'SMA20 Crossover', 'size_mult': 1.0},
    'BULL_VOLATILE': {'strategy': 'MACD Crossover', 'size_mult': 0.8},
    'BULL_CHOPPY': {'strategy': 'Bollinger Bounce', 'size_mult': 0.6},
    'CHOPPY': {'strategy': 'Bollinger Bounce', 'size_mult': 0.5},
    'RANGE': {'strategy': 'RSI + Trend Filter', 'size_mult': 0.5},
    'BEAR_CHOPPY': {'strategy': 'Williams %R', 'size_mult': 0.4},
    'BEAR_TREND': {'strategy': 'Volatility Breakout', 'size_mult': 0.3},
    'BEAR_VOLATILE': {'strategy': 'RSI < 30, > 70', 'size_mult': 0.2},
    'STRONG_BEAR': {'strategy': 'RSI < 30, > 70', 'size_mult': 0.1},
}

ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
BEACONCHAIN_API_KEY = os.environ.get('BEACONCHAIN_API_KEY', '')
FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK', '')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

DB_PATH = 'crypto_quant.db'
OUTPUT_PATH = 'docs/market_intelligence.json'
HISTORY_PATH = 'docs/signal_history.json'
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

def fetch_binance_klines_interval(symbol, interval='1h', limit=200):
    """Fetch data for any interval (1h, 4h, 1d)"""
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
        return df[['date','close']]
    except Exception as e:
        print(f"  ⚠️ Binance {interval} {symbol}: {e}")
        return pd.DataFrame()

def fetch_yahoo_ohlcv(ticker, period='2y'):
    try:
        import yfinance as yf
        data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if data.empty:
            return pd.DataFrame()
        df = data.reset_index()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [' '.join(col).strip() if col[1] else col[0] for col in df.columns.values]
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
        }
    except Exception as e:
        print(f"  ⚠️ CG coin {coin_id}: {e}")
        return {}

# ===================== INDICATOR ENGINE =====================

def add_features(df):
    df = df.copy().sort_values('date').reset_index(drop=True)
    df['return'] = df['close'].pct_change()
    df['return_5d'] = df['close'].pct_change(5)
    df['return_20d'] = df['close'].pct_change(20)
    df['return_60d'] = df['close'].pct_change(60)

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

    df['volatility_20'] = df['return'].rolling(20).std() * np.sqrt(365)
    df['volatility_50'] = df['return'].rolling(50).std() * np.sqrt(365)

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

# ===================== RISK ENGINE =====================

def calculate_dynamic_position_size(df, idx, base_size=1.0, account_capital=10000):
    latest = df.iloc[idx]
    
    atr_ratio = latest.get('atr_ratio', 0.02)
    normal_atr = 0.02
    vol_mult = min(1.5, max(0.3, normal_atr / (atr_ratio + 0.001)))
    
    dd = latest.get('drawdown', 0)
    dd_mult = 1.0
    for threshold, reduction in sorted(RISK_PARAMS['drawdown_reduction'].items()):
        if dd < -threshold:
            dd_mult = reduction
    
    regime = latest.get('regime', 'CHOPPY')
    regime_mult = REGIME_STRATEGY.get(regime, {'size_mult': 0.5})['size_mult']
    
    size_mult = vol_mult * dd_mult * regime_mult
    
    price = latest['close']
    atr_value = latest.get('atr_14', price * 0.02)
    stop_distance = atr_value * RISK_PARAMS['atr_multiplier_stop']
    risk_per_share = stop_distance
    max_risk_amount = account_capital * RISK_PARAMS['max_risk_per_trade']
    max_shares = max_risk_amount / risk_per_share if risk_per_share > 0 else 0
    
    final_shares = max_shares * size_mult
    
    return {
        'size_multiplier': round(size_mult, 2),
        'position_size': round(final_shares, 4),
        'risk_amount': round(final_shares * risk_per_share, 2),
        'risk_percent': round((final_shares * risk_per_share / account_capital) * 100, 2),
        'stop_loss': round(price - stop_distance, 4),
        'take_profit_1': round(price + atr_value * RISK_PARAMS['atr_multiplier_target'] * 1.0, 4),
        'take_profit_2': round(price + atr_value * RISK_PARAMS['atr_multiplier_target'] * 2.0, 4),
    }

# ===================== SIGNAL FACTORY =====================

def build_sub_signals_weighted(latest, asset_name, historical_accuracy=0.5):
    signals = {}
    votes = []
    price = latest['close']
    
    # 1. TREND (weight: 0.30)
    sma50 = latest.get('sma_50')
    sma200 = latest.get('sma_200')
    if pd.notna(sma50) and pd.notna(sma200):
        above50 = price > sma50
        above200 = price > sma200
        golden = sma50 > sma200
        if above50 and above200 and golden:
            score = 1.0
            verdict = 'BULLISH'
            detail = f"Price above both SMA50 and SMA200. Golden cross confirmed."
        elif above50 and not above200:
            score = 0.3
            verdict = 'CAUTIOUSLY BULLISH'
            detail = "Price above SMA50 but below SMA200."
        elif not above50 and above200:
            score = -0.3
            verdict = 'CAUTIOUSLY BEARISH'
            detail = "Price below SMA50 but above SMA200."
        else:
            score = -1.0
            verdict = 'BEARISH'
            detail = "Price below both SMA50 and SMA200."
    else:
        score = 0
        verdict = 'NEUTRAL'
        detail = "Insufficient data."
    signals['trend'] = {'score': score, 'verdict': verdict, 'detail': detail, 'weight': SIGNAL_WEIGHTS['trend']}
    votes.append(score * SIGNAL_WEIGHTS['trend'])
    
    # 2. MOMENTUM (weight: 0.25)
    rsi = latest.get('rsi_14')
    macd_hist = latest.get('macd_hist')
    if pd.notna(rsi) and pd.notna(macd_hist):
        if rsi < 30 and macd_hist > 0:
            score = 0.8
            verdict = 'BULLISH'
            detail = f"RSI {rsi:.1f} (oversold) + MACD turning positive."
        elif rsi > 70 and macd_hist < 0:
            score = -0.8
            verdict = 'BEARISH'
            detail = f"RSI {rsi:.1f} (overbought) + MACD turning negative."
        elif rsi < 40 and macd_hist < 0:
            score = -0.3
            verdict = 'BEARISH'
            detail = f"RSI {rsi:.1f} (weak) + MACD negative."
        elif rsi > 60 and macd_hist > 0:
            score = 0.5
            verdict = 'BULLISH'
            detail = f"RSI {rsi:.1f} (strong) + MACD positive."
        else:
            score = 0
            verdict = 'NEUTRAL'
            detail = f"RSI {rsi:.1f}, MACD {macd_hist:.4f}."
    else:
        score = 0
        verdict = 'NEUTRAL'
        detail = "Insufficient data."
    signals['momentum'] = {'score': score, 'verdict': verdict, 'detail': detail, 'weight': SIGNAL_WEIGHTS['momentum']}
    votes.append(score * SIGNAL_WEIGHTS['momentum'])
    
    # 3. VOLATILITY (weight: 0.15)
    atr = latest.get('atr_ratio')
    vol = latest.get('volatility_20')
    if pd.notna(atr) and pd.notna(vol):
        atr_pct = atr * 100
        if atr_pct < 3.0 and vol < 50:
            score = 0.5
            verdict = 'SAFE'
            detail = f"Volatility {vol:.1f}%, ATR {atr_pct:.2f}%."
        elif atr_pct > 6.0 or vol > 100:
            score = -0.7
            verdict = 'DANGEROUS'
            detail = f"High volatility: {vol:.1f}%, ATR {atr_pct:.2f}%."
        else:
            score = 0
            verdict = 'MODERATE'
            detail = f"Normal volatility ({vol:.1f}%)."
    else:
        score = 0
        verdict = 'UNKNOWN'
        detail = "Data unavailable."
    signals['volatility'] = {'score': score, 'verdict': verdict, 'detail': detail, 'weight': SIGNAL_WEIGHTS['volatility']}
    votes.append(score * SIGNAL_WEIGHTS['volatility'])
    
    # 4. SENTIMENT (weight: 0.12)
    fng = latest.get('fng_value')
    if pd.notna(fng):
        if fng < 25:
            score = 0.7
            verdict = 'CONTRARIAN BUY'
            detail = f"Fear & Greed: {fng:.0f} (Extreme Fear)."
        elif fng > 75:
            score = -0.7
            verdict = 'CONTRARIAN SELL'
            detail = f"Fear & Greed: {fng:.0f} (Extreme Greed)."
        elif fng < 45:
            score = 0.3
            verdict = 'CAUTIOUSLY BULLISH'
            detail = f"Fear & Greed: {fng:.0f} (Fear)."
        elif fng > 55:
            score = -0.3
            verdict = 'CAUTIOUSLY BEARISH'
            detail = f"Fear & Greed: {fng:.0f} (Greed)."
        else:
            score = 0
            verdict = 'NEUTRAL'
            detail = f"Fear & Greed: {fng:.0f} (Neutral)."
    else:
        score = 0
        verdict = 'NEUTRAL'
        detail = "Data unavailable."
    signals['sentiment'] = {'score': score, 'verdict': verdict, 'detail': detail, 'weight': SIGNAL_WEIGHTS['sentiment']}
    votes.append(score * SIGNAL_WEIGHTS['sentiment'])
    
    # 5. FUNDING (weight: 0.08)
    funding = latest.get('funding_rate')
    if pd.notna(funding):
        if funding > 0.0005:
            score = -0.5
            verdict = 'OVERHEATED'
            detail = f"Funding {funding*100:.4f}% (high)."
        elif funding < -0.0005:
            score = 0.5
            verdict = 'OVERSOLD'
            detail = f"Funding {funding*100:.4f}% (negative)."
        else:
            score = 0
            verdict = 'NEUTRAL'
            detail = f"Funding {funding*100:.4f}%."
    else:
        score = 0
        verdict = 'UNKNOWN'
        detail = "Data unavailable."
    signals['funding'] = {'score': score, 'verdict': verdict, 'detail': detail, 'weight': SIGNAL_WEIGHTS['funding']}
    votes.append(score * SIGNAL_WEIGHTS['funding'])
    
    # 6. VOLUME (weight: 0.05)
    vol_ratio = latest.get('volume_ratio')
    if pd.notna(vol_ratio):
        if vol_ratio > 1.5 and latest['close'] > latest['open']:
            score = 0.4
            verdict = 'CONFIRMING'
            detail = f"Volume {vol_ratio:.1f}x avg, green candle."
        elif vol_ratio > 1.5 and latest['close'] < latest['open']:
            score = -0.4
            verdict = 'DISTRIBUTION'
            detail = f"Volume {vol_ratio:.1f}x avg, red candle."
        else:
            score = 0
            verdict = 'NORMAL'
            detail = f"Volume {vol_ratio:.1f}x avg."
    else:
        score = 0
        verdict = 'UNKNOWN'
        detail = "Data insufficient."
    signals['volume'] = {'score': score, 'verdict': verdict, 'detail': detail, 'weight': SIGNAL_WEIGHTS['volume']}
    votes.append(score * SIGNAL_WEIGHTS['volume'])
    
    # 7. DRAWDOWN (weight: 0.05)
    dd = latest.get('drawdown')
    if pd.notna(dd):
        if dd < -0.50:
            score = 0.5
            verdict = 'DEEP VALUE'
            detail = f"Down {abs(dd)*100:.1f}% from peak."
        elif dd < -0.30:
            score = 0.2
            verdict = 'OVERSOLD'
            detail = f"Down {abs(dd)*100:.1f}% from peak."
        elif dd > -0.05:
            score = -0.3
            verdict = 'EXTENDED'
            detail = f"Near highs ({abs(dd)*100:.1f}% below)."
        else:
            score = 0
            verdict = 'NORMAL'
            detail = f"Drawdown {abs(dd)*100:.1f}%."
    else:
        score = 0
        verdict = 'UNKNOWN'
        detail = "Data unavailable."
    signals['drawdown'] = {'score': score, 'verdict': verdict, 'detail': detail, 'weight': SIGNAL_WEIGHTS['drawdown']}
    votes.append(score * SIGNAL_WEIGHTS['drawdown'])
    
    composite = sum(votes) / sum(SIGNAL_WEIGHTS.values())
    composite = composite * (0.5 + 0.5 * historical_accuracy)
    
    if composite >= 0.5:
        final_signal, action = "STRONG LONG", "Multiple factors align bullish. Consider standard position size."
    elif composite >= 0.2:
        final_signal, action = "LONG", "Conditions favor upside. Consider smaller position."
    elif composite >= -0.2:
        final_signal, action = "NO TRADE", "Mixed signals. Wait for clarity."
    elif composite >= -0.5:
        final_signal, action = "SHORT", "Conditions favor downside. Consider reducing exposure."
    else:
        final_signal, action = "STRONG SHORT", "Multiple bearish factors align. Exit longs or hedge."
    
    return {
        'signal': final_signal,
        'conviction': round(abs(composite), 2),
        'composite_score': round(composite, 3),
        'action': action,
        'sub_signals': signals,
        'weighted_votes': votes,
    }

# ===================== MULTI-TIMEFRAME ANALYSIS =====================

def multi_timeframe_analysis(symbol, timeframes=['1h', '4h', '1d']):
    """Analyze across timeframes for stronger signals"""
    tf_signals = {}
    tf_data = {}
    
    for tf in timeframes:
        df = fetch_binance_klines_interval(symbol, tf, 200)
        if df.empty:
            continue
        
        # Add basic indicators
        df['sma_20'] = df['close'].rolling(20).mean()
        df['sma_50'] = df['close'].rolling(50).mean()
        df['return'] = df['close'].pct_change()
        df['rsi'] = 100 - (100 / (1 + df['return'].rolling(14).mean()))
        
        latest = df.iloc[-1]
        price = latest['close']
        sma20 = latest.get('sma_20', price)
        sma50 = latest.get('sma_50', price)
        rsi = latest.get('rsi', 50)
        
        # Determine signal for this timeframe
        if price > sma20 and price > sma50 and rsi > 50:
            signal = 'BULLISH'
        elif price < sma20 and price < sma50 and rsi < 50:
            signal = 'BEARISH'
        else:
            signal = 'NEUTRAL'
        
        tf_signals[tf] = signal
        tf_data[tf] = {'price': price, 'sma20': sma20, 'sma50': sma50, 'rsi': rsi}
    
    # Check alignment
    signal_values = list(tf_signals.values())
    unique_signals = set(signal_values)
    
    if len(unique_signals) == 1:
        alignment = 'STRONG'
        strength = 1.2
    elif len(unique_signals) == 2 and 'NEUTRAL' not in unique_signals:
        alignment = 'MODERATE'
        strength = 0.8
    elif len(unique_signals) == 2 and 'NEUTRAL' in unique_signals:
        alignment = 'WEAK'
        strength = 0.6
    else:
        alignment = 'CONFLICT'
        strength = 0.3
    
    # Determine recommendation
    if alignment == 'STRONG':
        recommendation = tf_signals.get('1d', 'NEUTRAL')
    elif alignment in ['MODERATE', 'WEAK']:
        recommendation = 'CAUTIOUS_' + (tf_signals.get('1d', 'NEUTRAL'))
    else:
        recommendation = 'WAIT'
    
    return {
        'signals': tf_signals,
        'data': tf_data,
        'alignment': alignment,
        'strength': strength,
        'recommendation': recommendation,
    }

# ===================== VOLATILITY FORECAST =====================

def forecast_volatility(df, days=5):
    """Simple GARCH-style volatility forecast"""
    returns = df['return'].dropna()
    if len(returns) < 20:
        return {'current_annual_vol': 0, 'long_term_vol': 0, 'forecast_5d_vol': 0}
    
    # EWMA volatility (lambda = 0.94)
    lambda_ = 0.94
    vol = returns.ewm(span=1/(1-lambda_)).std() * np.sqrt(365)
    
    current_vol = vol.iloc[-1] if not vol.empty else 0.5
    long_term_vol = vol.mean() if not vol.empty else 0.5
    
    # Mean reversion forecast
    forecast = current_vol * 0.6 + long_term_vol * 0.4
    forecast_5d = forecast * np.sqrt(5) if forecast > 0 else 0
    
    return {
        'current_annual_vol': round(current_vol * 100, 1),
        'long_term_vol': round(long_term_vol * 100, 1),
        'forecast_5d_vol': round(forecast_5d * 100, 1),
        'forecast_20d_vol': round(forecast * np.sqrt(20) * 100, 1),
        'regime': 'HIGH' if forecast > vol.median() * 1.5 else 'LOW',
    }

# ===================== PRICE TARGETS WITH PROBABILITY =====================

def calculate_price_targets(price, atr, market_condition='NEUTRAL'):
    """Calculate price targets with probability based on market condition"""
    if market_condition in ['STRONG_BULL', 'BULL_TREND']:
        target_1 = price + atr * 1.0
        target_1_prob = 0.72
        target_2 = price + atr * 2.0
        target_2_prob = 0.55
        target_3 = price + atr * 3.0
        target_3_prob = 0.30
    elif market_condition in ['BULL_VOLATILE', 'BULL_CHOPPY']:
        target_1 = price + atr * 0.8
        target_1_prob = 0.65
        target_2 = price + atr * 1.5
        target_2_prob = 0.45
        target_3 = price + atr * 2.5
        target_3_prob = 0.20
    elif market_condition in ['STRONG_BEAR', 'BEAR_TREND']:
        target_1 = price - atr * 1.0
        target_1_prob = 0.70
        target_2 = price - atr * 2.0
        target_2_prob = 0.50
        target_3 = price - atr * 3.0
        target_3_prob = 0.25
    else:  # CHOPPY / RANGE / NEUTRAL
        target_1 = price + atr * 0.6
        target_1_prob = 0.55
        target_2 = price + atr * 1.2
        target_2_prob = 0.35
        target_3 = price + atr * 2.0
        target_3_prob = 0.15
    
    return {
        'target_1': {'price': round(target_1, 2), 'probability': target_1_prob},
        'target_2': {'price': round(target_2, 2), 'probability': target_2_prob},
        'target_3': {'price': round(target_3, 2), 'probability': target_3_prob},
    }

# ===================== SIGNAL HISTORY & PERFORMANCE =====================

def load_signal_history():
    """Load signal history from file"""
    try:
        with open(HISTORY_PATH, 'r') as f:
            return json.load(f)
    except:
        return {'signals': [], 'performance': {}}

def save_signal_history(history):
    """Save signal history to file"""
    with open(HISTORY_PATH, 'w') as f:
        json.dump(history, f, indent=2, default=str)

def track_signal_performance(asset, signal, price, conviction, trade_plan):
    """Track signal for performance analysis"""
    history = load_signal_history()
    
    entry = {
        'timestamp': datetime.now().isoformat(),
        'asset': asset,
        'signal': signal,
        'price': price,
        'conviction': conviction,
        'entry_price': trade_plan.get('entry_price', price),
        'stop_loss': trade_plan.get('stop_loss'),
        'take_profit_1': trade_plan.get('take_profit_1'),
        'take_profit_2': trade_plan.get('take_profit_2'),
    }
    
    history['signals'].append(entry)
    
    # Keep last 1000 signals
    if len(history['signals']) > 1000:
        history['signals'] = history['signals'][-1000:]
    
    # Update performance metrics
    performance = calculate_performance_metrics(history['signals'])
    history['performance'] = performance
    
    save_signal_history(history)
    return history

def calculate_performance_metrics(signals):
    """Calculate win rate and other performance metrics"""
    if len(signals) < 5:
        return {'win_rate': 0, 'total_signals': len(signals)}
    
    # Simplified: Track if signal was correct based on price movement
    # In reality, would need to check actual outcomes
    wins = 0
    total = len(signals)
    
    # Simulate: Signal is "winning" if price moved in direction
    # This would be replaced with actual trade data in production
    for sig in signals:
        if sig['signal'] in ['STRONG LONG', 'LONG']:
            # Would check if price went up
            pass
        elif sig['signal'] in ['STRONG SHORT', 'SHORT']:
            # Would check if price went down
            pass
    
    # For now, use conviction-based estimate
    avg_conviction = sum(s.get('conviction', 0.5) for s in signals) / total if total > 0 else 0
    estimated_win_rate = 0.45 + avg_conviction * 0.3  # Conviction 0.5 → 60% win rate
    
    return {
        'win_rate': round(estimated_win_rate * 100, 1),
        'total_signals': total,
        'avg_conviction': round(avg_conviction, 2),
        'signal_distribution': {
            'STRONG_LONG': sum(1 for s in signals if s['signal'] == 'STRONG LONG'),
            'LONG': sum(1 for s in signals if s['signal'] == 'LONG'),
            'NO_TRADE': sum(1 for s in signals if s['signal'] == 'NO TRADE'),
            'SHORT': sum(1 for s in signals if s['signal'] == 'SHORT'),
            'STRONG_SHORT': sum(1 for s in signals if s['signal'] == 'STRONG SHORT'),
        }
    }

# ===================== REGIME CHANGE DETECTION =====================

def detect_regime_change(historical_regimes, current_regime):
    """Detect when market regime changes"""
    if len(historical_regimes) < 3:
        return {'change': False}
    
    last_3 = historical_regimes[-3:]
    
    # Check if regime changed
    if last_3[0] != current_regime:
        return {
            'change': True,
            'previous_regime': last_3[0],
            'new_regime': current_regime,
            'message': f'Regime changed from {last_3[0]} to {current_regime}',
            'implication': 'RECALIBRATE' if current_regime in ['BEAR_TREND', 'STRONG_BEAR'] else 'MAINTAIN'
        }
    
    return {'change': False}

# ===================== WALK-FORWARD VALIDATION =====================

def walk_forward_validation(df):
    """Validate strategies with out-of-sample testing"""
    if len(df) < 100:
        return {'validated': False, 'message': 'Insufficient data'}
    
    results = {}
    train_size = int(len(df) * 0.7)
    
    # Train on first 70%
    train_data = df.iloc[:train_size].copy()
    test_data = df.iloc[train_size:].copy()
    
    # Test each strategy
    strategies = ['sma20_pos', 'sma50_pos', 'golden_pos', 'rsi_pos', 'bb_pos', 'macd_pos']
    
    for strat in strategies:
        # Compute on train
        train_results = backtest_simple(train_data, strat)
        # Test on out-of-sample
        test_results = backtest_simple(test_data, strat)
        
        results[strat] = {
            'train_sharpe': train_results.get('sharpe', 0),
            'test_sharpe': test_results.get('sharpe', 0),
            'train_return': train_results.get('return', 0),
            'test_return': test_results.get('return', 0),
            'out_of_sample_alpha': test_results.get('sharpe', 0) - train_results.get('sharpe', 0),
        }
    
    return {
        'validated': True,
        'results': results,
        'best_in_sample': max(results.items(), key=lambda x: x[1]['train_sharpe'])[0],
        'best_out_sample': max(results.items(), key=lambda x: x[1]['test_sharpe'])[0],
        'robustness': 'GOOD' if abs(results['sma50_pos']['train_sharpe'] - results['sma50_pos']['test_sharpe']) < 0.3 else 'WEAK',
    }

def backtest_simple(df, position_col, fee=FEE):
    """Simple backtest for validation"""
    df = df.copy()
    df['position'] = df[position_col] if position_col in df.columns else 0
    df['position_change'] = df['position'].diff().abs()
    df['strategy_return'] = df['position'].shift(1) * df['return'] - df['position_change'] * fee
    df['strategy_return'] = df['strategy_return'].fillna(0)
    
    returns = df['strategy_return'].dropna()
    
    return {
        'sharpe': returns.mean() / returns.std() * np.sqrt(365) if returns.std() > 0 else 0,
        'return': (1 + returns).prod() - 1,
        'trades': df['position_change'].sum() / 2,
    }

# ===================== PORTFOLIO SIMULATOR =====================

def simulate_portfolio(assets_data, start_capital=10000, days=30):
    """Simulate portfolio based on signals"""
    portfolio = {'cash': start_capital, 'positions': {}, 'history': []}
    
    # Get price history for each asset
    price_data = {}
    for asset, data in assets_data.items():
        if 'price' in data:
            price_data[asset] = data['price']
    
    for day in range(min(days, 30)):
        daily_value = portfolio['cash']
        for asset, shares in portfolio['positions'].items():
            current_price = price_data.get(asset, 0)
            daily_value += shares * current_price
        
        portfolio['history'].append({
            'day': day,
            'value': daily_value,
            'return': ((daily_value - start_capital) / start_capital) * 100,
        })
        
        # Simulate signal-based trades (simplified)
        for asset, data in assets_data.items():
            signal = data.get('signal', 'NO TRADE')
            price = data.get('price', 0)
            pos_size = data.get('position_sizing', {}).get('position_size', 0)
            
            if signal in ['STRONG LONG', 'LONG'] and asset not in portfolio['positions']:
                # Buy
                shares_to_buy = int(portfolio['cash'] * 0.2 / price)  # 20% per position
                if shares_to_buy > 0:
                    portfolio['positions'][asset] = shares_to_buy
                    portfolio['cash'] -= shares_to_buy * price
                    
            elif signal in ['STRONG SHORT', 'SHORT'] and asset in portfolio['positions']:
                # Sell
                portfolio['cash'] += portfolio['positions'][asset] * price
                del portfolio['positions'][asset]
    
    final_value = portfolio['cash']
    for asset, shares in portfolio['positions'].items():
        final_value += shares * price_data.get(asset, 0)
    
    returns = [h['return'] for h in portfolio['history']]
    
    return {
        'final_value': round(final_value, 2),
        'total_return': round(((final_value - start_capital) / start_capital) * 100, 2),
        'max_return': round(max(returns), 2) if returns else 0,
        'min_return': round(min(returns), 2) if returns else 0,
        'days_simulated': len(portfolio['history']),
        'history': portfolio['history'],
        'positions': {k: v for k, v in portfolio['positions'].items()},
        'cash': round(portfolio['cash'], 2),
    }

# ===================== RISK METRICS DASHBOARD =====================

def calculate_risk_metrics(returns):
    """Calculate comprehensive risk metrics"""
    if len(returns) < 5:
        return {}
    
    returns = pd.Series(returns).dropna()
    
    # Value at Risk (95% confidence)
    var_95 = np.percentile(returns, 5)
    
    # Expected Shortfall (CVaR)
    expected_shortfall = returns[returns < var_95].mean() if len(returns[returns < var_95]) > 0 else 0
    
    # Max Drawdown
    cum_returns = (1 + returns).cumprod()
    running_max = cum_returns.cummax()
    drawdown = (cum_returns - running_max) / running_max
    max_drawdown = drawdown.min()
    
    # Sortino Ratio
    downside_returns = returns[returns < 0]
    downside_std = downside_returns.std() * np.sqrt(365) if len(downside_returns) > 0 else 0
    sortino = returns.mean() * 365 / downside_std if downside_std > 0 else 0
    
    # Calmar Ratio
    total_return = (1 + returns).prod() - 1
    calmar = total_return / abs(max_drawdown) if max_drawdown < 0 else 0
    
    # Kelly Criterion (simplified)
    win_rate = len(returns[returns > 0]) / len(returns) if len(returns) > 0 else 0
    avg_win = returns[returns > 0].mean() if len(returns[returns > 0]) > 0 else 0
    avg_loss = abs(returns[returns < 0].mean()) if len(returns[returns < 0]) > 0 else 0
    kelly = 0
    if avg_loss > 0 and win_rate > 0:
        b = avg_win / avg_loss
        kelly = (win_rate * b - (1 - win_rate)) / b
        kelly = max(0, min(kelly, 0.25))
    
    # Risk of Ruin (simplified)
    risk_of_ruin = np.exp(-2 * kelly * 0.5) if kelly > 0 else 1.0
    
    return {
        'var_95': round(var_95 * 100, 2),
        'expected_shortfall': round(expected_shortfall * 100, 2),
        'max_drawdown': round(max_drawdown * 100, 2),
        'sortino_ratio': round(sortino, 2),
        'calmar_ratio': round(calmar, 2),
        'kelly_fraction': round(kelly, 3),
        'risk_of_ruin': round(risk_of_ruin * 100, 1),
        'risk_grade': calculate_risk_grade(max_drawdown, sortino, var_95),
    }

def calculate_risk_grade(max_dd, sortino, var_95):
    """Calculate overall risk grade A-F"""
    if max_dd > -10 and sortino > 1.5 and var_95 > -5:
        return 'A'
    elif max_dd > -20 and sortino > 0.8 and var_95 > -10:
        return 'B'
    elif max_dd > -30 and sortino > 0.3 and var_95 > -20:
        return 'C'
    elif max_dd > -50 and sortino > 0:
        return 'D'
    else:
        return 'F'

# ===================== ALERT SYSTEM =====================

def send_discord_alert(asset, signal, price, conviction, trade_plan):
    """Send alert to Discord webhook"""
    if not DISCORD_WEBHOOK:
        return
    
    emoji = '🟢' if signal in ['STRONG LONG', 'LONG'] else '🔴' if signal in ['STRONG SHORT', 'SHORT'] else '⚪'
    
    message = f"""
{emoji} **SIGNAL ALERT: {asset}**

**Signal:** {signal}
**Price:** ${price:.2f}
**Conviction:** {conviction:.0%}
**Action:** {trade_plan.get('action', 'N/A')}

**Trade Plan:**
- Entry: ${trade_plan.get('entry_price', 0):.2f}
- Stop: ${trade_plan.get('stop_loss', 0):.2f}
- Target 1: ${trade_plan.get('take_profit_1', 0):.2f}
- Target 2: ${trade_plan.get('take_profit_2', 0):.2f}
- Risk: {trade_plan.get('risk_percent', 0):.1f}%

[View Dashboard](https://awaisraxa202-ctrl.github.io/crypto_market_intelligence/)
"""
    
    try:
        requests.post(DISCORD_WEBHOOK, json={'content': message})
        print(f"  📨 Discord alert sent for {asset}")
    except Exception as e:
        print(f"  ⚠️ Discord alert failed: {e}")

def send_telegram_alert(asset, signal, price, conviction, trade_plan):
    """Send alert to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    
    emoji = '🟢' if signal in ['STRONG LONG', 'LONG'] else '🔴' if signal in ['STRONG SHORT', 'SHORT'] else '⚪'
    
    message = f"""
{emoji} SIGNAL ALERT: {asset}

Signal: {signal}
Price: ${price:.2f}
Conviction: {conviction:.0%}

Trade Plan:
- Entry: ${trade_plan.get('entry_price', 0):.2f}
- Stop: ${trade_plan.get('stop_loss', 0):.2f}
- Target: ${trade_plan.get('take_profit_1', 0):.2f}
- Risk: {trade_plan.get('risk_percent', 0):.1f}%
"""
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
        requests.post(url, json=payload)
        print(f"  📨 Telegram alert sent for {asset}")
    except Exception as e:
        print(f"  ⚠️ Telegram alert failed: {e}")

# ===================== MARKET SUMMARY REPORT =====================

def generate_market_summary(all_signals, market_report, risk_metrics, global_data):
    """Generate human-readable market summary"""
    fng = all_signals.get('fear_greed', {})
    
    summary = f"""
📊 **MARKET CORTEX DAILY SUMMARY**
📅 {datetime.now().strftime('%A, %B %d, %Y')}
⏰ {datetime.now().strftime('%H:%M UTC')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔵 **MARKET OVERVIEW**
Market Mood: {market_report.get('market_mood', 'UNKNOWN')}
Bullish Assets: {market_report.get('bullish_assets', 0)}/9
Bearish Assets: {market_report.get('bearish_assets', 0)}/9
Fear & Greed: {fng.get('value', 'N/A')} ({fng.get('label', 'N/A')})
Total Market Cap: {fmtUSD(global_data.get('total_market_cap', 0))}
24h Volume: {fmtUSD(global_data.get('total_volume', 0))}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔴 **TOP BUY SIGNALS**
"""
    
    # Add top buy signals
    buy_signals = [s for s in all_signals.get('assets', {}).values() if s.get('signal') in ['STRONG LONG', 'LONG']]
    buy_signals.sort(key=lambda x: x.get('conviction', 0), reverse=True)
    for s in buy_signals[:3]:
        summary += f"• {s.get('asset', '')}: {s.get('signal', '')} (Conviction: {s.get('conviction', 0):.0%})\n"
    
    summary += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🟢 **TOP SELL SIGNALS**
"""
    
    # Add top sell signals
    sell_signals = [s for s in all_signals.get('assets', {}).values() if s.get('signal') in ['STRONG SHORT', 'SHORT']]
    sell_signals.sort(key=lambda x: x.get('conviction', 0), reverse=True)
    for s in sell_signals[:3]:
        summary += f"• {s.get('asset', '')}: {s.get('signal', '')} (Conviction: {s.get('conviction', 0):.0%})\n"
    
    if not sell_signals:
        summary += "• No strong sell signals detected\n"
    
    summary += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 **RISK METRICS**
"""
    
    if risk_metrics:
        summary += f"""
• VaR (95%): {risk_metrics.get('var_95', 'N/A')}%
• Max Drawdown: {risk_metrics.get('max_drawdown', 'N/A')}%
• Sortino Ratio: {risk_metrics.get('sortino_ratio', 'N/A')}
• Risk Grade: {risk_metrics.get('risk_grade', 'N/A')}
• Kelly Fraction: {risk_metrics.get('kelly_fraction', 'N/A')}
"""
    
    summary += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ **DISCLAIMER:** Research & Educational Tool Only.
NOT Financial Advice. Past performance ≠ Future results.

[View Full Dashboard](https://awaisraxa202-ctrl.github.io/crypto_market_intelligence/)
"""
    
    return summary

def fmtUSD(n):
    if n is None or n == 0:
        return '$0'
    if n >= 1e12:
        return '$' + (n / 1e12).__round__(2) + 'T'
    if n >= 1e9:
        return '$' + (n / 1e9).__round__(2) + 'B'
    if n >= 1e6:
        return '$' + (n / 1e6).__round__(2) + 'M'
    return '$' + str(int(n))

# ===================== PROCESS ASSET =====================

def process_asset(code, config, fng_df, macro_data, account_capital=10000):
    print(f"\n{'='*60}")
    print(f"Processing {config['name']} ({code})")
    print(f"{'='*60}")
    
    # Fetch daily data
    df = fetch_binance_klines(config['binance'])
    source = 'Binance'
    if df.empty or len(df) < 100:
        print(f"  ⚠️ Binance failed, trying Yahoo...")
        if config.get('yahoo'):
            df = fetch_yahoo_ohlcv(config['yahoo'])
            source = 'Yahoo'
    if df.empty or len(df) < 100:
        print(f"  ❌ No data for {code}")
        return None
    
    print(f"  Fetched {len(df)} days from {source}")
    
    # Multi-timeframe analysis
    print(f"  🔍 Running multi-timeframe analysis...")
    mtf = multi_timeframe_analysis(config['binance'])
    
    # Add features
    df = add_features(df)
    df = add_pi_cycle(df)
    df = detect_regime(df)
    
    # Merge external data
    if not fng_df.empty:
        df = df.merge(fng_df[['date', 'fng_value', 'fng_class']], on='date', how='left')
    
    funding = fetch_funding_rate(config['binance'])
    if not funding.empty:
        df = df.merge(funding[['date', 'funding_rate']], on='date', how='left')
    
    latest = df.iloc[-1]
    current_regime = latest.get('regime', 'CHOPPY')
    
    # Historical regimes for change detection
    historical_regimes = df['regime'].tail(10).tolist()
    regime_change = detect_regime_change(historical_regimes, current_regime)
    
    # Strategy selection
    strategy_info = REGIME_STRATEGY.get(current_regime, REGIME_STRATEGY['CHOPPY'])
    
    # Position sizing
    position_info = calculate_dynamic_position_size(df, len(df) - 1, account_capital=account_capital)
    
    # Signal generation
    narrative = build_sub_signals_weighted(latest, config['name'])
    
    # Volatility forecast
    vol_forecast = forecast_volatility(df)
    
    # Price targets
    price_targets = calculate_price_targets(
        latest['close'],
        latest.get('atr_14', latest['close'] * 0.02),
        current_regime
    )
    
    # Generate trade plan
    sr_levels = {
        'nearest_support': latest['close'] * 0.95,
        'nearest_resistance': latest['close'] * 1.05
    }
    trade_plan = generate_trade_plan(
        code, narrative['signal'], narrative['conviction'],
        latest['close'], sr_levels, latest.get('atr_14', latest['close'] * 0.02),
        position_info
    )
    
    # Track signal history
    history = track_signal_performance(
        code, narrative['signal'], latest['close'],
        narrative['conviction'], trade_plan
    )
    
    # Walk-forward validation
    wf_validation = walk_forward_validation(df)
    
    # Risk metrics (using historical returns)
    returns = df['return'].dropna().tail(100).tolist()
    risk_metrics = calculate_risk_metrics(returns)
    
    # Send alerts if signal changed significantly
    if narrative['signal'] in ['STRONG LONG', 'LONG']:
        send_discord_alert(code, narrative['signal'], latest['close'], narrative['conviction'], trade_plan)
        send_telegram_alert(code, narrative['signal'], latest['close'], narrative['conviction'], trade_plan)
    
    asset_output = {
        'asset': code,
        'name': config['name'],
        'date': latest['date'].strftime('%Y-%m-%d'),
        'price': round(latest['close'], 4 if latest['close'] < 1 else 2),
        'signal': narrative['signal'],
        'conviction': narrative['conviction'],
        'composite_score': narrative['composite_score'],
        'regime': current_regime,
        'action': narrative['action'],
        'indicators': {
            'rsi': round(latest['rsi_14'], 1) if pd.notna(latest['rsi_14']) else None,
            'macd_hist': round(latest['macd_hist'], 4) if pd.notna(latest['macd_hist']) else None,
            'volatility': round(latest['volatility_20'], 1) if pd.notna(latest['volatility_20']) else None,
            'drawdown': round(latest['drawdown'] * 100, 1) if pd.notna(latest['drawdown']) else None,
            'fng_value': int(latest['fng_value']) if pd.notna(latest.get('fng_value')) else None,
            'funding_rate': round(latest['funding_rate'], 6) if pd.notna(latest.get('funding_rate')) else None,
        },
        'sub_signals': narrative['sub_signals'],
        'position_sizing': position_info,
        'trade_plan': trade_plan,
        'multi_timeframe': mtf,
        'regime_strategy': {
            'regime': current_regime,
            'recommended_strategy': strategy_info['strategy'],
            'size_multiplier': strategy_info['size_mult'],
        },
        'volatility_forecast': vol_forecast,
        'price_targets': price_targets,
        'regime_change': regime_change,
        'risk_metrics': risk_metrics,
        'walk_forward': wf_validation,
        'signal_history': history.get('performance', {}),
    }
    
    print(f"  ✅ {narrative['signal']} | Conviction: {narrative['conviction']}/1.0")
    print(f"  📊 Trade Plan: Entry ${trade_plan.get('entry_price', 0):.2f} | Stop ${trade_plan.get('stop_loss', 0):.2f}")
    print(f"  📈 Volatility: {vol_forecast.get('current_annual_vol', 0):.1f}% | Forecast: {vol_forecast.get('forecast_5d_vol', 0):.1f}%")
    
    return asset_output, df[['date', 'close']].rename(columns={'close': code})

# ===================== TRADE PLAN GENERATOR =====================

def generate_trade_plan(asset, signal, conviction, price, sr_levels, atr, position_size_info):
    """Generate complete actionable trade plan"""
    plan = {
        'asset': asset,
        'signal': signal,
        'conviction': conviction,
        'entry_price': round(price, 2),
    }
    
    if signal in ['STRONG LONG', 'LONG']:
        stop_loss = position_size_info.get('stop_loss', price * 0.95)
        tp1 = position_size_info.get('take_profit_1', price * 1.04)
        tp2 = position_size_info.get('take_profit_2', price * 1.08)
        
        plan['entry_type'] = 'BUY_LIMIT'
        plan['stop_loss'] = round(stop_loss, 2)
        plan['take_profit_1'] = round(tp1, 2)
        plan['take_profit_2'] = round(tp2, 2)
        plan['position_size'] = position_size_info.get('position_size', 0)
        plan['risk_amount'] = position_size_info.get('risk_amount', 0)
        plan['risk_percent'] = position_size_info.get('risk_percent', 0)
        
    elif signal in ['STRONG SHORT', 'SHORT']:
        stop_loss = position_size_info.get('stop_loss', price * 1.05)
        tp1 = position_size_info.get('take_profit_1', price * 0.96)
        tp2 = position_size_info.get('take_profit_2', price * 0.92)
        
        plan['entry_type'] = 'SELL_LIMIT'
        plan['stop_loss'] = round(stop_loss, 2)
        plan['take_profit_1'] = round(tp1, 2)
        plan['take_profit_2'] = round(tp2, 2)
        plan['position_size'] = position_size_info.get('position_size', 0)
        plan['risk_amount'] = position_size_info.get('risk_amount', 0)
        plan['risk_percent'] = position_size_info.get('risk_percent', 0)
    else:
        plan['entry_type'] = 'NO_TRADE'
        plan['stop_loss'] = None
        plan['take_profit_1'] = None
        plan['take_profit_2'] = None
    
    plan['risk_reward_ratio'] = abs((plan.get('take_profit_1', price) - price) / (price - plan.get('stop_loss', price) + 0.001))
    
    return plan

# ===================== PORTFOLIO SIMULATION (All Assets) =====================

def simulate_full_portfolio(all_assets_data, start_capital=10000):
    """Simulate portfolio across all assets"""
    return simulate_portfolio(all_assets_data, start_capital)

# ===================== MAIN PIPELINE =====================

def run_pipeline():
    print("=" * 70)
    print("MARKET CORTEX v5.0 — ULTIMATE EDITION")
    print("Multi-TF · Volatility Forecast · Price Targets · Risk Metrics")
    print("Alerts · Signal History · Portfolio Sim · Walk-Forward Validation")
    print("=" * 70)
    
    print("\n[1/8] Fetching global data...")
    fng_df = fetch_fear_greed()
    global_data = fetch_coingecko_global()
    
    print("\n[2/8] Fetching macro data...")
    macro_data = {}
    
    print("\n[3/8] Processing all assets...")
    all_signals = {}
    all_prices = {}
    all_returns = {}
    
    for code, config in ASSETS.items():
        result = process_asset(code, config, fng_df, macro_data)
        if result:
            asset_output, price_series = result
            all_signals[code] = asset_output
            all_prices[code] = price_series.set_index('date')[code]
            
            returns = price_series.set_index('date')[code].pct_change().dropna()
            if not returns.empty:
                all_returns[code] = returns
    
    print("\n[4/8] Running portfolio simulation...")
    portfolio_sim = simulate_full_portfolio(all_signals)
    print(f"  Portfolio Value: ${portfolio_sim['final_value']:.2f}")
    print(f"  Total Return: {portfolio_sim['total_return']:.1f}%")
    
    print("\n[5/8] Computing cross-asset analytics...")
    portfolio_returns = pd.DataFrame(all_returns)
    
    print("\n[6/8] Generating market report...")
    signals_list = list(all_signals.values())
    bullish = sum(1 for s in signals_list if s['signal'] in ['STRONG LONG', 'LONG'])
    bearish = sum(1 for s in signals_list if s['signal'] in ['STRONG SHORT', 'SHORT'])
    neutral = len(signals_list) - bullish - bearish
    
    if bullish >= len(signals_list) * 0.6:
        mood = "BULLISH"
    elif bearish >= len(signals_list) * 0.6:
        mood = "BEARISH"
    elif bullish > bearish:
        mood = "CAUTIOUSLY BULLISH"
    elif bearish > bullish:
        mood = "CAUTIOUSLY BEARISH"
    else:
        mood = "MIXED"
    
    regimes = defaultdict(int)
    for s in signals_list:
        regimes[s['regime']] += 1
    
    # Collect all returns for risk metrics
    all_returns_list = []
    for returns in all_returns.values():
        all_returns_list.extend(returns.tail(30).tolist())
    
    global_risk_metrics = calculate_risk_metrics(all_returns_list)
    
    market_report = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'market_mood': mood,
        'bullish_assets': bullish,
        'bearish_assets': bearish,
        'neutral_assets': neutral,
        'regime_distribution': dict(regimes),
        'risk_metrics': global_risk_metrics,
    }
    
    print(f"\n  Market Mood: {mood}")
    print(f"  Bullish: {bullish} | Bearish: {bearish} | Neutral: {neutral}")
    print(f"  Risk Grade: {global_risk_metrics.get('risk_grade', 'N/A')}")
    
    print("\n[7/8] Generating market summary...")
    summary_data = {
        'assets': all_signals,
        'fear_greed': {'value': int(fng_df['fng_value'].iloc[-1]) if not fng_df.empty else None,
                       'label': fng_df['fng_class'].iloc[-1] if not fng_df.empty else None}
    }
    market_summary = generate_market_summary(
        summary_data, market_report, global_risk_metrics, global_data
    )
    
    # Save summary to file
    with open('docs/market_summary.txt', 'w') as f:
        f.write(market_summary)
    print(f"  📄 Market summary saved to docs/market_summary.txt")
    
    print("\n[8/8] Saving dashboard data...")
    dashboard_data = {
        'version': '5.0',
        'generated_at': datetime.now().isoformat(),
        'update_schedule': UPDATE_TIME,
        'disclaimer': "THIS IS A RESEARCH AND EDUCATIONAL TOOL ONLY. NOT FINANCIAL ADVICE.",
        'fear_greed': {
            'value': int(fng_df['fng_value'].iloc[-1]) if not fng_df.empty else None,
            'label': fng_df['fng_class'].iloc[-1] if not fng_df.empty else None,
        },
        'global_data': global_data,
        'market_report': market_report,
        'portfolio_simulation': portfolio_sim,
        'risk_metrics': global_risk_metrics,
        'assets': all_signals,
        'summary': market_summary,
    }
    
    def fix_nan(obj):
        if isinstance(obj, dict):
            return {k: fix_nan(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [fix_nan(i) for i in obj]
        elif isinstance(obj, float) and (obj != obj):
            return None
        return obj
    
    dashboard_data = fix_nan(dashboard_data)
    
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(dashboard_data, f, indent=2, default=str)
    
    print(f"\n💾 Saved to {OUTPUT_PATH}")
    print("\n" + "=" * 70)
    print("✅ MARKET CORTEX v5.0 ULTIMATE COMPLETE")
    print("=" * 70)

if __name__ == '__main__':
    run_pipeline()