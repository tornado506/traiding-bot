"""
FinalSniperBotV7 - 클로드 7차 검수 최종 수정판
══════════════════════════════════════════════════
[V6까지 누적 반영 확인]
  ✅ Atomic State Update (reentry_pending 선설정)
  ✅ detected_qty=cur_sz / last_step=1 강제 고정
  ✅ orderFilter Order+StopOrder 명시 취소
  ✅ if not main_p 내 진입 로직 / ema50>0 방어
  ✅ calc_step 0.95 / limit=200 / processed_be 플래그
  ✅ 중복 오더 완전 차단 3계층 방어 (ADD-1~3)

[V7 신규 수정 — start_price 로직 단순화]

  🔴 FIX: get_executions 역추적 로직 완전 제거 및 avgPrice 직접 고정
            [문제] 숏 진입 시 get_executions의 min(exec_price)를 start_price로 사용.
                   슬리피지·이전 거래 잔존 데이터 오염으로 실제 평단가(예: 77,195)보다
                   훨씬 낮은 가격을 기준가로 오판 → 2차 거미줄 오더가 잘못된 위치에 배치.
                   (실제 사례: 평단 77,195 / 설정 간격 1,200 / 실제 2차 오더 77,355 = 약 200불 차이)
            [수정] 포지션이 처음 감지되는 시점(if not state["active"])에
                   API가 반환하는 현재 포지션의 평균단가(avgPrice)를
                   state["start_price"]로 즉시 할당. 롱/숏 동일하게 적용.
            [효과] 실제 잡힌 평단가로부터 정확한 intervals 간격으로 거미줄 보장.
"""

import logging
import time
import numpy as np
import pandas as pd
import requests
from pybit.unified_trading import HTTP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [1] 로깅 설정 (중복 방지 및 파일 저장 설정)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_logger = logging.getLogger()
_logger.handlers.clear()                # 기존 핸들러 제거 (로그 중복 방지 핵심)
_logger.setLevel(logging.INFO)
_fmt = logging.Formatter('%(asctime)s - %(message)s')

# 파일 저장 설정 (내 계정 전용 파일명: bybit7.log)
_fh = logging.FileHandler("bybit7.log", encoding="utf-8")
_fh.setFormatter(_fmt)

# 터미널 실시간 출력 설정
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)

_logger.addHandler(_fh)
_logger.addHandler(_sh)

# 이제 로그를 찍어도 에러가 나지 않습니다.
logging.info("Step 1: 프로그램 로딩 시작...")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [1] 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API_KEY        = "MZxVDs6SlVHsDndKXE"
API_SECRET     = "ba3OlKPxWgdMjEVddU844DrAREqWY3MgRVVv"
TELEGRAM_TOKEN = ""  
CHAT_ID        = ""  

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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [로깅 설정] 전역 1회만 실행 — 핸들러 중복 방지
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 질문1: 핸들러 중복 방지
#   basicConfig는 이미 핸들러가 있으면 무시되지만,
#   IDE 환경이나 모듈 reload 시 핸들러가 누적될 수 있음.
#   루트 로거의 기존 핸들러를 먼저 전부 제거하고 재등록.
# 질문2: 전역 1회 설정
#   클래스 내부나 함수 안이 아닌 모듈 최상단에서 단 한 번만 실행.
# 질문4: print/logging 혼용 제거
#   Step 1~4 print()를 logging.info()로 통일.
_logger = logging.getLogger()           # 루트 로거 취득
_logger.handlers.clear()               # 기존 핸들러 전부 제거 (중복 방지)
_logger.setLevel(logging.INFO)
_fmt = logging.Formatter('%(asctime)s - %(message)s')
_fh  = logging.FileHandler("user2_bybit7.log", encoding="utf-8")
_fh.setFormatter(_fmt)
_sh  = logging.StreamHandler()
_sh.setFormatter(_fmt)
_logger.addHandler(_fh)
_logger.addHandler(_sh)


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

def is_near_supply_zone(price: float, zones: list) -> bool:
    if not zones: return True
    for zone in zones:
        if abs(price - zone) / zone <= SUPPLY_ZONE_MARGIN: return True
    return False

