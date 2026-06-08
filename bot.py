"""
╔══════════════════════════════════════════════════════════════╗
║          HyperTrend Bot - Binance Spot Only                  ║
║  يتبع إشارات مؤشر HyperTrend Pro على فريمات 1-15 دقيقة     ║
╚══════════════════════════════════════════════════════════════╝

المنطق:
- يراقب عدة فريمات (1د، 3د، 5د، 15د)
- يدخل فقط إذا تأكدت الإشارة على فريمين أو أكثر
- 50% من الكمية يخرج عند TP1
- 25% عند TP2 ، 25% عند TP3
- وقف الخسارة يتحرك إلى نقطة الدخول بعد تحقق TP1
"""

import os
import time
import logging
import math
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv
from signal_engine import SignalEngine
from trade_manager import TradeManager

# ── إعداد اللوج ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

load_dotenv()

# ── الإعدادات ──────────────────────────────────────────────
SYMBOLS   = []          # يتملى من ملف symbols.txt
TIMEFRAMES = ['1m', '3m', '5m', '15m']
MIN_CONFIRM = 2         # عدد الفريمات التي يجب تأكيد الإشارة فيها
LOOP_SLEEP  = 30        # ثواني بين كل دورة فحص

def load_symbols():
    """تحميل قائمة العملات من ملف symbols.txt"""
    path = 'symbols.txt'
    if not os.path.exists(path):
        # قائمة افتراضية حلال (بدون ستيبل كوين ضد ستيبل كوين)
        defaults = [
            'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT',
            'ADAUSDT', 'DOTUSDT', 'LINKUSDT', 'AVAXUSDT',
            'MATICUSDT', 'ATOMUSDT', 'NEARUSDT', 'FTMUSDT'
        ]
        with open(path, 'w') as f:
            f.write('\n'.join(defaults))
        log.info(f"تم إنشاء symbols.txt بـ {len(defaults)} عملة افتراضية")
        return defaults
    with open(path) as f:
        syms = [l.strip().upper() for l in f if l.strip() and not l.startswith('#')]
    log.info(f"تم تحميل {len(syms)} عملة من symbols.txt")
    return syms


def get_balance(client, asset='USDT'):
    """جلب الرصيد المتاح"""
    try:
        bal = client.get_asset_balance(asset=asset)
        return float(bal['free']) if bal else 0.0
    except BinanceAPIException as e:
        log.error(f"خطأ في جلب الرصيد: {e}")
        return 0.0


def main():
    log.info("=" * 60)
    log.info("  HyperTrend Bot بدأ التشغيل")
    log.info("=" * 60)

    # الاتصال بـ Binance
    api_key    = os.getenv('BINANCE_API_KEY', '')
    api_secret = os.getenv('BINANCE_API_SECRET', '')

    if not api_key or not api_secret:
        log.error("❌ لم يتم إيجاد BINANCE_API_KEY أو BINANCE_API_SECRET في ملف .env")
        return

    client = Client(api_key, api_secret)
    log.info("✅ تم الاتصال بـ Binance بنجاح")

    # تحميل العملات
    symbols = load_symbols()

    # مدير الإشارات
    signal_engine = SignalEngine(client, TIMEFRAMES, MIN_CONFIRM)

    # مدير الصفقات
    trade_manager = TradeManager(client)

    log.info(f"📊 يراقب {len(symbols)} عملة على فريمات: {TIMEFRAMES}")
    log.info(f"✅ الحد الأدنى للتأكيد: {MIN_CONFIRM} فريمات")
    log.info("-" * 60)

    while True:
        try:
            # تحقق من الصفقات المفتوحة أولاً
            trade_manager.check_open_trades()

            # حساب الرصيد المتاح
            balance = get_balance(client, 'USDT')
            log.info(f"💰 الرصيد المتاح: {balance:.2f} USDT")

            if balance < 10:
                log.warning("⚠️  الرصيد أقل من 10 USDT - لا يمكن فتح صفقات جديدة")
                time.sleep(LOOP_SLEEP)
                continue

            # فحص الإشارات لكل عملة
            for symbol in symbols:
                # تخطي العملات التي عندها صفقة مفتوحة
                if trade_manager.has_open_trade(symbol):
                    continue

                signal = signal_engine.analyze(symbol)

                if signal and signal['type'] == 'BUY':
                    log.info(f"🚀 إشارة شراء على {symbol} | "
                             f"تأكيد على {signal['confirmed_tf']} فريمات | "
                             f"ADX: {signal['adx']:.1f}")

                    # فتح الصفقة بكل الرصيد المتاح
                    trade_manager.open_trade(
                        symbol   = symbol,
                        balance  = balance,
                        entry    = signal['entry'],
                        stop     = signal['stop'],
                        tp1      = signal['tp1'],
                        tp2      = signal['tp2'],
                        tp3      = signal['tp3'],
                        timeframe= signal['best_tf']
                    )
                    # تحديث الرصيد بعد الدخول
                    balance = get_balance(client, 'USDT')

            time.sleep(LOOP_SLEEP)

        except KeyboardInterrupt:
            log.info("🛑 تم إيقاف البوت يدوياً")
            break
        except Exception as e:
            log.error(f"خطأ غير متوقع: {e}", exc_info=True)
            time.sleep(10)


if __name__ == '__main__':
    main()
