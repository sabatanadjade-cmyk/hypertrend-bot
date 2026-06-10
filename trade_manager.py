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
    def __init__(self, client: Client, testnet: bool = False):
        self.client  = client
        self.testnet = testnet
        self.trades  = load_trades()

    def get_symbol_info(self, symbol: str) -> dict:
        info   = self.client.get_symbol_info(symbol)
        result = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 10.0}
        if not info:
            return result
        for f in info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                result['step_size'] = float(f['stepSize'])
                result['min_qty']   = float(f['minQty'])
            if f['filterType'] in ('MIN_NOTIONAL', 'NOTIONAL'):
                result['min_notional'] = float(f.get('minNotional', 10))
        return result

    def round_qty(self, qty: float, step_size: float) -> float:
        if step_size == 0:
            return qty
        precision = int(round(-math.log10(step_size)))
        return math.floor(qty / step_size) * step_size

    def execute_trade(self, signal: dict):
        symbol    = signal.get('symbol')
        direction = signal.get('direction', 'BUY')
        entry     = signal.get('entry')
        stop      = signal.get('stop')
        tp1       = signal.get('tp1')
        tp2       = signal.get('tp2')
        tp3       = signal.get('tp3')
        timeframe = signal.get('timeframe', '15m')

        if not all([symbol, entry, stop, tp1]):
            log.warning(f"⚠️ إشارة ناقصة: {signal}")
            return None

        try:
            balance_info = self.client.get_asset_balance(asset='USDT')
            balance = float(balance_info['free']) if balance_info else 100.0
        except Exception:
            balance = 100.0

        if balance < 10:
            log.warning(f"⚠️ رصيد غير كافٍ: {balance} USDT")
            return None

        tp2 = tp2 or (entry + (tp1 - entry) * 2)
        tp3 = tp3 or (entry + (tp1 - entry) * 3)

        self.open_trade(symbol, balance, entry, stop, tp1, tp2, tp3, timeframe)
        return f"دخل {symbol} عند {entry}"

    def manage_open_trades(self):
        self.check_open_trades()

    def open_trade(self, symbol, balance, entry, stop, tp1, tp2, tp3, timeframe):
        if self.has_open_trade(symbol):
            log.warning(f"⚠️  {symbol}: صفقة مفتوحة بالفعل")
            return

        sym_info  = self.get_symbol_info(symbol)
        step_size = sym_info['step_size']
        min_qty   = sym_info['min_qty']

        total_qty = self.round_qty((balance * 0.99) / entry, step_size)

        if total_qty < min_qty:
            log.warning(f"⚠️  {symbol}: الكمية {total_qty} أقل من الحد الأدنى {min_qty}")
            return

        qty_tp1 = self.round_qty(total_qty * 0.50, step_size)
        qty_tp2 = self.round_qty(total_qty * 0.25, step_size)
        qty_tp3 = self.round_qty(total_qty - qty_tp1 - qty_tp2, step_size)

        if total_qty * entry < sym_info['min_notional']:
            log.warning(f"⚠️  {symbol}: القيمة أقل من الحد الأدنى")
            return

        try:
            order = self.client.order_market_buy(symbol=symbol, quantity=total_qty)
            log.info(f"✅ تم فتح صفقة {symbol} | كمية: {total_qty} | دخول: ~{entry:.4f}")

            trade = {
                'symbol':     symbol,
                'timeframe':  timeframe,
                'entry':      entry,
                'stop':       stop,
                'stop_moved': False,
                'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
                'total_qty':  total_qty,
                'qty_tp1':    qty_tp1,
                'qty_tp2':    qty_tp2,
                'qty_tp3':    qty_tp3,
                'tp1_hit':    False,
                'tp2_hit':    False,
                'tp3_hit':    False,
                'sl_hit':     False,
                'order_id':   order['orderId'],
                'open_time':  datetime.now().isoformat(),
            }
            self.trades[symbol] = trade
            save_trades(self.trades)

            log.info(f"   SL : {stop:.4f} | TP1: {tp1:.4f} | TP2: {tp2:.4f} | TP3: {tp3:.4f}")

        except BinanceAPIException as e:
            log.error(f"❌ فشل فتح صفقة {symbol}: {e}")

    def check_open_trades(self):
        for symbol, trade in list(self.trades.items()):
            try:
                self._check_trade(symbol, trade)
            except Exception as e:
                log.error(f"خطأ في مراقبة {symbol}: {e}")

    def _check_trade(self, symbol: str, trade: dict):
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        price  = float(ticker['price'])

        entry      = trade['entry']
        stop       = trade['stop']
        stop_moved = trade['stop_moved']

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
                sl_type = "نقطة الدخول" if stop_moved else "وقف الخسارة"
                log.warning(f"⛔ {symbol}: {sl_type} عند {price:.4f}")
                self._sell(symbol, remaining_qty, f"SL @ {price:.4f}")
            self._close_trade(symbol)
            return

        if not trade['tp1_hit'] and price >= trade['tp1']:
            self._sell(symbol, trade['qty_tp1'], f"TP1 @ {price:.4f}")
            trade['tp1_hit']    = True
            trade['stop_moved'] = True
            log.info(f"🎯 {symbol}: TP1 ✅ | SL انتقل إلى {entry:.4f}")
            save_trades(self.trades)
            return

        if trade['tp1_hit'] and not trade['tp2_hit'] and price >= trade['tp2']:
            self._sell(symbol, trade['qty_tp2'], f"TP2 @ {price:.4f}")
            trade['tp2_hit'] = True
            log.info(f"🎯 {symbol}: TP2 ✅")
            save_trades(self.trades)
            return

        if trade['tp2_hit'] and not trade['tp3_hit'] and price >= trade['tp3']:
            self._sell(symbol, trade['qty_tp3'], f"TP3 @ {price:.4f}")
            trade['tp3_hit'] = True
            log.info(f"🎯 {symbol}: TP3 ✅")
            self._close_trade(symbol)
            return

        pnl_pct  = (price - entry) / entry * 100
        sl_label = f"BE({entry:.4f})" if stop_moved else f"{stop:.4f}"
        log.info(f"📈 {symbol}: {price:.4f} | PnL: {pnl_pct:+.2f}% | SL: {sl_label}")

    def _sell(self, symbol: str, qty: float, reason: str):
        sym_info = self.get_symbol_info(symbol)
        qty      = self.round_qty(qty, sym_info['step_size'])
        if qty <= 0:
            return
        try:
            self.client.order_market_sell(symbol=symbol, quantity=qty)
            log.info(f"💸 {symbol}: بيع {qty} | {reason}")
        except BinanceAPIException as e:
            log.error(f"❌ فشل البيع {symbol}: {e}")

    def _close_trade(self, symbol: str):
        if symbol in self.trades:
            del self.trades[symbol]
            save_trades(self.trades)
            log.info(f"🔒 {symbol}: تم إغلاق الصفقة")

    def has_open_trade(self, symbol: str) -> bool:
        return symbol in self.trades
