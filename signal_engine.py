"""
محرك الإشارات - يطبق منطق مؤشر HyperTrend [LuxAlgo]
نفس المنطق الموجود في TradingView بالضبط
Supertrend + EMA150/250 + MACD + HMA55 + dchannel
"""

import logging
import numpy as np
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

log = logging.getLogger(__name__)


class SignalEngine:
    def __init__(self, client: Client, timeframes: list, min_confirm: int = 2):
        self.client     = client
        self.timeframes = timeframes
        self.min_confirm = min_confirm
        self.tf_map = {
            '1m':  Client.KLINE_INTERVAL_1MINUTE,
            '3m':  Client.KLINE_INTERVAL_3MINUTE,
            '5m':  Client.KLINE_INTERVAL_5MINUTE,
            '15m': Client.KLINE_INTERVAL_15MINUTE,
        }

    # ──────────────────────────────────────────────────────
    # جلب البيانات
    # ──────────────────────────────────────────────────────
    def get_candles(self, symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
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
            return df.reset_index(drop=True)
        except BinanceAPIException as e:
            log.debug(f"خطأ {symbol} {interval}: {e}")
            return pd.DataFrame()

    # ──────────────────────────────────────────────────────
    # المؤشرات - نفس منطق TradingView
    # ──────────────────────────────────────────────────────
    def calc_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high  = df['high']
        low   = df['low']
        close = df['close']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1/period, adjust=False).mean()

    def calc_supertrend(self, df: pd.DataFrame, factor: float = 3.1, period: int = 25):
        """
        نفس دالة supertrend في كود TradingView بالضبط
        sensitivity=3.1, STuner=25
        """
        atr   = self.calc_atr(df, period)
        hl2   = (df['high'] + df['low']) / 2
        upper = hl2 + factor * atr
        lower = hl2 - factor * atr

        supertrend = [np.nan] * len(df)
        direction  = [0] * len(df)

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

            prev_st = supertrend[i-1] if not np.isnan(supertrend[i-1]) else ub

            if prev_st == upper.iloc[i-1]:
                if df['close'].iloc[i] > ub:
                    direction[i]  = 1
                    supertrend[i] = lb
                else:
                    direction[i]  = -1
                    supertrend[i] = ub
            else:
                if df['close'].iloc[i] < lb:
                    direction[i]  = -1
                    supertrend[i] = ub
                else:
                    direction[i]  = 1
                    supertrend[i] = lb

        return pd.Series(supertrend, index=df.index), pd.Series(direction, index=df.index)

    def calc_dchannel(self, df: pd.DataFrame, period: int = 30) -> pd.Series:
        """
        نفس دالة dchannel في TradingView
        maintrend = dchannel(30)
        """
        hh = df['close'].rolling(period).max()
        ll = df['close'].rolling(period).min()
        trend = pd.Series(0, index=df.index)
        for i in range(period, len(df)):
            if df['close'].iloc[i] > hh.iloc[i-1]:
                trend.iloc[i] = 1
            elif df['close'].iloc[i] < ll.iloc[i-1]:
                trend.iloc[i] = -1
            else:
                trend.iloc[i] = trend.iloc[i-1]
        return trend

    def calc_hma(self, df: pd.DataFrame, period: int = 55) -> pd.Series:
        """
        HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))
        hma55 في TradingView
        """
        half = int(period / 2)
        sqrt = int(np.sqrt(period))
        wma_half = df['close'].rolling(half).mean()
        wma_full = df['close'].rolling(period).mean()
        diff     = 2 * wma_half - wma_full
        hma      = diff.rolling(sqrt).mean()
        return hma

    def calc_macd(self, df: pd.DataFrame):
        """
        نفس MACD في TradingView: 12, 26, 9
        """
        ema12  = df['close'].ewm(span=12, adjust=False).mean()
        ema26  = df['close'].ewm(span=26, adjust=False).mean()
        macd   = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist   = macd - signal
        return macd, signal, hist

    def calc_ema(self, df: pd.DataFrame, period: int) -> pd.Series:
        return df['close'].ewm(span=period, adjust=False).mean()

    def calc_adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high     = df['high']
        low      = df['low']
        plus_dm  = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        plus_dm[plus_dm < minus_dm]  = 0
        minus_dm[minus_dm < plus_dm] = 0
        atr      = self.calc_atr(df, period)
        plus_di  = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr
        dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        adx      = dx.ewm(alpha=1/period, adjust=False).mean()
        return adx

    # ──────────────────────────────────────────────────────
    # فحص إشارة فريم واحد
    # ──────────────────────────────────────────────────────
    def check_timeframe(self, symbol: str, tf: str) -> dict | None:
        df = self.get_candles(symbol, tf, limit=300)
        if df.empty or len(df) < 260:
            return None

        # حساب المؤشرات - نفس TradingView
        st, direction  = self.calc_supertrend(df, factor=3.1, period=25)
        maintrend      = self.calc_dchannel(df, period=30)
        macd, _, hist  = self.calc_macd(df)
        ema150         = self.calc_ema(df, 150)
        ema250         = self.calc_ema(df, 250)
        hma55          = self.calc_hma(df, 55)
        atr            = self.calc_atr(df)
        adx            = self.calc_adx(df)

        # آخر شمعة مغلقة
        i  = len(df) - 2
        i1 = i - 1
        i2 = i - 2

        close_now  = df['close'].iloc[i]
        close_prev = df['close'].iloc[i1]
        st_now     = st.iloc[i]
        st_prev    = st.iloc[i1]

        macd_val   = macd.iloc[i]
        hist_val   = hist.iloc[i]
        hist_prev  = hist.iloc[i1]

        ema150_val = ema150.iloc[i]
        ema250_val = ema250.iloc[i]

        hma55_now  = hma55.iloc[i]
        hma55_old  = hma55.iloc[i2]

        main_now   = maintrend.iloc[i]
        main_prev  = maintrend.iloc[i1]

        atr_val    = atr.iloc[i]
        adx_val    = adx.iloc[i]

        # ── شروط إشارة الشراء - نفس confBull في TradingView ──
        # cross_bull: السعر يتجاوز Supertrend للأعلى
        cross_bull = (close_prev < st_prev) and (close_now > st_now)

        # macd إيجابي ومتصاعد
        macd_bull = macd_val > 0 and hist_val > hist_prev

        # EMA150 فوق EMA250
        ema_bull = ema150_val > ema250_val

        # HMA55 صاعد
        hma_bull = hma55_now > hma55_old

        # الاتجاه العام صاعد
        trend_bull = main_now > 0

        # ADX فوق 20
        adx_ok = adx_val > 20

        # ── قرار الشراء النهائي ──
        is_buy = cross_bull and macd_bull and ema_bull and hma_bull and trend_bull and adx_ok

        if not is_buy:
            return None

        # حساب نقاط الدخول والخروج
        entry   = df['close'].iloc[-1]
        sl_dist = atr_val * 2.2
        stop    = entry - sl_dist
        tp1     = entry + sl_dist * 1.0
        tp2     = entry + sl_dist * 2.0
        tp3     = entry + sl_dist * 3.0

        score = sum([
            cross_bull, macd_bull, ema_bull,
            hma_bull, trend_bull, adx_ok
        ])

        return {
            'symbol':    symbol,
            'timeframe': tf,
            'direction': 'BUY',
            'entry':     entry,
            'stop':      stop,
            'tp1':       tp1,
            'tp2':       tp2,
            'tp3':       tp3,
            'adx':       adx_val,
            'score':     score,
        }

    # ──────────────────────────────────────────────────────
    # تحليل متعدد الفريمات
    # ──────────────────────────────────────────────────────
    def analyze(self, symbol: str) -> dict | None:
        confirmations = []
        results       = {}

        for tf in self.timeframes:
            try:
                result = self.check_timeframe(symbol, tf)
                if result:
                    confirmations.append(tf)
                    results[tf] = result
            except Exception as e:
                log.debug(f"خطأ {symbol} {tf}: {e}")

        if len(confirmations) < self.min_confirm:
            return None

        # أفضل فريم = الأعلى ADX
        best_tf     = max(results, key=lambda t: results[t]['adx'])
        best_result = results[best_tf]

        log.info(f"✅ {symbol}: إشارة على {confirmations} | أفضل فريم: {best_tf}")

        return {
            'type':      'BUY',
            'direction': 'BUY',
            'symbol':    symbol,
            'best_tf':   best_tf,
            'timeframes': confirmations,
            'entry':     best_result['entry'],
            'stop':      best_result['stop'],
            'tp1':       best_result['tp1'],
            'tp2':       best_result['tp2'],
            'tp3':       best_result['tp3'],
            'adx':       best_result['adx'],
            'score':     len(confirmations),
        }
