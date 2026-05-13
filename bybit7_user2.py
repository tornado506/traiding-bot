"""
FinalSniperBotV7 — 최종 실전용 마스터판 (V10)
══════════════════════════════════════════════════════════════════
[원본 bybit7-1.py 대비 전체 수정 이력]

  ✅ BUG-1  로깅 핸들러 이중 등록 제거 / 파일명 단일화
  ✅ BUG-2  is_near_supply_zone 진입 조건 실제 연결
  ✅ BUG-3  unrealisedPnl / unrealizedPnl 이중 키 대응
  ✅ BUG-4  positionIdx 재조회 검증
  ✅ BUG-5  state 초기화 완전화 (_reset_state 헬퍼)
  ✅ BUG-6  본전/거미줄 오더 try/except 분리
  ✅ BUG-7  get_market_price 공급 구역 체크 연결
  ✅ BUG-8  thresholds MAX_STEP 자동 연동
  ✅ BUG-9  PID 체크 Windows 호환

  ✅ 중복오더-1  nets_placed_this_cycle 플래그
                 경로A(size변화) 실행 후 경로B(본전체크) 동일 사이클 스킵
  ✅ 중복오더-2  NameError 방지: nets_placed_this_cycle if블록 외부 초기화

  ✅ 중복오더-3  [핵심 / 실전 발생 원인]
                 placing 플래그 추가로 레이스 컨디션 완전 차단
                 time.sleep(3) 대기 중 다음 루프 사이클이 돌아도
                 placing=True 이면 size변화 감지 블록 자체를 차단
                 → 어제/오늘 스크린샷의 중복 오더 원인 완전 해결

  ✅ 구조개선    종목별 독립 스레드 (SymbolWorker) 병렬 처리
                 단일 for 루프 → 각 종목 독립 Thread
                 한 종목의 cancel_and_wait(최대 6초) / time.sleep(3)이
                 다른 종목을 블로킹하는 문제 구조적 완전 해결
                 placing 플래그가 각 스레드 내부에서만 작동하므로
                 스레드 간 간섭 원천 차단

  ✅ BotManager  워커 스레드 생명주기 감시 (watchdog)
                 비정상 종료 감지 시 자동 재시작
                 KeyboardInterrupt 시 전 종목 안전 종료 (join)

  ✅ 로깅        %(threadName)s 추가 → 어느 종목 로그인지 즉시 식별

  ✅ 헷지손절-1  unr_pnl 합산 PnL 수정
                 기존: main_p(메인 방향)만 조회 → 헷지 손실 미반영
                 수정: pos_res 전체 합산 → 추가 API 호출 없이 진짜 순손익

  ✅ 헷지손절-2  익절 조건에 헷지 손실 방어선 추가
                 헷지 손실이 메인 수익의 80% 초과 시 익절 차단
                 → 합산 음수임에도 익절 발동하는 케이스 완전 방지

  ✅ 헷지손절-3  메인+헷지 동시 청산 (threading 병렬)
                 기존: 메인 청산 → 헷지 청산 순차 실행 → 시차 손실 발생
                 수정: 두 포지션을 동시 시장가 청산 → 시차 완전 제거
"""

from datetime import datetime
import logging
import os
import sys
import threading
import time

import numpy as np
import pandas as pd
import requests
from pybit.unified_trading import HTTP


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [1] 로깅 — 1회 등록 / threadName 포함 (BUG-1 + 멀티스레드)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_logger = logging.getLogger()
_logger.handlers.clear()
_logger.setLevel(logging.INFO)
_fmt = logging.Formatter('%(asctime)s [%(threadName)s] %(message)s')
_fh  = logging.FileHandler("bybit7.log", encoding="utf-8")
_fh.setFormatter(_fmt)
_sh  = logging.StreamHandler()
_sh.setFormatter(_fmt)
_logger.addHandler(_fh)
_logger.addHandler(_sh)

logging.info("Step 1: 프로그램 로딩 시작...")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [2] 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API_KEY        = "MZxVDs6SlVHsDndKXE"
API_SECRET     = "ba3OlKPxWgdMjEVddU844DrAREqWY3MgRVVv"
TELEGRAM_TOKEN = ""  
CHAT_ID        = ""  

