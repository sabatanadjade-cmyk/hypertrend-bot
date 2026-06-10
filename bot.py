"""
HyperTrend Bot - بوت التداول التلقائي
يراقب قناة تلجرام للحيتان + مؤشر HyperTrend
مع إشعارات Telegram لكل صفقة
"""

import os
import time
import logging
import asyncio
import re
import requests
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
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5569601833")

TIMEFRAMES     = ["1m", "3m", "5m", "15m"]
MIN_CONFIRM    = 2
SCAN_INTERVAL  = 30
WHALE_INTERVAL = 60

DEFAULT_SYMBOLS = [
    "SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","SUIUSDT","AVAXUSDT",
    "LINKUSDT","DOTUSDT","ARBUSDT","NEARUSDT","LTCUSDT","XLMUSDT",
    "OPUSDT","APTUSDT","ATOMUSDT","ALGOUSDT","FILUSDT","GRTUSDT",
    "TONUSDT","VETUSDT","ICPUSDT","BCHUSDT","ETCUSDT","QNTUSDT",
    "EGLDUSDT","IMXUSDT","RENDERUSDT","FETUSDT","INJUSDT","MATICUSDT"
]

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        log.warning(f"خطأ إرسال Telegram: {e}")

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

    send_telegram(
        f"🤖 <b>HyperTrend Bot بدأ التشغيل</b>\n"
        f"🌐 الوضع: {'TESTNET تجريبي' if TESTNET else 'REAL حقيقي'}\n"
        f"📡 قناة الحيتان: {TELEGRAM_CHANNEL}\n"
        f"⏰ كل 30 ثانية يفحص 221 عملة"
    )

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
        send_telegram(f"❌ خطأ الاتصال بـ Binance: {e}")
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
                        send_telegram(f"🐋 <b>عملات الحيتان:</b> {', '.join(whale_symbols)}")
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
                sym   = best_signal["symbol"]
                tag   = "🐋 حوت + " if best_signal.get("whale") else ""
                entry = best_signal.get("entry", 0)
                stop  = best_signal.get("stop", 0)
                tp1   = best_signal.get("tp1", 0)
                tp2   = best_signal.get("tp2", 0)
                tp3   = best_signal.get("tp3", 0)

                log.info(f"✅ إشارة {tag}{best_signal['direction']} على {sym} (نقاط: {best_score})")

                send_telegram(
                    f"✅ <b>إشارة {tag}{best_signal['direction']}</b>\n"
                    f"💎 العملة: <b>{sym}</b>\n"
                    f"📥 دخول: <b>{entry:.4f}</b>\n"
                    f"🛑 وقف الخسارة: <b>{stop:.4f}</b>\n"
                    f"🎯 TP1: <b>{tp1:.4f}</b>\n"
                    f"🎯 TP2: <b>{tp2:.4f}</b>\n"
                    f"🎯 TP3: <b>{tp3:.4f}</b>\n"
                    f"⭐ نقاط: {best_score}"
                )

                try:
                    result = manager.execute_trade(best_signal)
                    if result:
                        log.info(f"📈 تم الدخول: {sym} | {result}")
                        send_telegram(f"📈 <b>تم الدخول في {sym}</b>\n{result}")
                except Exception as e:
                    log.warning(f"خطأ التداول: {e}")
                    send_telegram(f"⚠️ خطأ التداول {sym}: {e}")
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
            send_telegram("🛑 تم إيقاف البوت")
            break
        except Exception as e:
            log.error(f"❌ خطأ: {e}")
            send_telegram(f"❌ خطأ في البوت: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
