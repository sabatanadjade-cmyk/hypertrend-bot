"""
مدير الصفقات - يتحكم في فتح وإغلاق الصفقات
منطق التقسيم:
  - 50% عند TP1 → وقف الخسارة يتحرك إلى نقطة الدخول
  - 25% عند TP2
  - 25% عند TP3 (أو يبقى مفتوح)
"""

import json
import logging
import math
import os
import time
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException

log = logging.getLogger(__name__)

TRADES_FILE = 'trades.json'


def load_trades() -> dict:
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f:
            return json.load(f)
    return {}


def save_trades(trades: dict):
    with open(TRADES_FILE, 'w') as f:
        json.dump(trades, f, indent=2, ensure_ascii=False)


class TradeManager:
    def __init__(self, client: Client):
        self.client = client
        self.trades = load_trades()

    # ──────────────────────────────────────────────────────
    # معلومات الرمز
    # ──────────────────────────────────────────────────────
    def get_symbol_info(self, symbol: str) -> dict:
        info = self.client.get_symbol_info(symbol)
        result = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 10.0}
        if not info:
            return result
        for f in info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                result['step_size']  = float(f['stepSize'])
                result['min_qty']    = float(f['minQty'])
            if f['filterType'] in ('MIN_NOTIONAL', 'NOTIONAL'):
                result['min_notional'] = float(f.get('minNotional', 10))
        return result

    def round_qty(self, qty: float, step_size: float) -> float:
        if step_size == 0:
            return qty
        precision = int(round(-math.log10(step_size)))
        return math.floor(qty / step_size) * step_size

    # ──────────────────────────────────────────────────────
    # فتح الصفقة
    # ──────────────────────────────────────────────────────
    def open_trade(self, symbol, balance, entry, stop, tp1, tp2, tp3, timeframe):
        if self.has_open_trade(symbol):
            log.warning(f"⚠️  {symbol}: صفقة مفتوحة بالفعل")
            return

        sym_info  = self.get_symbol_info(symbol)
        step_size = sym_info['step_size']
        min_qty   = sym_info['min_qty']

        # الكمية الإجمالية بكل الرصيد (مع هامش 1% للعمولات)
        total_qty = self.round_qty((balance * 0.99) / entry, step_size)

        if total_qty < min_qty:
            log.warning(f"⚠️  {symbol}: الكمية {total_qty} أقل من الحد الأدنى {min_qty}")
            return

        # تقسيم الكمية
        qty_tp1 = self.round_qty(total_qty * 0.50, step_size)   # 50%
        qty_tp2 = self.round_qty(total_qty * 0.25, step_size)   # 25%
        qty_tp3 = total_qty - qty_tp1 - qty_tp2                 # الباقي 25%
        qty_tp3 = self.round_qty(qty_tp3, step_size)

        # تحقق من الحد الأدنى للقيمة
        if total_qty * entry < sym_info['min_notional']:
            log.warning(f"⚠️  {symbol}: القيمة الإجمالية أقل من الحد الأدنى")
            return

        try:
            order = self.client.order_market_buy(
                symbol=symbol,
                quantity=total_qty
            )
            log.info(f"✅ تم فتح صفقة شراء {symbol} | "
                     f"كمية: {total_qty} | سعر دخول: ~{entry:.4f}")

            # حفظ الصفقة
            trade = {
                'symbol':      symbol,
                'timeframe':   timeframe,
                'entry':       entry,
                'stop':        stop,
                'stop_moved':  False,      # هل تحرك SL إلى نقطة الدخول؟
                'tp1':         tp1,
                'tp2':         tp2,
                'tp3':         tp3,
                'total_qty':   total_qty,
                'qty_tp1':     qty_tp1,
                'qty_tp2':     qty_tp2,
                'qty_tp3':     qty_tp3,
                'tp1_hit':     False,
                'tp2_hit':     False,
                'tp3_hit':     False,
                'sl_hit':      False,
                'order_id':    order['orderId'],
                'open_time':   datetime.now().isoformat(),
            }
            self.trades[symbol] = trade
            save_trades(self.trades)

            log.info(f"📊 {symbol} تفاصيل الصفقة:")
            log.info(f"   دخول : {entry:.4f}")
            log.info(f"   SL   : {stop:.4f} ({((stop-entry)/entry*100):.2f}%)")
            log.info(f"   TP1  : {tp1:.4f} (+{((tp1-entry)/entry*100):.2f}%) → {qty_tp1} قطعة")
            log.info(f"   TP2  : {tp2:.4f} (+{((tp2-entry)/entry*100):.2f}%) → {qty_tp2} قطعة")
            log.info(f"   TP3  : {tp3:.4f} (+{((tp3-entry)/entry*100):.2f}%) → {qty_tp3} قطعة")

        except BinanceAPIException as e:
            log.error(f"❌ فشل فتح صفقة {symbol}: {e}")

    # ──────────────────────────────────────────────────────
    # مراقبة الصفقات المفتوحة
    # ──────────────────────────────────────────────────────
    def check_open_trades(self):
        if not self.trades:
            return

        for symbol, trade in list(self.trades.items()):
            try:
                self._check_trade(symbol, trade)
            except Exception as e:
                log.error(f"خطأ في مراقبة {symbol}: {e}")

    def _check_trade(self, symbol: str, trade: dict):
        # جلب السعر الحالي
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        price  = float(ticker['price'])

        entry      = trade['entry']
        stop       = trade['stop']
        stop_moved = trade['stop_moved']

        # ── فحص وقف الخسارة ──
        effective_sl = entry if stop_moved else stop
        if price <= effective_sl:
            remaining_qty = 0
            if not trade['tp1_hit']:
                remaining_qty = trade['total_qty']
            elif not trade['tp2_hit']:
                remaining_qty = trade['qty_tp2'] + trade['qty_tp3']
            elif not trade['tp3_hit']:
                remaining_qty = trade['qty_tp3']

            if remaining_qty > 0:
                sl_type = "نقطة الدخول (BE)" if stop_moved else "وقف الخسارة"
                log.warning(f"⛔ {symbol}: تم لمس {sl_type} عند {price:.4f} | بيع {remaining_qty}")
                self._sell(symbol, remaining_qty, f"SL @ {price:.4f}")
            self._close_trade(symbol)
            return

        # ── فحص TP1 (50%) ──
        if not trade['tp1_hit'] and price >= trade['tp1']:
            log.info(f"🎯 {symbol}: TP1 تحقق عند {price:.4f} | بيع 50% ({trade['qty_tp1']})")
            self._sell(symbol, trade['qty_tp1'], f"TP1 @ {price:.4f}")
            trade['tp1_hit']    = True
            trade['stop_moved'] = True   # ← وقف الخسارة ينتقل إلى نقطة الدخول
            log.info(f"🔒 {symbol}: وقف الخسارة انتقل إلى نقطة الدخول {entry:.4f}")
            save_trades(self.trades)
            return

        # ── فحص TP2 (25%) ──
        if trade['tp1_hit'] and not trade['tp2_hit'] and price >= trade['tp2']:
            log.info(f"🎯 {symbol}: TP2 تحقق عند {price:.4f} | بيع 25% ({trade['qty_tp2']})")
            self._sell(symbol, trade['qty_tp2'], f"TP2 @ {price:.4f}")
            trade['tp2_hit'] = True
            save_trades(self.trades)
            return

        # ── فحص TP3 (25% الباقية) ──
        if trade['tp2_hit'] and not trade['tp3_hit'] and price >= trade['tp3']:
            log.info(f"🎯 {symbol}: TP3 تحقق عند {price:.4f} | بيع 25% ({trade['qty_tp3']})")
            self._sell(symbol, trade['qty_tp3'], f"TP3 @ {price:.4f}")
            trade['tp3_hit'] = True
            self._close_trade(symbol)
            return

        # تقرير الحالة
        pnl_pct = (price - entry) / entry * 100
        sl_label = f"BE({entry:.4f})" if stop_moved else f"{stop:.4f}"
        log.info(f"📈 {symbol}: {price:.4f} | PnL: {pnl_pct:+.2f}% | SL: {sl_label} | "
                 f"TP1:{'✅' if trade['tp1_hit'] else '⏳'} "
                 f"TP2:{'✅' if trade['tp2_hit'] else '⏳'} "
                 f"TP3:{'✅' if trade['tp3_hit'] else '⏳'}")

    # ──────────────────────────────────────────────────────
    # أوامر البيع
    # ──────────────────────────────────────────────────────
    def _sell(self, symbol: str, qty: float, reason: str):
        sym_info  = self.get_symbol_info(symbol)
        qty       = self.round_qty(qty, sym_info['step_size'])
        if qty <= 0:
            return
        try:
            order = self.client.order_market_sell(symbol=symbol, quantity=qty)
            log.info(f"💸 {symbol}: تم البيع {qty} | السبب: {reason}")
        except BinanceAPIException as e:
            log.error(f"❌ فشل البيع {symbol}: {e}")

    def _close_trade(self, symbol: str):
        if symbol in self.trades:
            del self.trades[symbol]
            save_trades(self.trades)
            log.info(f"🔒 {symbol}: تم إغلاق الصفقة نهائياً")

    def has_open_trade(self, symbol: str) -> bool:
        return symbol in self.trades
