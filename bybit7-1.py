"""
FinalSniperBotV7 - 클로드 8차 검수 최종 마스터판
══════════════════════════════════════════════════
[V7까지 누적 반영 확인]
  ✅ Atomic State Update (reentry_pending 선설정)
  ✅ detected_qty=cur_sz / last_step=1 강제 고정
  ✅ orderFilter Order+StopOrder 명시 취소
  ✅ if not main_p 내 진입 로직 / ema50>0 방어
  ✅ calc_step 0.95 / limit=200 / processed_be 플래그
  ✅ 중복 오더 완전 차단 3계층 방어 (ADD-1~3)
  ✅ start_price = avgPrice 직접 고정 (V7)

[V8 신규 수정 — 전체 버그 완전 수정]

  🔴 BUG-1 수정: 로깅 핸들러 이중 등록 완전 제거
                  36~50줄(1차)과 100~109줄(2차) 중복 블록 통합.
                  파일명 "bybit7.log" 단일화, 핸들러 1회만 등록.

  🔴 BUG-2 수정: is_near_supply_zone 실제 진입 조건에 연결
                  supply_zones_cache 계산 후 미호출 상태였던 필터를
                  진입 side_order 결정 직후에 적용.
                  롱 진입 시 공급 구역 근처이면 차단, 로그 출력.

  🔴 BUG-3 수정: unrealisedPnl / unrealizedPnl 키 철자 이중 대응
                  Bybit API 버전에 따라 철자가 다를 수 있어
                  .get() 체인으로 양쪽 키를 모두 조회.

  🟠 BUG-4 수정: 포지션 재조회 시 positionIdx 함께 검증
                  side 비교에 positionIdx(1=롱/2=숏)도 추가하여
                  헷지 모드에서 반대 포지션 혼선 방지.

  🟠 BUG-5 수정: do_take_profit 및 포지션 소멸 시 state 초기화 완전화
                  entry_price / detected_qty / side / lock 등
                  누락됐던 필드 모두 초기화. 두 경로(328/377줄) 동일 적용.

  🟠 BUG-6 수정: 본전 오더와 거미줄 오더 try/except 분리
                  본전 덜어내기 실패 시 거미줄 전체 취소되던 문제 해결.
                  각 step 오더도 개별 try/except로 감싸 부분 실패 허용.

  🟡 BUG-7 수정: get_market_price 활용 — 공급 구역 체크 시 현재가 사용
                  closed_price(15분봉 종가) 대신 실시간 시세로 공급 구역 판단.

  🟡 BUG-8 수정: thresholds를 MAX_STEP에 자동 연동
                  하드코딩 제거, MAX_STEP 변경 시 자동 반영.

  🟡 BUG-9 수정: PID 체크 Windows 호환성 보완
                  os.kill(pid, 0) 실패 시 psutil 또는 fallback 처리 추가.
"""

from datetime import datetime
import logging
import os
import sys
import time
import numpy as np
import pandas as pd
import requests
from pybit.unified_trading import HTTP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [BUG-1 수정] 로깅 핸들러 단일 등록 — 파일 상단 1회만
# 기존: 36~50줄(bybit7.log)과 100~109줄(user2_bybit7.log) 이중 등록
# 수정: 통합 1회, 파일명 bybit7.log 고정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_logger = logging.getLogger()
_logger.handlers.clear()                # 기존 핸들러 전부 제거
_logger.setLevel(logging.INFO)
_fmt = logging.Formatter('%(asctime)s - %(message)s')
_fh  = logging.FileHandler("bybit7.log", encoding="utf-8")   # 파일명 단일화
_fh.setFormatter(_fmt)
_sh  = logging.StreamHandler()
_sh.setFormatter(_fmt)
_logger.addHandler(_fh)
_logger.addHandler(_sh)

logging.info("Step 1: 프로그램 로딩 시작...")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [1] 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API_KEY        = "O1QpHJH4wdsjPiCe1x"
API_SECRET     = "XdHD6eGaNz1iSnLF0j6nVEETZMDiSvGnai75"
TELEGRAM_TOKEN = "8750597756:AAHRCFpsftkHRZErKh10G1xW8PmawwXieGQ"
CHAT_ID        = "6931693139"

