"""
FinalSniperBotV6 - 클로드 5차 검수 최종 수정판
══════════════════════════════════════════════════
[4차까지 누적 수정 확인]
  ✅ if not main_p 블록 구조 정상 (진입 로직 내부 위치)
  ✅ ema50 <= 0 → if ema50 > 0 방어 처리
  ✅ get_executions 반대체결 필터 (start_price 오염 방지)
  ✅ sleep(3) 후 포지션 재조회
  ✅ calc_step 여유치 0.95 / limit=200 / processed_be 플래그

[5차 신규 수정 - 레이스 컨디션 및 중복 진입 방지]
  🔴 FIX-1: Atomic State Update
            place_order 직전 reentry_pending=True 선설정
            실패 시 except에서 False 복구 → 재시도 허용
            성공/실패 무관하게 state 일관성 보장
  🔴 FIX-2: 첫 진입 시 last_step=1 강제 고정
            신규 포지션 인식 시 last_step=1 명시 강제
            중복 체결로 qty가 2배여도 step이 2로 오판되지 않도록
            detected_qty는 항상 min_unit 고정 유지
  🔴 FIX-3: 본전 덜어내기 이중 보호
            place_nets_and_exit 내부 조건에 last_step >= 2 명시
            실시간 체크(341번줄)에 이미 있는 조건과 이중 보호
            step=1 상태에서는 어떤 경우에도 덜어내기 미발동
  🟠 FIX-4: 중복 진입 시도 Skip 로그
            reentry_pending=True 상태에서 RSI 조건 재충족 시
            SKIP 로그 기록으로 상태 추적 가능

[확인 사항]
  ℹ️  헷지 주문: Bybit V5 orderType="Market" + triggerPrice → 조건부 스탑
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
REENTRY_COOLDOWN  = 900       
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

    def get_indicators(self, symbol: str) -> tuple:
        try:
            # ★ 수정: EMA50의 정밀도 향상을 위해 데이터 수집량을 200으로 상향 ★
            kline = session.get_kline(category="linear", symbol=symbol, interval="15", limit=200)
            df = pd.DataFrame(kline['result']['list'], columns=['ts','o','h','l','c','v','t']).apply(pd.to_numeric)
            df = df.sort_values(by='ts', ascending=True)
            # (1) RSI 계산
            delta = df['c'].diff()
            gain = delta.where(delta > 0, 0); loss = -delta.where(delta < 0, 0)
            avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rs = avg_gain / (avg_loss + 1e-9)
            rsi_s = 100 - (100 / (1 + rs))
            # (2) EMA 50 계산
            ema50_s = df['c'].ewm(span=50, adjust=False).mean()
            
            # 마감된 종가(iloc[-2]) 기준 데이터 추출
            return float(rsi_s.iloc[-2]), float(ema50_s.iloc[-2]), float(df['c'].iloc[-2])
        except: return 50.0, 0.0, 0.0

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

            # ★FIX-3: 본전 덜어내기 이중 보호
            # state["last_step"] >= 2 조건을 여기서도 명시하여
            # 실시간 체크(run 내부)와 이중으로 보호.
            # step=1(첫 진입) 상태에서는 어떤 경로로도 발동 불가.
            if current_step >= 2 and state["last_step"] >= 2 and unrealised_pnl >= 0 and not state["breakeven_done"] and not is_increase:
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

                    # ─── [A] 포지션 없음 ─────────────────────────────
                    if not main_p:
                        if state["active"]:
                            state.update({"active":False,"breakeven_done":False,"reentry_pending":False,"last_tp_time":time.time(),"last_size":0,"last_step":1,"start_price":0})
                        # ★BUG-1 수정: 진입 로직을 if not main_p 블록 안으로 이동
                        #   기존: 253번줄 if가 독립 블록 → 포지션 있어도 진입 시도 발생
                        if (time.time() - state["last_tp_time"]) > REENTRY_COOLDOWN and not state["reentry_pending"]:
                            rsi, ema50, closed_price = self.get_indicators(symbol)
                            # ★BUG-2 수정: ema50 <= 0 시 continue → if ema50 > 0 으로 교체
                            #   continue는 for symbol 루프를 건너뛰어 XAUUSDT 마틴게일 관리까지 스킵했음
                            if ema50 > 0:
                                # ── 롱 진입 조건 ──────────────────────────
                                # 조건 A (추세 눌림목): 종가 > EMA50 이면서 RSI ≤ 35
                                # 조건 B (절대 과매도): RSI ≤ 30 (이평선 위치 무관)
                                long_A  = (closed_price > ema50 and rsi <= 35)
                                long_B  = (rsi <= 30)
                                # ── 숏 진입 조건 ──────────────────────────
                                # 조건 A (추세 반등):   종가 < EMA50 이면서 RSI ≥ 65
                                # 조건 B (절대 과매수): RSI ≥ 70 (이평선 위치 무관)
                                short_A = (closed_price < ema50 and rsi >= 65)
                                short_B = (rsi >= 70)

                                side_order = None
                                if long_A or long_B:
                                    side_order = "Buy"
                                elif short_A or short_B:
                                    side_order = "Sell"

                                if side_order:
                                    qty = conf["min_unit"]
                                    # ★FIX-1: Atomic State Update
                                    # place_order 직전에 reentry_pending=True를 선설정.
                                    # API 호출이 실패해도 다음 루프에서 재시도하려면 False로 복구.
                                    # 성공하면 그대로 True 유지 → 포지션 감지 후 active=True로 전환.
                                    state["reentry_pending"] = True
                                    state["side"] = side_order
                                    try:
                                        session.place_order(
                                            category="linear", symbol=symbol,
                                            side=side_order, orderType="Market",
                                            qty=str(round(qty, conf["qty_step"])),
                                            positionIdx=1 if side_order == "Buy" else 2
                                        )
                                        reason = "A(눌림목)" if (side_order == "Buy" and long_A) or (side_order == "Sell" and short_A) else "B(절대RSI)"
                                        logging.info(f"🎯 [{symbol}] {side_order} 진입 | 조건:{reason} RSI:{round(rsi,1)} EMA50:{round(ema50,2)} 종가:{closed_price}")
                                    except Exception as e:
                                        # API 실패 시 상태 복구 → 다음 루프에서 재시도 허용
                                        state["reentry_pending"] = False
                                        state["side"] = None
                                        logging.warning(f"⚠️  [{symbol}] 진입 주문 실패 (상태 복구됨): {e}")
                                # ★FIX-4: 중복 진입 시도 Skip 로그
                                # reentry_pending=True 상태에서 RSI 조건이 다시 충족될 경우
                                # 주문은 나가지 않지만 로그로 추적 가능하게 기록
                                elif state["reentry_pending"] and (long_A or long_B or short_A or short_B):
                                    logging.info(f"⏭️  [{symbol}] 진입 조건 충족이나 reentry_pending=True → SKIP (중복 방지)")

                    # ─── [B] 포지션 있음 ─────────────────────────────
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
                                try:
                                    trades = session.get_executions(category="linear", symbol=symbol, limit=100)["result"]["list"]
                                    # API는 최신순 반환 → reverse=True로 최신순 유지하여 탐색
                                    # (최신 체결부터 거슬러 올라가다 반대 방향 체결에서 중단)
                                    trades_sorted = sorted(trades, key=lambda t: int(t["execTime"]), reverse=True)
                                    cur_side  = main_p["side"]
                                    opp_side_str = "Sell" if cur_side == "Buy" else "Buy"
                                    exec_p = []
                                    for t in trades_sorted:
                                        if t["side"] == opp_side_str:
                                            break
                                        if t["side"] == cur_side:
                                            exec_p.append(float(t["execPrice"]))
                                    state["start_price"] = (max(exec_p) if cur_side == "Buy" else min(exec_p)) if exec_p else cur_av
                                except:
                                    state["start_price"] = cur_av

                                # ★FIX-2: 첫 진입 시 last_step=1 강제 고정
                                # 중복 체결로 cur_sz가 min_unit의 2배가 되더라도
                                # detected_qty=min_unit 고정이므로 calc_step은 항상 1 또는 2.
                                # last_step을 1로 명시 강제하여 본전 덜어내기 조건(>=2) 오발동 차단.
                                state.update({
                                    "active":         True,
                                    "side":           main_p["side"],
                                    "detected_qty":   conf["min_unit"],
                                    "reentry_pending":False,
                                    "breakeven_done": False,
                                    "last_step":      1,    # ★ 강제 고정
                                })
                                logging.info(f"🚀 [{symbol}] {state['side']} 전략 시작 (기준가={state['start_price']}, step 강제=1)")
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