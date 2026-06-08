"""
محرك الإشارات - يطبق منطق مؤشر HyperTrend Pro
يحسب: Supertrend + ADX + MACD + EMA + تأكيد متعدد الفريمات
"""

import logging
import numpy as np
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

log = logging.getLogger(__name__)


class SignalEngine:
    def __init__(self, client: Client, timeframes: list, min_confirm: int = 2):
        self.client      = client
        self.timeframes  = timeframes
        self.min_confirm = min_confirm

        # خريطة الفريمات
        self.tf_map = {
            '1m':  Client.KLINE_INTERVAL_1MINUTE,
            '3m':  Client.KLINE_INTERVAL_3MINUTE,
            '5m':  Client.KLINE_INTERVAL_5MINUTE,
            '15m': Client.KLINE_INTERVAL_15MINUTE,
        }

    # ──────────────────────────────────────────────────────
    # جلب البيانات
    # ──────────────────────────────────────────────────────
    def get_candles(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        try:
            klines = self.client.get_klines(
                symbol=symbol,
                interval=self.tf_map[interval],
                limit=limit
            )
            df = pd.DataFrame(klines, columns=[
                'time','open','high','low','close','volume',
                'close_time','qav','num_trades','taker_buy_base',
                'taker_buy_quote','ignore'
            ])
            for col in ['open','high','low','close','volume']:
                df[col] = df[col].astype(float)
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            return df
        except BinanceAPIException as e:
            log.debug(f"خطأ في جلب كاندلز {symbol} {interval}: {e}")
            return pd.DataFrame()

    # ──────────────────────────────────────────────────────
    # المؤشرات
    # ──────────────────────────────────────────────────────
    def calc_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1/period, adjust=False).mean()

    def calc_supertrend(self, df: pd.DataFrame, factor: float = 3.1, period: int = 25):
        atr = self.calc_atr(df, period)
        hl2 = (df['high'] + df['low']) / 2
        upper = hl2 + factor * atr
        lower = hl2 - factor * atr

        supertrend = pd.Series(index=df.index, dtype=float)
        direction  = pd.Series(index=df.index, dtype=int)

        for i in range(1, len(df)):
            # Lower band
            if lower.iloc[i] > lower.iloc[i-1] or df['close'].iloc[i-1] < lower.iloc[i-1]:
                lb = lower.iloc[i]
            else:
                lb = lower.iloc[i-1]

            # Upper band
            if upper.iloc[i] < upper.iloc[i-1] or df['close'].iloc[i-1] > upper.iloc[i-1]:
                ub = upper.iloc[i]
            else:
                ub = upper.iloc[i-1]

            # Direction
            prev_st = supertrend.iloc[i-1] if i > 1 else ub
            if prev_st == upper.iloc[i-1] if i > 1 else True:
                if df['close'].iloc[i] > ub:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = lb
                else:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = ub
            else:
                if df['close'].iloc[i] < lb:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = ub
                else:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = lb

        return supertrend, direction

    def calc_adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df['high'], df['low'], df['close']
        plus_dm  = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        plus_dm[plus_dm < minus_dm]  = 0
        minus_dm[minus_dm < plus_dm] = 0

        atr      = self.calc_atr(df, period)
        plus_di  = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr
        dx       = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
        adx      = dx.ewm(alpha=1/period, adjust=False).mean()
        return adx

    def calc_macd(self, df: pd.DataFrame):
        ema12  = df['close'].ewm(span=12, adjust=False).mean()
        ema26  = df['close'].ewm(span=26, adjust=False).mean()
        macd   = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist   = macd - signal
        return macd, signal, hist

    def calc_ema(self, df: pd.DataFrame, period: int) -> pd.Series:
        return df['close'].ewm(span=period, adjust=False).mean()

    # ──────────────────────────────────────────────────────
    # فحص إشارة فريم واحد
    # ──────────────────────────────────────────────────────
    def check_timeframe(self, symbol: str, tf: str) -> dict | None:
        df = self.get_candles(symbol, tf, limit=200)
        if df.empty or len(df) < 50:
            return None

        # المؤشرات
        st, direction = self.calc_supertrend(df, factor=3.1, period=25)
        adx           = self.calc_adx(df)
        macd, _, hist = self.calc_macd(df)
        ema150        = self.calc_ema(df, 150)
        ema200        = self.calc_ema(df, 200)
        ema250        = self.calc_ema(df, 250)
        atr           = self.calc_atr(df)

        # آخر شمعتين
        i  = len(df) - 2   # الشمعة المغلقة الأخيرة
        i1 = i - 1

        close_now  = df['close'].iloc[i]
        close_prev = df['close'].iloc[i1]
        st_now     = st.iloc[i]
        st_prev    = st.iloc[i1]
        dir_now    = direction.iloc[i]
        dir_prev   = direction.iloc[i1]

        adx_val    = adx.iloc[i]
        macd_val   = macd.iloc[i]
        hist_val   = hist.iloc[i]
        hist_prev  = hist.iloc[i1]

        ema150_val = ema150.iloc[i]
        ema200_val = ema200.iloc[i]
        ema250_val = ema250.iloc[i]
        atr_val    = atr.iloc[i]

        # ── شروط إشارة الشراء (نفس منطق HyperTrend Pro) ──
        # 1. تقاطع فوق Supertrend (مع تأكيد الشمعة)
        cross_bull = (close_prev < st_prev) and (close_now > st_now)

        # 2. MACD إيجابي ومتصاعد
        macd_bull  = macd_val > 0 and hist_val > hist_prev

        # 3. EMA150 فوق EMA250 (اتجاه صاعد)
        trend_bull = ema150_val > ema250_val

        # 4. السعر فوق EMA200
        above_ema200 = close_now > ema200_val

        # 5. ADX فوق 20 (سوق متحرك وليس جانبي)
        adx_ok = adx_val > 20

        # ── تأكيد الشمعة: الشمعة الحالية خضراء ──
        current_candle_bull = df['close'].iloc[-1] > df['open'].iloc[-1]

        # ── قرار الإشارة ──
        is_buy = cross_bull and macd_bull and trend_bull and adx_ok and current_candle_bull

        if not is_buy:
            return None

        # حساب TP/SL
        entry   = df['close'].iloc[-1]
        sl_dist = atr_val * 2.2
        stop    = entry - sl_dist
        tp1     = entry + sl_dist * 1.0
        tp2     = entry + sl_dist * 2.0
        tp3     = entry + sl_dist * 3.0

        return {
            'symbol':    symbol,
            'timeframe': tf,
            'entry':     entry,
            'stop':      stop,
            'tp1':       tp1,
            'tp2':       tp2,
            'tp3':       tp3,
            'adx':       adx_val,
            'above_strong': above_ema200,
        }

    # ──────────────────────────────────────────────────────
    # تحليل متعدد الفريمات
    # ──────────────────────────────────────────────────────
    def analyze(self, symbol: str) -> dict | None:
        confirmations = []
        results       = {}

        for tf in self.timeframes:
            result = self.check_timeframe(symbol, tf)
            if result:
                confirmations.append(tf)
                results[tf] = result

        if len(confirmations) < self.min_confirm:
            return None

        # أفضل فريم = الأعلى ADX
        best_tf     = max(results, key=lambda t: results[t]['adx'])
        best_result = results[best_tf]

        log.info(f"✅ {symbol}: إشارة مؤكدة على {confirmations} | أفضل فريم: {best_tf}")

        return {
            'type':         'BUY',
            'symbol':       symbol,
            'best_tf':      best_tf,
            'confirmed_tf': len(confirmations),
            'timeframes':   confirmations,
            'entry':        best_result['entry'],
            'stop':         best_result['stop'],
            'tp1':          best_result['tp1'],
            'tp2':          best_result['tp2'],
            'tp3':          best_result['tp3'],
            'adx':          best_result['adx'],
        }