session = HTTP(
    testnet=False,
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

# ── intervals: 퍼센트(%) 기반 갭 (V8 전환)
# 각 단계 사이의 개별 간격 비율. sum([:n])으로 누적 % 계산.
CONFIGS = {
    "BTCUSDT":  {"intervals": [1.5, 1.5, 1.5, 2.0, 2.5, 2.5], "tick_size": 1, "qty_step": 3, "min_unit": 0.01},
    "XAUUSDT":  {"intervals": [1.5, 1.5, 1.5, 2.0, 2.5, 2.5], "tick_size": 2, "qty_step": 2, "min_unit": 0.2},
    "ETHUSDT":  {"intervals": [1.5, 1.5, 1.5, 2.0, 2.5, 2.5], "tick_size": 2, "qty_step": 2, "min_unit": 0.2},
    "SOLUSDT":  {"intervals": [1.5, 1.5, 1.5, 2.0, 2.5, 2.5], "tick_size": 3, "qty_step": 1, "min_unit": 6},
    "XAGUSDT":  {"intervals": [1.5, 1.5, 1.5, 2.0, 2.5, 2.5], "tick_size": 3, "qty_step": 1, "min_unit": 7},
    "DOGEUSDT": {"intervals": [1.5, 1.5, 1.5, 2.0, 2.5, 2.5], "tick_size": 5, "qty_step": 0, "min_unit": 5000},
}

MAX_STEP          = 7
FEE_RATE          = 0.0007
REENTRY_COOLDOWN  = 900
SUPPLY_ZONE_MARGIN = 0.003
HEDGE_START_STEP  = 5


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [2] 유틸리티 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_msg(text: str) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
        return True
    except:
        return False


def get_total_balance() -> str:
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        return f"{round(float(resp['result']['list'][0]['totalEquity']), 2)} USDT"
    except:
        return "조회불가"


def get_supply_zones(symbol: str, n_zones: int = 10) -> list:
    try:
        kline = session.get_kline(category="linear", symbol=symbol, interval="60", limit=720)
        df = pd.DataFrame(
            kline['result']['list'],
            columns=['ts', 'o', 'h', 'l', 'c', 'v', 't']
        ).apply(pd.to_numeric)
        pivots = sorted(list(df['h']) + list(df['l']))
        clusters = []
        for p in pivots:
            merged = False
            for cluster in clusters:
                if abs(p - np.mean(cluster)) / np.mean(cluster) <= SUPPLY_ZONE_MARGIN:
                    cluster.append(p)
                    merged = True
                    break
            if not merged:
                clusters.append([p])
        clusters.sort(key=lambda c: -len(c))
        return sorted([round(np.mean(c), 2) for c in clusters[:n_zones]])
    except:
        return []


def is_near_supply_zone(price: float, zones: list) -> bool:
    """공급 구역 근접 여부 — zones가 비어있으면 True(보수적 차단)"""
    if not zones:
        return True
    for zone in zones:
        if abs(price - zone) / zone <= SUPPLY_ZONE_MARGIN:
            return True
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
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            normal = session.get_open_orders(
                category="linear", symbol=symbol, orderFilter="Order"
            )["result"]["list"]
            cond = session.get_open_orders(
                category="linear", symbol=symbol, orderFilter="StopOrder"
            )["result"]["list"]
            if len(normal) == 0 and len(cond) == 0:
                return True
        except Exception:
            pass

    logging.warning(f"[{symbol}] ⚠️  오더 취소 확인 타임아웃 ({timeout}초) — 강제 진행")
    return False


def _get_unr_pnl(position: dict) -> float:
    """
    [BUG-3 수정] Bybit API 버전에 따라 키 철자가 다름
    unrealisedPnl (영국식) / unrealizedPnl (미국식) 양쪽 대응
    """
    val = position.get("unrealisedPnl") or position.get("unrealizedPnl") or 0
    return float(val)


def _reset_state(state: dict, keep_tp_time: bool = True):
    """
    [BUG-5 수정] state 초기화 완전화
    기존: entry_price / detected_qty / side / lock 누락
    수정: 모든 필드 명시적 초기화
    """
    state.update({
        "active":          False,
        "side":            None,
        "detected_qty":    0,
        "last_size":       0,
        "last_entry_price": 0,
        "entry_price":     0,         # ★ 추가
        "last_step":       1,
        "start_price":     0,
        "breakeven_done":  False,
        "reentry_pending": False,
        "lock":            False,     # ★ 추가
        "last_tp_time":    time.time() if keep_tp_time else state.get("last_tp_time", 0),
    })


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
                "last_entry_price": 0,
                "last_step":       1,
                "entry_price":     0,
                "reentry_pending": False,
                "lock":            False,
                "breakeven_done":  False,
                "start_price":     0,
            }
            for s in CONFIGS
        }

    def get_indicators(self, symbol: str) -> tuple:
        try:
            kline = session.get_kline(category="linear", symbol=symbol, interval="15", limit=200)
            df = pd.DataFrame(
                kline['result']['list'],
                columns=['ts', 'o', 'h', 'l', 'c', 'v', 't']
            ).apply(pd.to_numeric)
            df = df.sort_values(by='ts', ascending=True)
            # RSI 계산
            delta    = df['c'].diff()
            gain     = delta.where(delta > 0, 0)
            loss     = -delta.where(delta < 0, 0)
            avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rs       = avg_gain / (avg_loss + 1e-9)
            rsi_s    = 100 - (100 / (1 + rs))
            # EMA50 계산
            ema50_s  = df['c'].ewm(span=50, adjust=False).mean()
            # 마감된 종가(iloc[-2]) 기준
            return float(rsi_s.iloc[-2]), float(ema50_s.iloc[-2]), float(df['c'].iloc[-2])
        except:
            return 50.0, 0.0, 0.0

    def get_market_price(self, symbol: str) -> float:
        """[BUG-7 활용] 실시간 현재가 조회 — 공급 구역 체크 등에 활용"""
        try:
            resp = session.get_tickers(category="linear", symbol=symbol)
            return float(resp['result']['list'][0]['lastPrice'])
        except:
            return 0.0

    @staticmethod
    def calc_step(cur_sz: float, base_qty: float) -> int:
        if base_qty <= 0:
            return 1
        ratio = cur_sz / base_qty
        # [BUG-8 수정] thresholds를 MAX_STEP에 자동 연동 (하드코딩 제거)
        thresholds = [2 ** i for i in range(MAX_STEP)]
        step = 1
        for i, t in enumerate(thresholds):
            if ratio >= t * 0.95:
                step = i + 1
        return min(step, MAX_STEP)

    def _calc_target_price(self, start_price: float, cumulative_pct: float,
                           tick_size: int, side: str) -> float:
        """
        [질문1~2 수정] 퍼센트 갭을 실제 가격으로 변환
        중간 정밀도(tick_size+2)로 부동소수점 오차 흡수 후 최종 tick_size로 반올림
        """
        gap_amt  = round(start_price * cumulative_pct / 100, tick_size + 2)
        if side == "Buy":
            raw = start_price - gap_amt
        else:
            raw = start_price + gap_amt
        return round(raw, tick_size)

    def place_nets_and_exit(self, symbol: str, avg_price: float, current_sz: float,
                            side: str, current_step: int, is_increase: bool,
                            unrealised_pnl: float) -> bool:
        conf  = CONFIGS[symbol]
        state = self.states[symbol]
        p_idx       = 1 if side == "Buy" else 2
        opp_p_idx   = 2 if side == "Buy" else 1
        opp_side    = "Sell" if side == "Buy" else "Buy"
        hedge_trig_dir = 2 if side == "Buy" else 1

        cancel_and_wait(symbol)

        # ─── 본전 50% 덜어내기 ────────────────────────────────────────
        # [BUG-6 수정] 독립 try/except — 실패해도 거미줄 오더는 계속 진행
        if (current_step >= 2 and state["last_step"] >= 2
                and unrealised_pnl >= 0
                and not state["breakeven_done"]
                and not is_increase):
            try:
                half_qty = round(current_sz * 0.5, conf["qty_step"])
                if half_qty > 0:
                    session.place_order(
                        category="linear", symbol=symbol,
                        side=opp_side, orderType="Limit",
                        qty=str(half_qty),
                        price=str(round(avg_price, conf["tick_size"])),
                        reduceOnly=True, positionIdx=p_idx
                    )
                    state["breakeven_done"] = True
                    logging.info(f"📉 [{symbol}] 본전 50% 덜어내기 배치")
            except Exception as e:
                logging.warning(f"[{symbol}] 본전 오더 실패 (거미줄 배치 계속 진행): {e}")

        # ─── 거미줄 오더 배치 ─────────────────────────────────────────
        # [BUG-6 수정] 각 step 개별 try/except — 부분 실패 허용
        success = True
        for step in range(2, MAX_STEP + 1):
            if step <= current_step:
                continue
            end_idx        = min(step - 1, len(conf["intervals"]))
            cumulative_pct = sum(conf["intervals"][:end_idx])

            # [질문1~2 수정] 퍼센트 기반 가격 계산 (부동소수점 안전 버전)
            target_p = self._calc_target_price(
                state["start_price"], cumulative_pct, conf["tick_size"], side
            )

            l_qty = round(state["detected_qty"] * (2 ** (step - 2)), conf["qty_step"])
            if l_qty <= 0:
                continue

            try:
                session.place_order(
                    category="linear", symbol=symbol,
                    side=side, orderType="Limit",
                    qty=str(l_qty), price=str(target_p),
                    timeInForce="PostOnly", positionIdx=p_idx
                )
            except Exception as e:
                logging.error(f"[{symbol}] step={step} 리밋 오더 실패: {e}")
                success = False
                continue  # 실패해도 다음 step 계속 시도

            # ─── 헷징 오더 ───────────────────────────────────────────
            if step >= HEDGE_START_STEP:
                if step >= 6:
                    hedge_qty = l_qty                              # 6회차~: 100%
                else:
                    hedge_qty = round(l_qty * 0.5, conf["qty_step"])  # 5회차: 50%

                if hedge_qty > 0:
                    try:
                        session.place_order(
                            category="linear", symbol=symbol,
                            side=opp_side, orderType="Market",
                            qty=str(hedge_qty),
                            triggerPrice=str(target_p),
                            triggerBy="LastPrice",
                            triggerDirection=hedge_trig_dir,
                            positionIdx=opp_p_idx
                        )
                    except Exception as e:
                        logging.error(f"[{symbol}] step={step} 헷지 오더 실패: {e}")
                        success = False

        return success

    def do_take_profit(self, symbol: str, side: str, cur_sz: float):
        conf      = CONFIGS[symbol]
        state     = self.states[symbol]
        p_idx     = 1 if side == "Buy" else 2
        opp_p_idx = 2 if side == "Buy" else 1
        opp_side  = "Sell" if side == "Buy" else "Buy"
        try:
            cancel_and_wait(symbol)
            # ① 본 포지션 종료
            session.place_order(
                category="linear", symbol=symbol,
                side=opp_side, orderType="Market",
                qty=str(round(cur_sz, conf["qty_step"])),
                reduceOnly=True, positionIdx=p_idx
            )
            # ② 헷지 포지션 종료
            try:
                opp_pos = session.get_positions(category="linear", symbol=symbol)["result"]["list"]
                for p in opp_pos:
                    if int(p["positionIdx"]) == opp_p_idx and float(p["size"]) > 0:
                        h_qty = str(round(float(p["size"]), conf["qty_step"]))
                        session.place_order(
                            category="linear", symbol=symbol,
                            side=side, orderType="Market",
                            qty=h_qty, reduceOnly=True, positionIdx=opp_p_idx
                        )
                        logging.info(f"🛡️ [{symbol}] 헷지 포지션 종료: {h_qty}")
            except:
                pass

            # [BUG-5 수정] 공통 헬퍼로 완전 초기화
            _reset_state(state, keep_tp_time=True)
            send_msg(f"💰 [{symbol}] 익절 완료 ({get_total_balance()})")
        except Exception as e:
            logging.error(f"익절 오류: {e}")

    def run(self):
        logging.info("Step 3: 바이비트 데모 접속 완료")
        logging.info(f"Step 4: 잔액: {get_total_balance()}")

        # ★ADD-1: 봇 시작 시 전체 오더 정리
        logging.info("🧹 봇 시작: 모든 종목 잔존 오더 전체 정리 중...")
        for symbol in CONFIGS:
            cancel_and_wait(symbol, timeout=8.0)
            logging.info(f"  [{symbol}] 오더 정리 완료")
        time.sleep(1)
        logging.info("✅ 오더 정리 완료 — 메인 루프 시작")

        supply_zones_cache   = {s: [] for s in CONFIGS}
        supply_zones_updated = {s: 0  for s in CONFIGS}

        while True:
            try:
                pos_res = session.get_positions(
                    category="linear", settleCoin="USDT"
                )["result"]["list"]

                for symbol in CONFIGS:
                    state = self.states[symbol]
                    conf  = CONFIGS[symbol]

                    # 공급 구역 30분마다 갱신
                    if time.time() - supply_zones_updated[symbol] > 1800:
                        supply_zones_cache[symbol]   = get_supply_zones(symbol)
                        supply_zones_updated[symbol] = time.time()

                    long_p  = next((p for p in pos_res
                                    if p["symbol"] == symbol and p["side"] == "Buy"
                                    and float(p["size"]) > 0), None)
                    short_p = next((p for p in pos_res
                                    if p["symbol"] == symbol and p["side"] == "Sell"
                                    and float(p["size"]) > 0), None)
                    main_p  = (
                        (long_p if state["side"] == "Buy" else short_p)
                        if state["active"]
                        else (long_p or short_p)
                    )

                    # ─── 금/은 거래 시간 제한 (KST 기준) ─────────────────
                    is_metal = "XAU" in symbol or "XAG" in symbol
                    if is_metal and not main_p:
                        now = datetime.now()
                        wd, hr = now.weekday(), now.hour
                        # 토요일 06시 ~ 월요일 07시 이전 차단
                        if (wd == 5 and hr >= 6) or (wd == 6) or (wd == 0 and hr < 7):
                            continue
                    # ───────────────────────────────────────────────────────

                    # ─── [A] 포지션 없음 ───────────────────────────────────
                    if not main_p:
                        if state["active"]:
                            # [BUG-5 수정] 공통 헬퍼로 완전 초기화
                            _reset_state(state, keep_tp_time=True)

                        if ((time.time() - state["last_tp_time"]) > REENTRY_COOLDOWN
                                and not state["reentry_pending"]):
                            rsi, ema50, closed_price = self.get_indicators(symbol)

                            if ema50 > 0:
                                long_A  = (closed_price > ema50 and rsi <= 35)
                                long_B  = (rsi <= 30)
                                short_A = (closed_price < ema50 and rsi >= 65)
                                short_B = (rsi >= 70)

                                side_order = None
                                if long_A or long_B:
                                    side_order = "Buy"
                                elif short_A or short_B:
                                    side_order = "Sell"

                                if side_order:
                                    # [BUG-2/7 수정] 실시간 시세로 공급 구역 체크
                                    current_price = self.get_market_price(symbol)
                                    if (side_order == "Buy"
                                            and is_near_supply_zone(
                                                current_price, supply_zones_cache[symbol])):
                                        logging.info(
                                            f"⛔ [{symbol}] 공급 구역 근처 롱 진입 차단: "
                                            f"{current_price}"
                                        )
                                    else:
                                        qty = conf["min_unit"]
                                        # ★FIX-1: Atomic State Update
                                        state["reentry_pending"] = True
                                        state["side"]            = side_order
                                        try:
                                            session.place_order(
                                                category="linear", symbol=symbol,
                                                side=side_order, orderType="Market",
                                                qty=str(round(qty, conf["qty_step"])),
                                                positionIdx=1 if side_order == "Buy" else 2
                                            )
                                            reason = (
                                                "A(눌림목)"
                                                if (side_order == "Buy" and long_A)
                                                   or (side_order == "Sell" and short_A)
                                                else "B(절대RSI)"
                                            )
                                            logging.info(
                                                f"🎯 [{symbol}] {side_order} 진입 | "
                                                f"조건:{reason} RSI:{round(rsi,1)} "
                                                f"EMA50:{round(ema50,2)} 종가:{closed_price}"
                                            )
                                        except Exception as e:
                                            state["reentry_pending"] = False
                                            state["side"]            = None
                                            logging.warning(
                                                f"⚠️  [{symbol}] 진입 주문 실패, 상태 복구: {e}"
                                            )
                                elif state["reentry_pending"]:
                                    logging.debug(
                                        f"⏭️  [{symbol}] reentry_pending=True → 중복 진입 차단"
                                    )

                    # ─── [B] 포지션 있음 ───────────────────────────────────
                    else:
                        cur_sz  = float(main_p["size"])
                        cur_av  = float(main_p["avgPrice"])
                        # [BUG-3 수정] 키 철자 이중 대응
                        unr_pnl = _get_unr_pnl(main_p)

                        # ★ NameError 방지: if 블록 진입 여부와 무관하게 항상 초기화
                        # size변화(경로A) 진입 시 True → 경로B(본전체크) 스킵
                        nets_placed_this_cycle = False

                        if not state["active"] or abs(cur_sz - state["last_size"]) > 1e-6:
                            is_inc = cur_sz > state["last_size"]
                            time.sleep(3)

                            # 데이터 안정화 후 포지션 재조회
                            ref_res = session.get_positions(
                                category="linear", symbol=symbol
                            )["result"]["list"]

                            # [BUG-4 수정] positionIdx까지 함께 검증
                            tgt_idx = 1 if main_p["side"] == "Buy" else 2
                            main_p  = next(
                                (p for p in ref_res
                                 if p["side"] == main_p["side"]
                                 and int(p["positionIdx"]) == tgt_idx),
                                main_p
                            )
                            cur_sz  = float(main_p["size"])
                            cur_av  = float(main_p["avgPrice"])
                            # [BUG-3 수정] 재조회 후에도 이중 키 대응
                            unr_pnl = _get_unr_pnl(main_p)

                            if not state["active"]:
                                # ★V7 FIX: avgPrice 직접 고정
                                state["start_price"] = cur_av
                                state.update({
                                    "active":          True,
                                    "side":            main_p["side"],
                                    "detected_qty":    cur_sz,
                                    "reentry_pending": False,
                                    "breakeven_done":  False,
                                    "last_step":       1,
                                })
                                logging.info(
                                    f"🚀 [{symbol}] {state['side']} 전략 시작 | "
                                    f"기준가={state['start_price']} 기준수량={cur_sz}"
                                )
                                is_inc = True

                            current_step = self.calc_step(cur_sz, state["detected_qty"])
                            state.update({
                                "last_size":   cur_sz,
                                "entry_price": cur_av,
                                "last_step":   current_step,
                            })
                            if is_inc:
                                state["breakeven_done"] = False

                            # ★ADD-3: Lock으로 보호
                            # size 변화 경로(A)가 실행되면 이번 사이클 경로(B)는 스킵
                            if not state["lock"]:
                                state["lock"] = True
                                try:
                                    self.place_nets_and_exit(
                                        symbol, cur_av, cur_sz,
                                        state["side"], current_step, is_inc, unr_pnl
                                    )
                                    nets_placed_this_cycle = True   # ★ 경로A 실행 표시
                                finally:
                                    state["lock"] = False

                        # 본전 덜어내기 실시간 체크
                        # ★중복 오더 수정: 경로A(size변화)가 이미 실행됐으면 경로B 완전 스킵
                        # 기존: nets_placed_this_cycle 없음 → 같은 사이클에서 경로B 진입 가능
                        #       경로B의 cancel_and_wait가 경로A 오더 전체 취소 후 재배치 → 중복
                        # 수정: nets_placed_this_cycle=True 이면 경로B 조건 자체를 차단
                        processed_be = False
                        if (not nets_placed_this_cycle          # ★ 핵심 추가 조건
                                and state["last_step"] >= 2
                                and not state["breakeven_done"]
                                and unr_pnl >= 0
                                and not state["lock"]):
                            state["lock"] = True
                            try:
                                self.place_nets_and_exit(
                                    symbol, cur_av, cur_sz,
                                    state["side"], state["last_step"], False, unr_pnl
                                )
                                processed_be = True
                            finally:
                                state["lock"] = False

                        # 익절 체크 (본전 덜어내기 직후 사이클은 스킵)
                        if not processed_be:
                            round_trip_fee = cur_sz * cur_av * FEE_RATE * 2
                            net_pnl        = unr_pnl - round_trip_fee
                            multiplier     = 3
                            if net_pnl >= (round_trip_fee * multiplier) and not state["lock"]:
                                state["lock"] = True
                                try:
                                    self.do_take_profit(symbol, state["side"], cur_sz)
                                finally:
                                    state["lock"] = False

                time.sleep(5)

            except Exception as e:
                logging.error(f"루프 에러: {e}")
                time.sleep(10)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [4] 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _check_pid(pid: int) -> bool:
    """
    [BUG-9 수정] PID 생존 여부 확인 — Windows/Unix 호환
    1순위: psutil (크로스 플랫폼)
    2순위: os.kill(pid, 0) Unix 방식
    3순위: fallback — 판단 불가 시 False 반환(실행 허용)
    """
    # psutil 사용 가능 시 (가장 신뢰도 높음)
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass

    # Unix 계열 fallback
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
    except Exception:
        # Windows 등 예외 — 판단 불가, 실행 허용
        return False


if __name__ == "__main__":

    PID_FILE = "bybit7.pid"

    if os.path.exists(PID_FILE):
        try:
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
                logging.warning(f"⚠️  PID {old_pid} 은 이미 종료됨. PID 파일 재생성.")
        except (ValueError, IOError) as e:
            logging.warning(f"⚠️  PID 파일 읽기 실패, 재생성: {e}")

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    logging.info(f"🔒 PID 파일 생성: {PID_FILE} (PID: {os.getpid()})")

    try:
        bot = FinalSniperBotV7()
        bot.run()
    finally:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
            logging.info("🔓 PID 파일 삭제 완료")