def cancel_and_wait(symbol: str, timeout: float = 6.0) -> bool:
    """
    ADD-2: 취소 완료 확인 후 배치 (Wait-until-Clear)
    ─────────────────────────────────────────────────
    단계 1: Order + StopOrder 취소 명령 각각 발송
    단계 2: 0.5초 간격 폴링으로 양쪽 모두 0개 확인
    단계 3: 완전 소멸 확인 후 True 반환 → 오더 배치 진행
    타임아웃(기본 6초) 초과 시 경고 로그 후 강제 진행
    """
    for order_filter in ["Order", "StopOrder"]:
        try:
            session.cancel_all_orders(
                category="linear", symbol=symbol, orderFilter=order_filter
            )
        except Exception:
            pass  # 해당 타입 주문 없을 경우 무시

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            normal = session.get_open_orders(
                category="linear", symbol=symbol,
                orderFilter="Order")["result"]["list"]
            cond = session.get_open_orders(
                category="linear", symbol=symbol,
                orderFilter="StopOrder")["result"]["list"]
            if len(normal) == 0 and len(cond) == 0:
                return True  # 완전 소멸 확인
        except Exception:
            pass

    logging.warning(f"[{symbol}] ⚠️  오더 취소 확인 타임아웃 ({timeout}초) — 강제 진행")
    return False

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [3] 메인 봇 클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FinalSniperBotV7:
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
            # ★ADD-2: cancel_and_wait으로 교체
            # 기존 고정 1.5초 대기 → 취소 완료 폴링 확인 방식으로 변경
            # Order + StopOrder 양쪽 모두 0개 확인 후 배치 진행
            cancel_and_wait(symbol)

            # 본전 50% 덜어내기
            # ★FIX-2 보조: state["last_step"] >= 2 이중 보호
            # place_nets_and_exit 호출 시 전달된 current_step 뿐 아니라
            # state에 저장된 last_step도 함께 확인 → step=1 시 절대 미발동
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
            # ★ADD-2 동일 적용: 익절 시에도 cancel_and_wait으로 완전 취소 확인
            cancel_and_wait(symbol)
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
        logging.info("Step 3: 바이비트 데모 접속 완료")
        logging.info(f"Step 4: 잔액: {get_total_balance()}")

        # ★ADD-1: 봇 시작 시 전체 오더 정리
        # 재시작 후 거래소에 남아있는 잔존 오더가 새 오더와 중복되는 문제 방지
        # Order + StopOrder 모두 완전 취소 확인 후 메인 루프 진입
        logging.info("🧹 봇 시작: 모든 종목 잔존 오더 전체 정리 중...")
        for symbol in CONFIGS:
            cancel_and_wait(symbol, timeout=8.0)
            logging.info(f"  [{symbol}] 오더 정리 완료")
        time.sleep(1)
        logging.info("✅ 오더 정리 완료 — 메인 루프 시작")

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
                                    # place_order 직전에 reentry_pending=True를 먼저 설정.
                                    # API가 타임아웃/오류로 실패해도 다음 루프에서 재진입을 막음.
                                    # 실패 시 except에서 False로 복구 → 재시도 허용.
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
                                        # 주문 실패 → 상태 복구하여 다음 루프에서 재시도 허용
                                        state["reentry_pending"] = False
                                        state["side"] = None
                                        logging.warning(f"⚠️  [{symbol}] 진입 주문 실패, 상태 복구: {e}")
                                elif state["reentry_pending"]:
                                    # ★FIX-1 보조: 이미 대기 중인데 조건이 또 충족된 경우 Skip 로그
                                    logging.debug(f"⏭️  [{symbol}] reentry_pending=True → 중복 진입 차단")

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
                                # ★V7 FIX: start_price 로직 단순화
                                # ─────────────────────────────────────────────────────
                                # [구 로직 제거] get_executions로 체결가를 역추적하던 방식 삭제.
                                #   문제: 숏 진입 시 min(exec_p)를 기준가로 잡아,
                                #         슬리피지·이전 거래 잔존 데이터 오염으로
                                #         실제 평단가(예: 77,195)보다 훨씬 낮은 가격을
                                #         start_price로 오판 → 2차 오더가 잘못된 위치에 배치됨.
                                # [신 로직] 포지션이 처음 감지되는 순간,
                                #   거래소 API가 반환하는 현재 포지션의 평균단가(avgPrice)를
                                #   start_price로 즉시 고정. 롱/숏 동일하게 적용.
                                #   → 실제 잡힌 평단가로부터 정확한 intervals 간격으로 거미줄 보장.
                                state["start_price"] = cur_av

                                # ★FIX-2: detected_qty = cur_sz (실제 첫 진입 수량 기준)
                                # 기존: detected_qty=conf[min_unit]=0.01 고정
                                #   → 중복 체결로 cur_sz=0.02면 calc_step(0.02,0.01)=2 오판
                                # 수정: detected_qty=cur_sz
                                #   → calc_step(cur_sz, cur_sz) = 1 항상 보장
                                #   → 마틴 배수 기준도 실제 첫 진입 수량 기반으로 자연스럽게 설정
                                # last_step=1 강제 고정으로 이중 보호
                                state.update({
                                    "active":          True,
                                    "side":            main_p["side"],
                                    "detected_qty":    cur_sz,   # ★ min_unit → cur_sz
                                    "reentry_pending": False,
                                    "breakeven_done":  False,
                                    "last_step":       1,        # ★ 강제 고정
                                })
                                logging.info(f"🚀 [{symbol}] {state['side']} 전략 시작 | 기준가={state['start_price']} 기준수량={cur_sz}")
                                is_inc = True

                            current_step = self.calc_step(cur_sz, state["detected_qty"])
                            state.update({"last_size":cur_sz, "entry_price":cur_av, "last_step":current_step})
                            if is_inc: state["breakeven_done"] = False

                            # ★ADD-3: size_changed 경로도 Lock으로 보호
                            # 기존: Lock 없이 호출 → 본전탈출 실시간 체크와 동시 실행 가능
                            # 수정: 모든 place_nets_and_exit 호출 경로를 Lock으로 통일
                            if not state["lock"]:
                                state["lock"] = True
                                try:
                                    self.place_nets_and_exit(symbol, cur_av, cur_sz, state["side"], current_step, is_inc, unr_pnl)
                                finally:
                                    state["lock"] = False

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
                            multiplier = 3  # <--- 5에서 3으로 수정 (전 단계 수수료 3배 익절)
                            if net_pnl >= (round_trip_fee * multiplier) and not state["lock"]:
                                state["lock"] = True
                                try: self.do_take_profit(symbol, state["side"], cur_sz)
                                finally: state["lock"] = False
                time.sleep(5)
            except Exception as e: logging.error(f"루프 에러: {e}"); time.sleep(10)

