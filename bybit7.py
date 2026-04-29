"""
FinalSniperBotV6 - 클로드 3차 검수 최종 수정판
══════════════════════════════════════════════════
[이전 버전 반영 확인]
  ✅ Bug1: continue → processed_be 플래그 교체 (XAUUSDT 스킵 방지)
  ✅ Bug2: start_price 재시작 복원 (get_executions 기반)
  ✅ Bug3: 헷지 종료 qty float 변환 + round 처리
  ✅ Bug4: sleep(3) 후 포지션 재조회 반영
  ✅ Improvement: calc_step 여유치 0.95 상향

[이번 버전 신규 수정]
  🔴 Fix-B: start_price 복원 시 이전 거래 오염 방지
            반대 방향 체결(익절) 발견 즉시 탐색 중단으로
            현재 포지션 체결가만 정확히 필터링

[확인 사항]
  ℹ️  헷지 주문: Bybit V5는 orderType="Market" + triggerPrice 조합이
      조건부 스탑 주문으로 처리됨 (별도 "StopMarket" 타입 없음) → 원본 유지
"""

print("Step 1: 프로그램 로딩 시작...")
import time
import logging
import numpy as np
import pandas as pd
import requests
print("Step 2: 라이브러리 로드 완료")
from pybit.unified_trading import HTTP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [1] 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API_KEY = "O1QpHJH4wdsjPiCe1x"
API_SECRET = "XdHD6eGaNz1iSnLF0j6nVEETZMDiSvGnai75"
TELEGRAM_TOKEN = "8750597756:AAHRCFpsftkHRZErKh10G1xW8PmawwXieGQ"
CHAT_ID = "6931693139"

session = HTTP(
    testnet=False,
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

CONFIGS = {
    "BTCUSDT": {"intervals": [1200, 1200, 1200, 1600, 1600, 2000], "tick_size": 1, "qty_step": 3, "min_unit": 0.01},
    "XAUUSDT": {"intervals": [60,   60,   60,   90,   90,   120],  "tick_size": 2, "qty_step": 2, "min_unit": 0.2},
}

MAX_STEP          = 7
FEE_RATE          = 0.0007   
REENTRY_COOLDOWN  = 300       
SUPPLY_ZONE_MARGIN = 0.003   
HEDGE_START_STEP  = 5        

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler("user2_bybit7.log"), logging.StreamHandler()] 
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [2] 유틸리티 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_msg(text):
    if not TELEGRAM_TOKEN or not CHAT_ID: return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
        return True
    except: return False

def get_total_balance():
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        return f"{round(float(resp['result']['list'][0]['totalEquity']), 2)} USDT"
    except: return "조회불가"

def get_supply_zones(symbol: str, n_zones: int = 10) -> list[float]:
    try:
        kline = session.get_kline(category="linear", symbol=symbol, interval="60", limit=720)
        df = pd.DataFrame(kline['result']['list'], columns=['ts', 'o', 'h', 'l', 'c', 'v', 't']).apply(pd.to_numeric)
        pivots = sorted(list(df['h']) + list(df['l']))
        clusters = []
        for p in pivots:
            merged = False
            for cluster in clusters:
                if abs(p - np.mean(cluster)) / np.mean(cluster) <= SUPPLY_ZONE_MARGIN:
                    cluster.append(p); merged = True; break
            if not merged: clusters.append([p])
        clusters.sort(key=lambda c: -len(c))
        return sorted([round(np.mean(c), 2) for c in clusters[:n_zones]])
    except: return []