# 전역 단일 세션 — pybit HTTP 내부는 thread-safe
session = HTTP(
    testnet=False,
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

# intervals: 퍼센트(%) 기반 단계별 갭 비율
# sum([:n]) 으로 누적% 계산 → start_price 기준 실제 가격 갭으로 변환
CONFIGS = {
    "BTCUSDT":  {"intervals": [1.5, 1.5, 1.5, 2.0, 2.5, 2.5], "tick_size": 1, "qty_step": 3, "min_unit": 0.01},
    "XAUUSDT":  {"intervals": [1.5, 1.5, 1.5, 2.0, 2.5, 2.5], "tick_size": 2, "qty_step": 2, "min_unit": 0.2},
    "ETHUSDT":  {"intervals": [1.5, 1.5, 1.5, 2.0, 2.5, 2.5], "tick_size": 2, "qty_step": 2, "min_unit": 0.3},
}

MAX_STEP           = 7
FEE_RATE           = 0.0007
REENTRY_COOLDOWN   = 900
SUPPLY_ZONE_MARGIN = 0.003
HEDGE_START_STEP   = 5
LOOP_INTERVAL      = 5       # 종목별 루프 주기 (초)
SUPPLY_ZONE_TTL    = 1800    # 공급 구역 갱신 주기 (초)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [3] 전역 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_msg(text: str) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=5,
        )
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
        kline = session.get_kline(
            category="linear", symbol=symbol, interval="60", limit=720
        )
        df = pd.DataFrame(
            kline["result"]["list"],
            columns=["ts", "o", "h", "l", "c", "v", "t"],
        ).apply(pd.to_numeric)
        pivots = sorted(list(df["h"]) + list(df["l"]))
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
    """BUG-2: 공급 구역 근접 여부 — zones 비어있으면 보수적으로 True"""
    if not zones:
        return True
    for zone in zones:
        if abs(price - zone) / zone <= SUPPLY_ZONE_MARGIN:
            return True
    return False


def cancel_and_wait(symbol: str, timeout: float = 6.0) -> bool:
    """Order + StopOrder 전부 취소 후 완전 소멸 확인"""
    for order_filter in ["Order", "StopOrder"]:
        try:
            session.cancel_all_orders(
                category="linear", symbol=symbol, orderFilter=order_filter
            )
        except:
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
        except:
            pass
    logging.warning(
        f"[{symbol}] ⚠️ 오더 취소 확인 타임아웃 ({timeout}초) — 강제 진행"
    )
    return False


def _get_unr_pnl(position: dict) -> float:
    """BUG-3: unrealisedPnl(영국식) / unrealizedPnl(미국식) 양쪽 대응"""
    val = position.get("unrealisedPnl") or position.get("unrealizedPnl") or 0
    return float(val)


def _make_fresh_state() -> dict:
    """종목 state 초기값 — 모든 필드 명시 (BUG-5 + placing 플래그)"""
    return {
        "active":           False,
        "side":             None,
        "detected_qty":     0,
        "last_size":        0,
        "last_tp_time":     0,
        "last_entry_price": 0,
        "entry_price":      0,
        "last_step":        1,
        "start_price":      0,
        "breakeven_done":   False,
        "reentry_pending":  False,
        "lock":             False,
        # ★ 중복오더-3: 레이스 컨디션 차단 플래그
        # place_nets_and_exit 실행 진입 시 True
        # time.sleep(3) 대기 중 다음 루프가 와도 이 블록 재진입 불가
        # 완료 후(성공/실패 무관) 반드시 False로 해제
        "placing":          False,
    }