if __name__ == "__main__":
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 질문3: PID 파일로 중복 프로세스 실행 차단
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 로그가 2줄씩 찍히는 가장 흔한 원인:
    #   터미널 A에서 실행 중인데 터미널 B에서 또 실행한 경우.
    # PID 파일을 생성하여 이미 실행 중이면 즉시 종료.
    import os, sys

    PID_FILE = "bybit7_user2.pid"

    def _check_pid(pid: int) -> bool:
        """해당 PID 프로세스가 실제로 살아있는지 확인"""
        try:
            os.kill(pid, 0)   # 시그널 0 = 존재 여부만 확인
            return True
        except (OSError, ProcessLookupError):
            return False

    # 기존 PID 파일 확인
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = int(f.read().strip())
        if _check_pid(old_pid):
            logging.error(
                f"🚫 이미 실행 중인 봇이 있습니다 (PID: {old_pid}). "
                f"중복 실행 차단 — 종료합니다.\n"
                f"   강제 종료하려면: kill {old_pid}"
            )
            sys.exit(1)
        else:
            # 이전 프로세스가 비정상 종료하여 PID 파일만 남은 경우
            logging.warning(f"⚠️  PID {old_pid} 은 이미 종료됨. PID 파일 재생성.")

    # 현재 프로세스 PID 기록
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    logging.info(f"🔒 PID 파일 생성: {PID_FILE} (PID: {os.getpid()})")

    try:
        bot = FinalSniperBotV7()
        bot.run()
    finally:
        # 봇 종료 시 PID 파일 삭제 (정상/비정상 종료 모두)
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
            logging.info("🔓 PID 파일 삭제 완료")