def is_near_supply_zone(price: float, zones: list[float]) -> bool:
    if not zones: return True
    for zone in zones:
        if abs(price - zone) / zone <= SUPPLY_ZONE_MARGIN: return True
    return False

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [3] 메인 봇 클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FinalSniperBotV6:
    def __init__(self):
        self.states = {
            s: {
                "active":          False,
                "side":            None,
                "detected_qty":    0,       
                "last_size":       0,
                "last_tp_time":    0,
                "last_entry_price":0,
                "last_step":       1,
                "entry_price":     0,       
                "reentry_pending": False,
                "lock":            False,   
                "breakeven_done":  False,
                "start_price":     0 
            }
            for s in CONFIGS
        }

    def get_rsi(self, symbol: str) -> float:
        try:
            kline = session.get_kline(category="linear", symbol=symbol, interval="15", limit=100)
            df = pd.DataFrame(kline['result']['list'], columns=['ts','o','h','l','c','v','t']).apply(pd.to_numeric)
            df = df.sort_values(by='ts', ascending=True)
            delta = df['c'].diff()
            gain = delta.where(delta > 0, 0); loss = -delta.where(delta < 0, 0)
            avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rs = avg_gain / (avg_loss + 1e-9)
            return float((100 - (100 / (1 + rs))).iloc[-1])
        except: return 50.0

    def get_market_price(self, symbol: str) -> float:
        try:
            resp = session.get_tickers(category="linear", symbol=symbol)
            return float(resp['result']['list'][0]['lastPrice'])
        except: return 0.0

    @staticmethod
    def calc_step(cur_sz: float, base_qty: float) -> int:
        if base_qty <= 0: return 1
        ratio = cur_sz / base_qty
        thresholds = [1, 2, 4, 8, 16, 32, 64]
        step = 1
        for i, t in enumerate(thresholds):
            if ratio >= t * 0.95: # ★개선: 여유치를 0.95로 상향하여 오더 중복 방지
                step = i + 1
        return min(step, MAX_STEP)

    def place_nets_and_exit(self, symbol, avg_price, current_sz, side, current_step, is_increase, unrealised_pnl) -> bool:
        conf  = CONFIGS[symbol]; state = self.states[symbol]
        p_idx = 1 if side == "Buy" else 2; opp_p_idx = 2 if side == "Buy" else 1
        opp_side = "Sell" if side == "Buy" else "Buy"
        hedge_trig_dir = 2 if side == "Buy" else 1

        try:
            session.cancel_all_orders(category="linear", symbol=symbol)
            time.sleep(1.5)

            if current_step >= 2 and unrealised_pnl >= 0 and not state["breakeven_done"] and not is_increase:
                half_qty = round(current_sz * 0.5, conf["qty_step"])
                if half_qty > 0:
                    session.place_order(category="linear", symbol=symbol, side=opp_side, orderType="Limit",
                                        qty=str(half_qty), price=str(round(avg_price, conf["tick_size"])),
                                        reduceOnly=True, positionIdx=p_idx)
                    state["breakeven_done"] = True
                    logging.info(f"📉 [{symbol}] 본전 50% 덜어내기 배치")

            for step in range(2, MAX_STEP + 1):
                if step <= current_step: continue
                end_idx = min(step - 1, len(conf["intervals"]))
                cumulative_gap = sum(conf["intervals"][:end_idx])

                target_p = round(state["start_price"] - cumulative_gap if side == "Buy" 
                                 else state["start_price"] + cumulative_gap, conf["tick_size"])

                l_qty = round(state["detected_qty"] * (2 ** (step - 2)), conf["qty_step"])
                if l_qty <= 0: continue

                session.place_order(category="linear", symbol=symbol, side=side, orderType="Limit",
                                    qty=str(l_qty), price=str(target_p), timeInForce="PostOnly", positionIdx=p_idx)

                if step >= HEDGE_START_STEP:
                    hedge_qty = round(l_qty * 0.5, conf["qty_step"])
                    if hedge_qty > 0:
                        # Bybit V5 규격: orderType="Market" + triggerPrice 조합이
                        # 조건부 스탑 주문으로 처리됨. "StopMarket"은 잘못된 타입명.
                        session.place_order(category="linear", symbol=symbol, side=opp_side, orderType="Market",
                                            qty=str(hedge_qty), triggerPrice=str(target_p), triggerBy="LastPrice",
                                            triggerDirection=hedge_trig_dir, positionIdx=opp_p_idx)
            return True
        except Exception as e:
            logging.error(f"[{symbol}] 오더 배치 오류: {e}"); return False

    def do_take_profit(self, symbol: str, side: str, cur_sz: float):
        conf = CONFIGS[symbol]; state = self.states[symbol]
        p_idx = 1 if side == "Buy" else 2; opp_p_idx = 2 if side == "Buy" else 1
        opp_side = "Sell" if side == "Buy" else "Buy"
        try:
            session.cancel_all_orders(category="linear", symbol=symbol)
            # ① 본 포지션 종료
            session.place_order(category="linear", symbol=symbol, side=opp_side, orderType="Market",
                                qty=str(round(cur_sz, conf["qty_step"])), reduceOnly=True, positionIdx=p_idx)
            
            # ② ★수정: 헷지 포지션 종료 시 수량 round 처리 (Bug 3 반영)
            try:
                opp_pos = session.get_positions(category="linear", symbol=symbol)["result"]["list"]
                for p in opp_pos:
                    if int(p["positionIdx"]) == opp_p_idx and float(p["size"]) > 0:
                        h_qty = str(round(float(p["size"]), conf["qty_step"]))
                        session.place_order(category="linear", symbol=symbol, side=side, orderType="Market",
                                            qty=h_qty, reduceOnly=True, positionIdx=opp_p_idx)
                        logging.info(f"🛡️ [{symbol}] 헷지 포지션 종료: {h_qty}")
            except: pass

            state.update({"active":False,"breakeven_done":False,"reentry_pending":False,"last_tp_time":time.time(),"last_entry_price":0,"last_size":0,"last_step":1,"start_price":0})
            send_msg(f"💰 [{symbol}] 익절 완료 ({get_total_balance()})")
        except Exception as e: logging.error(f"익절 오류: {e}")

    def run(self):
        print("Step 3: 접속 완료"); print(f"Step 4: 잔액: {get_total_balance()}")
        supply_zones_cache = {s: [] for s in CONFIGS}; supply_zones_updated = {s: 0 for s in CONFIGS}

        while True:
            try:
                pos_res = session.get_positions(category="linear", settleCoin="USDT")["result"]["list"]
                for symbol in CONFIGS:
                    state = self.states[symbol]; conf = CONFIGS[symbol]

                    if time.time() - supply_zones_updated[symbol] > 1800:
                        supply_zones_cache[symbol] = get_supply_zones(symbol); supply_zones_updated[symbol] = time.time()

                    long_p  = next((p for p in pos_res if p["symbol"] == symbol and p["side"] == "Buy"  and float(p["size"]) > 0), None)
                    short_p = next((p for p in pos_res if p["symbol"] == symbol and p["side"] == "Sell" and float(p["size"]) > 0), None)
                    main_p = (long_p if state["side"] == "Buy" else short_p) if state["active"] else (long_p or short_p)

                    if not main_p:
                        if state["active"]:
                            state.update({"active":False,"breakeven_done":False,"reentry_pending":False,"last_tp_time":time.time(),"last_size":0,"last_step":1,"start_price":0})
                        if (time.time() - state["last_tp_time"]) > REENTRY_COOLDOWN and not state["reentry_pending"]:
                            rsi = self.get_rsi(symbol)
                            side_order = "Buy" if rsi <= 35 else "Sell" if rsi >= 65 else None
                            if side_order:
                                qty = conf["min_unit"]
                                session.place_order(category="linear", symbol=symbol, side=side_order, orderType="Market",
                                                    qty=str(round(qty, conf["qty_step"])), positionIdx=1 if side_order == "Buy" else 2)
                                state.update({"reentry_pending":True, "side":side_order})
                                logging.info(f"🎯 [{symbol}] {side_order} 진입 (RSI: {round(rsi,1)})")
                    else:
                        cur_sz, cur_av = float(main_p["size"]), float(main_p["avgPrice"])
                        unr_pnl = float(main_p.get("unrealisedPnl", 0))

                        if not state["active"] or abs(cur_sz - state["last_size"]) > 1e-6:
                            is_inc = cur_sz > state["last_size"]; time.sleep(3)
                            
                            # ★수정: 데이터 안정화 후 포지션 재조회 (Bug 4 반영)
                            ref_res = session.get_positions(category="linear", symbol=symbol)["result"]["list"]
                            main_p = next((p for p in ref_res if p["side"] == main_p["side"]), main_p)
                            cur_sz, cur_av = float(main_p["size"]), float(main_p["avgPrice"])
                            unr_pnl = float(main_p.get("unrealisedPnl", 0))

                            if not state["active"]:
                                # ★Fix-B: start_price 이전 거래 오염 방지
                                # 단순 max/min(limit=50)은 이전 익절 거래의 체결가를 포함하여
                                # start_price가 잘못된 가격으로 설정될 수 있음.
                                # 반대 방향 체결(익절/청산)이 나오면 그 이전은 이전 포지션의 것이므로
                                # 탐색을 즉시 중단하고 현재 포지션 체결가만 사용.
                                try:
                                    trades = session.get_executions(category="linear", symbol=symbol, limit=100)["result"]["list"]
                                    # API는 최신순 반환 → 오래된 순으로 정렬하여 시간 순 탐색
                                    trades_sorted = sorted(trades, key=lambda t: int(t["execTime"]), reverse=True)
                                    cur_side  = main_p["side"]
                                    opp_side_str = "Sell" if cur_side == "Buy" else "Buy"
                                    exec_p = []
                                    for t in trades_sorted:
                                        if t["side"] == opp_side_str:
                                            break  # 반대 방향 체결 = 직전 포지션 청산 지점 → 탐색 중단
                                        if t["side"] == cur_side:
                                            exec_p.append(float(t["execPrice"]))
                                    state["start_price"] = (max(exec_p) if cur_side == "Buy" else min(exec_p)) if exec_p else cur_av
                                except:
                                    state["start_price"] = cur_av
                                
                                state.update({"active":True, "side":main_p["side"], "detected_qty":conf["min_unit"], "reentry_pending":False, "breakeven_done":False})
                                logging.info(f"🚀 [{symbol}] {state['side']} 전략 시작 (기준가={state['start_price']})")
                                is_inc = True

                            current_step = self.calc_step(cur_sz, state["detected_qty"])
                            state.update({"last_size":cur_sz, "entry_price":cur_av, "last_step":current_step})
                            if is_inc: state["breakeven_done"] = False
                            self.place_nets_and_exit(symbol, cur_av, cur_sz, state["side"], current_step, is_inc, unr_pnl)

                        # ★수정: continue 삭제 및 처리 플래그 도입 (Bug 1 반영)
                        processed_be = False
                        if state["last_step"] >= 2 and not state["breakeven_done"] and unr_pnl >= 0 and not state["lock"]:
                            state["lock"] = True
                            try: 
                                self.place_nets_and_exit(symbol, cur_av, cur_sz, state["side"], state["last_step"], False, unr_pnl)
                                processed_be = True
                            finally: state["lock"] = False

                        if not processed_be: # 본전 덜어내기 직후면 이번 사이클 익절 스킵
                            round_trip_fee = cur_sz * cur_av * FEE_RATE * 2
                            net_pnl = unr_pnl - round_trip_fee
                            multiplier = 5 if state["last_step"] <= 4 else 3
                            if net_pnl >= (round_trip_fee * multiplier) and not state["lock"]:
                                state["lock"] = True
                                try: self.do_take_profit(symbol, state["side"], cur_sz)
                                finally: state["lock"] = False
                time.sleep(5)
            except Exception as e: logging.error(f"루프 에러: {e}"); time.sleep(10)

if __name__ == "__main__":
    bot = FinalSniperBotV6(); bot.run()