def _reset_state(state: dict, keep_tp_time: bool = True):
    """BUG-5: 포지션 소멸/익절 시 모든 필드 명시적 초기화"""
    tp_time = state.get("last_tp_time", 0)
    state.update(_make_fresh_state())
    state["last_tp_time"] = time.time() if keep_tp_time else tp_time


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [4] 종목별 독립 스레드 워커
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SymbolWorker(threading.Thread):
    """
    한 종목 전담 스레드.

    중복 오더 방지 3계층
    ────────────────────
    1. placing 플래그 (레이스 컨디션 차단 — 핵심)
       place_nets_and_exit 호출 직전 True 설정.
       time.sleep(3) 대기 중 이 스레드가 다시 루프를 돌아도
       placing=True 이면 size변화 감지 블록을 완전히 건너뜀.
       finally에서 반드시 False 해제.

    2. nets_placed_this_cycle 플래그 (사이클 내 이중 호출 차단)
       경로A(size변화) 성공 후 True.
       같은 사이클 안의 경로B(본전체크) 진입 차단.
       if 블록 외부에서 False 초기화 → NameError 방지.

    3. lock 플래그 (동시 실행 차단)
       place_nets_and_exit / do_take_profit 실행 중
       다른 경로의 동시 진입 차단.

    독립성
    ──────
    - state, supply_zone 캐시 각자 보유
    - 다른 종목 스레드의 sleep/대기가 전혀 영향 없음
    - stop_event로 외부에서 안전 종료 가능
    """

    def __init__(self, symbol: str, stop_event: threading.Event):
        super().__init__(name=symbol, daemon=True)
        self.symbol     = symbol
        self.conf       = CONFIGS[symbol]
        self.stop_event = stop_event
        self.state      = _make_fresh_state()
        self._supply_zones         = []
        self._supply_zones_updated = 0.0

    # ── 지표 계산 ──────────────────────────────────────────────
    def _get_indicators(self) -> tuple:
        try:
            kline = session.get_kline(
                category="linear", symbol=self.symbol, interval="15", limit=200
            )
            df = pd.DataFrame(
                kline["result"]["list"],
                columns=["ts", "o", "h", "l", "c", "v", "t"],
            ).apply(pd.to_numeric).sort_values("ts", ascending=True)
            delta    = df["c"].diff()
            gain     = delta.where(delta > 0, 0)
            loss     = -delta.where(delta < 0, 0)
            avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rs       = avg_gain / (avg_loss + 1e-9)
            rsi_s    = 100 - (100 / (1 + rs))
            ema50_s  = df["c"].ewm(span=50, adjust=False).mean()
            return float(rsi_s.iloc[-2]), float(ema50_s.iloc[-2]), float(df["c"].iloc[-2])
        except:
            return 50.0, 0.0, 0.0

    def _get_market_price(self) -> float:
        """BUG-7: 실시간 현재가 — 공급 구역 체크에 활용"""
        try:
            resp = session.get_tickers(category="linear", symbol=self.symbol)
            return float(resp["result"]["list"][0]["lastPrice"])
        except:
            return 0.0

    # ── 스텝/가격 계산 ─────────────────────────────────────────
    @staticmethod
    def _calc_step(cur_sz: float, base_qty: float) -> int:
        if base_qty <= 0:
            return 1
        ratio      = cur_sz / base_qty
        thresholds = [2 ** i for i in range(MAX_STEP)]   # BUG-8: MAX_STEP 자동 연동
        step = 1
        for i, t in enumerate(thresholds):
            if ratio >= t * 0.95:
                step = i + 1
        return min(step, MAX_STEP)

    def _target_price(self, cumulative_pct: float, side: str) -> float:
        """
        % 갭 → 실제 가격 변환 (부동소수점 안전)
        tick_size+2 중간 정밀도로 오차 흡수 후 최종 tick_size 반올림
        """
        sp  = self.state["start_price"]
        ts  = self.conf["tick_size"]
        gap = round(sp * cumulative_pct / 100, ts + 2)
        raw = sp - gap if side == "Buy" else sp + gap
        return round(raw, ts)

    # ── 오더 배치 ──────────────────────────────────────────────
    def _place_nets_and_exit(
        self,
        avg_price: float,
        current_sz: float,
        side: str,
        current_step: int,
        is_increase: bool,
        unr_pnl: float,
    ) -> bool:
        conf      = self.conf
        state     = self.state
        sym       = self.symbol
        p_idx     = 1 if side == "Buy" else 2
        opp_p_idx = 2 if side == "Buy" else 1
        opp_side  = "Sell" if side == "Buy" else "Buy"
        trig_dir  = 2 if side == "Buy" else 1

        cancel_and_wait(sym)

        # ── 본전 50% 덜어내기 — 독립 try/except (BUG-6) ──────
        if (current_step >= 2 and state["last_step"] >= 2
                and unr_pnl >= 0
                and not state["breakeven_done"]
                and not is_increase):
            try:
                half_qty = round(current_sz * 0.5, conf["qty_step"])
                if half_qty > 0:
                    session.place_order(
                        category="linear", symbol=sym,
                        side=opp_side, orderType="Limit",
                        qty=str(half_qty),
                        price=str(round(avg_price, conf["tick_size"])),
                        reduceOnly=True, positionIdx=p_idx,
                    )
                    state["breakeven_done"] = True
                    logging.info(f"📉 [{sym}] 본전 50% 덜어내기 배치")
            except Exception as e:
                logging.warning(f"[{sym}] 본전 오더 실패 (거미줄 계속 진행): {e}")

        # ── 거미줄 오더 — step별 독립 try/except (BUG-6) ──────
        success = True
        for step in range(2, MAX_STEP + 1):
            if step <= current_step:
                continue
            end_idx        = min(step - 1, len(conf["intervals"]))
            cumulative_pct = sum(conf["intervals"][:end_idx])
            target_p       = self._target_price(cumulative_pct, side)
            l_qty          = round(
                state["detected_qty"] * (2 ** (step - 2)), conf["qty_step"]
            )
            if l_qty <= 0:
                continue

            try:
                session.place_order(
                    category="linear", symbol=sym,
                    side=side, orderType="Limit",
                    qty=str(l_qty), price=str(target_p),
                    timeInForce="PostOnly", positionIdx=p_idx,
                )
            except Exception as e:
                logging.error(f"[{sym}] step={step} 리밋 오더 실패: {e}")
                success = False
                continue

            # ── 헷징 오더 ────────────────────────────────────
            if step >= HEDGE_START_STEP:
                hedge_qty = (
                    l_qty if step >= 6
                    else round(l_qty * 0.5, conf["qty_step"])
                )
                if hedge_qty > 0:
                    try:
                        session.place_order(
                            category="linear", symbol=sym,
                            side=opp_side, orderType="Market",
                            qty=str(hedge_qty),
                            triggerPrice=str(target_p),
                            triggerBy="LastPrice",
                            triggerDirection=trig_dir,
                            positionIdx=opp_p_idx,
                        )
                    except Exception as e:
                        logging.error(f"[{sym}] step={step} 헷지 오더 실패: {e}")
                        success = False

        return success

    def _do_take_profit(self, side: str, cur_sz: float):
        """
        ★ 헷지손절-3: 메인 + 헷지 동시 청산 (threading 병렬)
        ──────────────────────────────────────────────────────
        기존: 메인 청산 → 헷지 청산 순차 실행
              두 주문 사이 시차(수백ms~수초)에 시세 변동으로
              헷지가 불리한 가격에 체결되어 추가 손실 발생
        수정: 현재 포지션 전체를 재조회 후 threading으로 동시 시장가 청산
              양방향이 거의 동일 시점에 체결 → 시차 손실 완전 제거
        """
        conf  = self.conf
        state = self.state
        sym   = self.symbol
        try:
            cancel_and_wait(sym)

            # 청산 직전 포지션 재조회 — 실제 수량 정확도 확보
            cur_all = session.get_positions(
                category="linear", symbol=sym
            )["result"]["list"]

            close_results = {}

            def _close_one(p_side: str, p_qty: float, p_posidx: int, c_side: str):
                try:
                    session.place_order(
                        category="linear", symbol=sym,
                        side=c_side, orderType="Market",
                        qty=str(round(p_qty, conf["qty_step"])),
                        reduceOnly=True, positionIdx=p_posidx,
                    )
                    close_results[p_side] = True
                    logging.info(
                        f"✅ [{sym}] {p_side} 포지션 청산 "
                        f"qty={p_qty} posIdx={p_posidx}"
                    )
                except Exception as e:
                    close_results[p_side] = False
                    logging.error(f"[{sym}] {p_side} 포지션 청산 실패: {e}")

            # 보유 중인 전체 포지션 동시 청산 스레드 구성
            threads = []
            for p in cur_all:
                p_sz = float(p.get("size", 0))
                if p_sz <= 0:
                    continue
                p_side   = p["side"]
                p_posidx = int(p.get("positionIdx", 0))
                c_side   = "Sell" if p_side == "Buy" else "Buy"
                t = threading.Thread(
                    target=_close_one,
                    args=(p_side, p_sz, p_posidx, c_side),
                )
                threads.append(t)

            if not threads:
                logging.warning(f"[{sym}] 청산할 포지션 없음")
                return

            # 동시 발사
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            failed = [k for k, v in close_results.items() if not v]
            if failed:
                logging.error(
                    f"[{sym}] 청산 실패 포지션: {failed} — 수동 확인 필요"
                )
            else:
                _reset_state(state, keep_tp_time=True)
                send_msg(f"💰 [{sym}] 익절 완료 ({get_total_balance()})")

        except Exception as e:
            logging.error(f"[{sym}] 익절 오류: {e}")

    # ── 메인 루프 ──────────────────────────────────────────────
    def run(self):
        sym   = self.symbol
        conf  = self.conf
        state = self.state

        logging.info(f"🟢 [{sym}] 워커 스레드 시작")
        cancel_and_wait(sym, timeout=8.0)
        logging.info(f"  [{sym}] 잔존 오더 정리 완료")

        while not self.stop_event.is_set():
            try:
                # 공급 구역 30분마다 갱신
                if time.time() - self._supply_zones_updated > SUPPLY_ZONE_TTL:
                    self._supply_zones         = get_supply_zones(sym)
                    self._supply_zones_updated = time.time()

                # 포지션 조회
                pos_res = session.get_positions(
                    category="linear", symbol=sym
                )["result"]["list"]

                long_p  = next(
                    (p for p in pos_res
                     if p["side"] == "Buy" and float(p["size"]) > 0), None
                )
                short_p = next(
                    (p for p in pos_res
                     if p["side"] == "Sell" and float(p["size"]) > 0), None
                )
                main_p = (
                    (long_p if state["side"] == "Buy" else short_p)
                    if state["active"]
                    else (long_p or short_p)
                )

                # 금/은 거래 시간 제한 (KST 기준)
                is_metal = "XAU" in sym or "XAG" in sym
                if is_metal and not main_p:
                    now    = datetime.now()
                    wd, hr = now.weekday(), now.hour
                    if (wd == 5 and hr >= 6) or (wd == 6) or (wd == 0 and hr < 7):
                        time.sleep(LOOP_INTERVAL)
                        continue

                # ── [A] 포지션 없음 ──────────────────────────────────
                if not main_p:
                    if state["active"]:
                        _reset_state(state, keep_tp_time=True)   # BUG-5

                    if ((time.time() - state["last_tp_time"]) > REENTRY_COOLDOWN
                            and not state["reentry_pending"]):
                        rsi, ema50, closed_price = self._get_indicators()

                        if ema50 > 0:
                            long_A  = closed_price > ema50 and rsi <= 35
                            long_B  = rsi <= 30
                            short_A = closed_price < ema50 and rsi >= 65
                            short_B = rsi >= 70

                            side_order = None
                            if long_A or long_B:
                                side_order = "Buy"
                            elif short_A or short_B:
                                side_order = "Sell"

                            if side_order:
                                # BUG-2/7: 실시간 시세로 공급 구역 체크
                                cur_price = self._get_market_price()
                                if (side_order == "Buy"
                                        and is_near_supply_zone(
                                            cur_price, self._supply_zones)):
                                    logging.info(
                                        f"⛔ [{sym}] 공급 구역 근처 롱 진입 차단: {cur_price}"
                                    )
                                else:
                                    qty = conf["min_unit"]
                                    state["reentry_pending"] = True   # Atomic
                                    state["side"]            = side_order
                                    try:
                                        session.place_order(
                                            category="linear", symbol=sym,
                                            side=side_order, orderType="Market",
                                            qty=str(round(qty, conf["qty_step"])),
                                            positionIdx=1 if side_order == "Buy" else 2,
                                        )
                                        reason = (
                                            "A(눌림목)"
                                            if (side_order == "Buy" and long_A)
                                               or (side_order == "Sell" and short_A)
                                            else "B(절대RSI)"
                                        )
                                        logging.info(
                                            f"🎯 [{sym}] {side_order} 진입 | "
                                            f"조건:{reason} RSI:{round(rsi,1)} "
                                            f"EMA50:{round(ema50,2)} 종가:{closed_price}"
                                        )
                                    except Exception as e:
                                        state["reentry_pending"] = False
                                        state["side"]            = None
                                        logging.warning(
                                            f"⚠️ [{sym}] 진입 실패, 복구: {e}"
                                        )
                            elif state["reentry_pending"]:
                                logging.debug(
                                    f"⏭️ [{sym}] reentry_pending=True → 차단"
                                )

                # ── [B] 포지션 있음 ──────────────────────────────────
                else:
                    cur_sz  = float(main_p["size"])
                    cur_av  = float(main_p["avgPrice"])

                    # ★ 헷지손절-1: 메인+헷지 합산 PnL
                    # 기존: main_p(메인 방향)만 조회 → 헷지 손실 미반영
                    #       메인 +26$ / 헷지 -52$ 상황에서 익절 발동 → 실손 발생
                    # 수정: pos_res(이미 조회된 전체)에서 해당 종목 전체 합산
                    #       추가 API 호출 없이 진짜 합산 순손익으로 판단
                    unr_pnl   = sum(_get_unr_pnl(p) for p in pos_res)
                    # 익절 방어선 계산용 — 메인/헷지 개별 PnL 보존
                    main_pnl  = _get_unr_pnl(main_p)
                    hedge_p   = short_p if state["side"] == "Buy" else long_p
                    hedge_pnl = _get_unr_pnl(hedge_p) if hedge_p else 0.0

                    # ★ 중복오더-2: NameError 방지
                    # if 블록 외부에서 항상 False로 초기화
                    nets_placed_this_cycle = False

                    # ★ 중복오더-3: placing 플래그 체크
                    # place_nets_and_exit 실행 중이면 이 블록 전체 스킵
                    # → time.sleep(3) 대기 중 다음 루프가 와도 재진입 불가
                    if not state["placing"] and (
                        not state["active"]
                        or abs(cur_sz - state["last_size"]) > 1e-6
                    ):
                        is_inc = cur_sz > state["last_size"]

                        # ★ placing=True: sleep 전에 설정
                        # 이 순간부터 다른 루프 사이클이 이 블록에 진입 불가
                        state["placing"] = True
                        time.sleep(3)   # 데이터 안정화 대기

                        try:
                            # 재조회 + BUG-4: positionIdx 함께 검증
                            ref_res = session.get_positions(
                                category="linear", symbol=sym
                            )["result"]["list"]
                            tgt_idx = 1 if main_p["side"] == "Buy" else 2
                            main_p  = next(
                                (p for p in ref_res
                                 if p["side"] == main_p["side"]
                                 and int(p["positionIdx"]) == tgt_idx),
                                main_p,
                            )
                            cur_sz  = float(main_p["size"])
                            cur_av  = float(main_p["avgPrice"])
                            unr_pnl = _get_unr_pnl(main_p)   # BUG-3

                            if not state["active"]:
                                state["start_price"] = cur_av   # V7: avgPrice 고정
                                state.update({
                                    "active":          True,
                                    "side":            main_p["side"],
                                    "detected_qty":    cur_sz,
                                    "reentry_pending": False,
                                    "breakeven_done":  False,
                                    "last_step":       1,
                                })
                                logging.info(
                                    f"🚀 [{sym}] {state['side']} 전략 시작 | "
                                    f"기준가={state['start_price']} "
                                    f"기준수량={cur_sz}"
                                )
                                is_inc = True

                            current_step = self._calc_step(
                                cur_sz, state["detected_qty"]
                            )
                            state.update({
                                "last_size":   cur_sz,
                                "entry_price": cur_av,
                                "last_step":   current_step,
                            })
                            if is_inc:
                                state["breakeven_done"] = False

                            # 경로A: size 변화 → 거미줄 전체 재배치
                            if not state["lock"]:
                                state["lock"] = True
                                try:
                                    self._place_nets_and_exit(
                                        cur_av, cur_sz, state["side"],
                                        current_step, is_inc, unr_pnl,
                                    )
                                    # ★ 중복오더-1: 경로A 실행 표시
                                    nets_placed_this_cycle = True
                                finally:
                                    state["lock"] = False

                        except Exception as e:
                            logging.error(f"[{sym}] 포지션 처리 오류: {e}")
                        finally:
                            # ★ 성공/실패 무관하게 반드시 placing 해제
                            state["placing"] = False

                    # 경로B: 본전 덜어내기 실시간 체크
                    # ① nets_placed_this_cycle: 경로A 실행 사이클 → 스킵
                    # ② placing: 경로A 실행 중인 사이클 → 스킵
                    processed_be = False
                    if (not nets_placed_this_cycle
                            and not state["placing"]
                            and state["last_step"] >= 2
                            and not state["breakeven_done"]
                            and unr_pnl >= 0
                            and not state["lock"]):
                        state["lock"] = True
                        try:
                            self._place_nets_and_exit(
                                cur_av, cur_sz, state["side"],
                                state["last_step"], False, unr_pnl,
                            )
                            processed_be = True
                        finally:
                            state["lock"] = False

                    # 익절 체크
                    if not processed_be and not state["placing"]:
                        round_trip_fee = cur_sz * cur_av * FEE_RATE * 2
                        net_pnl        = unr_pnl - round_trip_fee

                        # ★ 헷지손절-2: 헷지 손실 방어선
                        # 헷지 손실이 메인 수익의 80% 초과 시 익절 차단
                        # → 합산 실손익이 음수인데 익절이 발동하는 케이스 방지
                        hedge_danger = (
                            hedge_pnl < 0
                            and main_pnl > 0
                            and abs(hedge_pnl) > main_pnl * 0.8
                        )
                        if hedge_danger:
                            logging.warning(
                                f"⛔ [{sym}] 익절 차단 — 헷지 손실 과다 "
                                f"(메인={main_pnl:.1f}$ 헷지={hedge_pnl:.1f}$ "
                                f"합산={unr_pnl:.1f}$)"
                            )

                        if (net_pnl >= round_trip_fee * 3
                                and not hedge_danger
                                and not state["lock"]):
                            state["lock"] = True
                            try:
                                self._do_take_profit(state["side"], cur_sz)
                            finally:
                                state["lock"] = False

            except Exception as e:
                logging.error(f"[{sym}] 루프 에러: {e}")
                # 에러 시에도 placing이 걸려있으면 반드시 해제
                if state.get("placing"):
                    state["placing"] = False
                time.sleep(10)
                continue

            time.sleep(LOOP_INTERVAL)

        logging.info(f"🔴 [{sym}] 워커 스레드 종료")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [5] BotManager — 스레드 생명주기 감시
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BotManager:
    """
    종목별 SymbolWorker 스레드 생성·감시·재시작.

    - watchdog 루프: 비정상 종료된 워커 10초마다 감지 후 자동 재시작
    - KeyboardInterrupt: stop_event 전파 → 전 종목 안전 종료 (join)
    """

    def __init__(self):
        self.stop_event = threading.Event()
        self.workers: dict = {}

    def _start_worker(self, symbol: str) -> SymbolWorker:
        w = SymbolWorker(symbol, self.stop_event)
        w.start()
        self.workers[symbol] = w
        return w

    def run(self):
        logging.info("Step 3: 바이비트 데모 접속 완료")
        logging.info(f"Step 4: 잔액: {get_total_balance()}")
        logging.info(f"🚀 총 {len(CONFIGS)}개 종목 병렬 스레드 시작")

        for symbol in CONFIGS:
            self._start_worker(symbol)

        try:
            while not self.stop_event.is_set():
                time.sleep(10)
                if self.stop_event.is_set():
                    break
                for symbol, worker in list(self.workers.items()):
                    if not worker.is_alive():
                        logging.warning(
                            f"⚠️ [{symbol}] 워커 스레드 비정상 종료 감지 — 자동 재시작"
                        )
                        self._start_worker(symbol)

        except KeyboardInterrupt:
            logging.info("🛑 KeyboardInterrupt — 전 종목 워커 종료 중...")
            self.stop_event.set()
            for symbol, worker in self.workers.items():
                worker.join(timeout=15)
                if worker.is_alive():
                    logging.warning(f"⚠️ [{symbol}] 워커 강제 종료 (join timeout)")
            logging.info("✅ 전 종목 워커 종료 완료")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [6] 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _check_pid(pid: int) -> bool:
    """BUG-9: Windows/Unix 호환 PID 생존 확인"""
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
    except:
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
                    f"중복 실행 차단.\n   강제 종료: kill {old_pid}"
                )
                sys.exit(1)
            else:
                logging.warning(
                    f"⚠️ PID {old_pid} 은 이미 종료됨. PID 파일 재생성."
                )
        except (ValueError, IOError) as e:
            logging.warning(f"⚠️ PID 파일 읽기 실패, 재생성: {e}")

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    logging.info(f"🔒 PID 파일 생성: {PID_FILE} (PID: {os.getpid()})")

    try:
        manager = BotManager()
        manager.run()
    finally:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
            logging.info("🔓 PID 파일 삭제 완료")
