"""
HyperTrend Bot - بوت التداول التلقائي
يراقب قناة تلجرام للحيتان + مؤشر HyperTrend
"""

import os
import time
import logging
import asyncio
import re
from binance.client import Client
from binance.exceptions import BinanceAPIException
from signal_engine import SignalEngine
from trade_manager import TradeManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger(__name__)

API_KEY          = os.getenv("BINANCE_API_KEY", "")
API_SECRET       = os.getenv("BINANCE_API_SECRET", "")
TESTNET          = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "@KdrxWhale")

TIMEFRAMES    = ["1m", "3m", "5m", "15m"]
MIN_CONFIRM   = 2
SCAN_INTERVAL = 30
WHALE_INTERVAL = 60

DEFAULT_SYMBOLS = [
    "SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","SUIUSDT","AVAXUSDT",
    "LINKUSDT","DOTUSDT","ARBUSDT","NEARUSDT","LTCUSDT","XLMUSDT",
    "OPUSDT","APTUSDT","ATOMUSDT","ALGOUSDT","FILUSDT","GRTUSDT",
    "TONUSDT","VETUSDT","ICPUSDT","BCHUSDT","ETCUSDT","QNTUSDT",
    "EGLDUSDT","IMXUSDT","RENDERUSDT","FETUSDT","INJUSDT","MATICUSDT"
]

def load_symbols():
    try:
        with open("symbols.txt") as f:
            syms = [s.strip() for s in f.read().splitlines() if s.strip()]
        return syms if syms else DEFAULT_SYMBOLS
    except:
        return DEFAULT_SYMBOLS

def extract_symbols_from_text(text):
    text = text.upper()
    found = []
    patterns = [
        r'\b([A-Z]{2,10})USDT\b',
        r'\b([A-Z]{2,10})/USDT\b',
        r'\$([A-Z]{2,10})\b',
        r'#([A-Z]{2,10})\b',
    ]
    for pattern in patterns:
        for m in re.findall(pattern, text):
            symbol = m + "USDT" if not m.endswith("USDT") else m
            if symbol not in found:
                found.append(symbol)
    return found

async def get_whale_signals():
    if not TELEGRAM_TOKEN:
        return []
    try:
        import aiohttp
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
        symbols = []
        if data.get("ok") and data.get("result"):
            for update in data["result"][-20:]:
                msg = update.get("message") or update.get("channel_post")
                if msg:
                    chat = msg.get("chat", {})
                    if TELEGRAM_CHANNEL.replace("@", "") in chat.get("username", ""):
                        found = extract_symbols_from_text(msg.get("text", ""))
                        symbols.extend(found)
        return list(set(symbols))
    except Exception as e:
        log.warning(f"Telegram error: {e}")
        return []

def main():
    log.info("=" * 60)
    log.info("🤖 بدأ تشغيل HyperTrend Bot")
    log.info(f"🌐 الوضع: {'TESTNET تجريبي' if TESTNET else 'REAL حقيقي'}")
    log.info(f"📡 قناة الحيتان: {TELEGRAM_CHANNEL}")
    log.info("=" * 60)

    try:
        if TESTNET:
            client = Client(API_KEY, API_SECRET, testnet=True)
            client.API_URL = "https://testnet.binance.vision/api"
            log.info("✅ متصل بـ Binance Testnet")
        else:
            client = Client(API_KEY, API_SECRET)
            log.info("✅ متصل بـ Binance الحقيقي")
        client.get_account()
        log.info("✅ حساب Binance يعمل")
    except Exception as e:
        log.error(f"❌ خطأ الاتصال: {e}")
        time.sleep(30)
        return

    symbols = load_symbols()
    log.info(f"📊 عدد العملات: {len(symbols)}")

    engine  = SignalEngine(client, TIMEFRAMES, MIN_CONFIRM)
    manager = TradeManager(client, testnet=TESTNET)

    whale_symbols    = []
    last_whale_check = 0
    scan_count       = 0

    while True:
        try:
            scan_count += 1
            now = time.time()

            if now - last_whale_check > WHALE_INTERVAL:
                log.info("🐋 فحص قناة الحيتان...")
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    whale_symbols = loop.run_until_complete(get_whale_signals())
                    loop.close()
                    if whale_symbols:
                        log.info(f"🐋 عملات الحيتان: {whale_symbols}")
                except Exception as e:
                    log.warning(f"خطأ الحيتان: {e}")
                last_whale_check = now

            scan_symbols = whale_symbols + [s for s in symbols if s not in whale_symbols]
            log.info(f"🔍 فحص #{scan_count} — {len(scan_symbols)} عملة")

            best_signal = None
            best_score  = 0

            for symbol in scan_symbols[:50]:
                try:
                    signal = engine.analyze(symbol)
                    if signal and signal.get("score", 0) > best_score:
                        best_score  = signal["score"]
                        best_signal = signal
                        best_signal["symbol"] = symbol
                        best_signal["whale"]  = symbol in whale_symbols
                except:
                    pass

            if best_signal and best_score >= MIN_CONFIRM:
                sym = best_signal["symbol"]
                tag = "🐋 حوت + " if best_signal.get("whale") else ""
                log.info(f"✅ إشارة {tag}{best_signal['direction']} على {sym} (نقاط: {best_score})")
                try:
                    result = manager.execute_trade(best_signal)
                    if result:
                        log.info(f"📈 تم الدخول: {sym} | {result}")
                except Exception as e:
                    log.warning(f"خطأ التداول: {e}")
            else:
                log.info("⏳ لا توجد إشارة كافية الآن")

            try:
                manager.manage_open_trades()
            except Exception as e:
                log.warning(f"خطأ إدارة الصفقات: {e}")

            log.info(f"⏰ انتظار {SCAN_INTERVAL} ثانية...")
            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            log.info("🛑 تم إيقاف البوت")
            break
        except Exception as e:
            log.error(f"❌ خطأ: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
