"""
Bybit Pair Trading Bot - v13.0 Final
=====================================
페어트레이딩 본질에 충실한 최소 구조

진입: 15분봉 Z-Score 임계값 초과 시
청산: 목표 순익 $40 달성 OR Time Stop 도달

안전장치 (3가지만):
  - 상태 영속화 (JSON): 재시작 시 Time Stop 정확히 유지
  - 포지션 방향 역산 복구: 재시작 시 청산 방향 오류 방지
  - METALS 휴장 진입 차단: 주말 미체결 방지

제거된 것:
  - 이중 타임프레임 (1h+15m)
  - 공적분 검정 / 베타 괴리 필터
  - 변동성 연동 손절선
  - Better-Z 재진입 필터
  - 분할 진입 (ze1/ze2) → 단일 진입
  - 강제익절 1.5x override
  - 동적 목표 순익
  - 포지션 크기 동적 조절
  - 반쪽 체결 역청산 (오류 가능성 제거)
"""

import ccxt
import pandas as pd
import statsmodels.api as sm
import time
import datetime
import json
import threading
from pybit.unified_trading import HTTP

# ══════════════════════════════════════════════════════
# [1] 인스턴스 설정
# ══════════════════════════════════════════════════════
API_KEY = 'PdCEy7g1TSfU5SY5aQ'.strip()
SECRET  = 'lYE2y2y7c79wG3K0kntZiwgwDwhdHY6mNUHo'.strip()

ex_pub  = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'linear'}})
session = HTTP(testnet=False, demo=True, api_key=API_KEY, api_secret=SECRET)
session.endpoint = "https://api-demo.bybit.com"

PRECISION = {
    'BTCUSDT':  3,
    'ETHUSDT':  2,
    'XAUUSDT':  2,
    'XAGUSDT':  1,
    'SOLUSDT':  1,
    'AVAXUSDT': 2,
}

# ══════════════════════════════════════════════════════
# [2] 전략 파라미터
# ══════════════════════════════════════════════════════
PAIRS = {
    'CRYPTO': {
        'a':        'BTCUSDT',
        'b':        'ETHUSDT',
        'val':      8000,       # 포지션 총액 ($) — 각 레그 $4,000
        'tar':      40.0,       # 목표 순익 ($)
        'ze':       1.9,        # 진입 Z 임계값
        'max_hold': 48,         # Time Stop (시간)
    },
    'METALS': {
        'a':        'XAUUSDT',
        'b':        'XAGUSDT',
        'val':      8000,
        'tar':      40.0,
        'ze':       2.0,
        'max_hold': 96,
    },
    'ALTCOINS': {
        'a':        'SOLUSDT',
        'b':        'AVAXUSDT',
        'val':      8000,
        'tar':      40.0,
        'ze':       1.8,
        'max_hold': 72,
    },
}

COOL_DOWN      = 900    # 청산 후 재진입 쿨타임 (초)
STATS_INTERVAL = 300    # Z-Score 재계산 주기 (초)
STATE_FILE     = 'bot_state.json'

# ══════════════════════════════════════════════════════
# [3] 런타임 상태
# ══════════════════════════════════════════════════════
in_position  = {p: False for p in PAIRS}  # 포지션 보유 여부
entry_z      = {p: 0.0   for p in PAIRS}  # 진입 Z
entry_time   = {p: 0.0   for p in PAIRS}  # 진입 시각
entry_side   = {p: 0     for p in PAIRS}  # 진입 방향 (1/-1)
last_close   = {p: 0.0   for p in PAIRS}  # 마지막 청산 시각

# Z-Score 캐시
last_stats_time = {p: 0.0           for p in PAIRS}
stats_cache     = {p: (None, None)  for p in PAIRS}  # (z, df)


# ══════════════════════════════════════════════════════
# [4] 유틸
# ══════════════════════════════════════════════════════
def log(msg: str):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════
# [5] 상태 영속화
# ══════════════════════════════════════════════════════
def save_state():
    try:
        state = {
            'in_position': {k: bool(v)  for k, v in in_position.items()},
            'entry_z':     {k: float(v) for k, v in entry_z.items()},
            'entry_time':  {k: float(v) for k, v in entry_time.items()},
            'entry_side':  {k: int(v)   for k, v in entry_side.items()},
            'last_close':  {k: float(v) for k, v in last_close.items()},
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log(f"⚠️ 상태 저장 에러: {e}")


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        in_position.update({k: bool(v)  for k, v in s.get('in_position', {}).items()})
        entry_z.update(s.get('entry_z',    {}))
        entry_time.update(s.get('entry_time',  {}))
        entry_side.update(s.get('entry_side',  {}))
        last_close.update(s.get('last_close',  {}))
        log("✅ 상태 복구 완료")
    except FileNotFoundError:
        log("ℹ️  저장 파일 없음, 초기 상태로 시작")
    except Exception as e:
        log(f"⚠️ 상태 로드 에러: {e}")


def reset_pair(pid: str):
    in_position[pid] = False
    entry_z[pid]     = 0.0
    entry_time[pid]  = 0.0
    entry_side[pid]  = 0


def verify_state_vs_positions(all_pos):
    """
    재시작 시 실제 포지션과 상태 불일치 복구.
    A종목 side(Buy/Sell)로 진입 방향 역산.
    """
    for pid, c in PAIRS.items():
        cur_pair = [p for p in all_pos if p.get('symbol') in [c['a'], c['b']]]
        if cur_pair and not in_position[pid]:
            a_pos = next((p for p in cur_pair if p['symbol'] == c['a']), None)
            if a_pos:
                in_position[pid] = True
                entry_side[pid]  = 1 if a_pos['side'] == 'Sell' else -1
                entry_z[pid]     = float(c['ze']) * entry_side[pid]
                if entry_time[pid] == 0:
                    # 진입 시각 불명 → 최대 보유 시간의 절반 경과로 가정
                    entry_time[pid] = time.time() - (c['max_hold'] * 3600 / 2)
                log(f"🔧 [{pid}] 고아 포지션 복구 "
                    f"(A={a_pos['side']}, side={entry_side[pid]})")
        elif not cur_pair and in_position[pid]:
            log(f"🔧 [{pid}] 포지션 없음 감지 → 상태 리셋")
            reset_pair(pid)


# ══════════════════════════════════════════════════════
# [6] 15분봉 Z-Score 계산
# ══════════════════════════════════════════════════════
def get_stats(s1: str, s2: str, pid: str):
    """
    15분봉 100개 OLS Z-Score 계산.
    반환: (z, df) 또는 (None, None)
    """
    try:
        r1 = ex_pub.fetch_ohlcv(s1, '15m', limit=120)
        r2 = ex_pub.fetch_ohlcv(s2, '15m', limit=120)

        if len(r1) < 60 or len(r2) < 60:
            log(f"⚠️ [{pid}] 데이터 부족 ({len(r1)}/{len(r2)}봉)")
            return None, None

        df = pd.merge(
            pd.DataFrame(r1, columns=['ts','o','h','l','c1','v'])[['ts','c1']],
            pd.DataFrame(r2, columns=['ts','o','h','l','c2','v'])[['ts','c2']],
            on='ts'
        ).dropna().tail(100).reset_index(drop=True)

        if len(df) < 60:
            return None, None

        y   = df['c1']
        x   = sm.add_constant(df['c2'])
        res = sm.OLS(y, x).fit()
        beta, alpha = float(res.params.iloc[1]), float(res.params.iloc[0])

        spr = df['c1'] - (alpha + beta * df['c2'])
        std = float(spr.std())
        if std < 1e-9:
            return None, None

        return float(spr.iloc[-1] / std), df

    except ccxt.NetworkError as e:
        log(f"🌐 [{pid}] 네트워크 오류: {e}")
        return None, None
    except ccxt.ExchangeError as e:
        log(f"🏦 [{pid}] 거래소 오류: {e}")
        return None, None
    except Exception as e:
        log(f"⚠️ [{pid}] get_stats 예외: {e}")
        return None, None


# ══════════════════════════════════════════════════════
# [7] 주문 실행
# ══════════════════════════════════════════════════════
def _place_market(sym: str, side: str, qty: float,
                  p_idx: int, reduce: bool = False) -> bool:
    try:
        qty_str = str(round(qty, PRECISION.get(sym, 2)))
        res = session.place_order(
            category="linear", symbol=sym,
            side=side.capitalize(), orderType="Market",
            qty=qty_str, positionIdx=p_idx, reduceOnly=reduce,
        )
        if res.get('retCode') == 10001:
            session.place_order(
                category="linear", symbol=sym,
                side=side.capitalize(), orderType="Market",
                qty=qty_str, positionIdx=0, reduceOnly=reduce,
            )
        return True
    except Exception as e:
        log(f"⚠️ 주문 에러 ({sym} {side}): {e}")
        return False


def place_pair_orders(orders: list, reduce: bool = False) -> bool:
    """두 다리 병렬 실행."""
    results: dict = {}

    def worker(sym, side, qty, p_idx):
        results[sym] = _place_market(sym, side, qty, p_idx, reduce)

    threads = [threading.Thread(target=worker, args=o) for o in orders]
    for t in threads: t.start()
    for t in threads: t.join()

    return all(results.values())


# ══════════════════════════════════════════════════════
# [8] 메인 루프
# ══════════════════════════════════════════════════════
def main():
    load_state()

    log("=" * 60)
    log("🚀 Pair Engine v13.0 Final [Z-Score + Time Stop]")
    for pid, c in PAIRS.items():
        log(f"   {pid}: {c['a']}/{c['b']} "
            f"ze={c['ze']} val=${c['val']} "
            f"tar=${c['tar']} maxhold={c['max_hold']}h")
    log(f"   쿨타임={COOL_DOWN}s  Z재계산={STATS_INTERVAL}s")
    log("=" * 60)

    loop_cnt = 0

    while True:
        try:
            # ── 포지션 조회 ───────────────────────────────
            res_pos = session.get_positions(category="linear", settleCoin="USDT")
            all_pos = [
                p for p in res_pos['result']['list']
                if safe_float(p.get('size', 0)) > 0
            ]

            if loop_cnt == 0:
                verify_state_vs_positions(all_pos)

            status_report = []

            for pid, c in PAIRS.items():
                cur_pair = [
                    p for p in all_pos
                    if p.get('symbol') in [c['a'], c['b']]
                ]

                # ── METALS 휴장 체크 ──────────────────────
                metals_closed = False
                if pid == 'METALS':
                    now_dt = datetime.datetime.now()
                    wd, hr = now_dt.weekday(), now_dt.hour
                    metals_closed = (
                        (wd == 5 and hr >= 6) or  # 토 06시~
                        (wd == 6) or              # 일 전체
                        (wd == 0 and hr < 7)      # 월 07시 이전
                    )

                # ── Z-Score 캐시 갱신 (300초 주기) ───────
                now_t = time.time()
                if now_t - last_stats_time[pid] >= STATS_INTERVAL:
                    z, df = get_stats(c['a'], c['b'], pid)
                    stats_cache[pid]     = (z, df)
                    last_stats_time[pid] = now_t

                z, df = stats_cache[pid]
                z_str = f"{z:.2f}" if z is not None else "N/A"

                # ── 포지션 없음: 진입 로직 ────────────────
                if not cur_pair:
                    # 상태 불일치 정리
                    if in_position[pid]:
                        reset_pair(pid)
                        save_state()

                    # 쿨타임 / 휴장 / Z 체크
                    cooldown_ok   = (now_t - last_close[pid] > COOL_DOWN)
                    can_enter     = (
                        z is not None
                        and cooldown_ok
                        and not (pid == 'METALS' and metals_closed)
                        and abs(z) >= c['ze']
                    )

                    if can_enter:
                        try:
                            tk1 = ex_pub.fetch_ticker(c['a'])
                            tk2 = ex_pub.fetch_ticker(c['b'])
                        except Exception as e:
                            log(f"⚠️ [{pid}] 티커 조회 실패: {e}")
                            status_report.append(f"{pid}: Z={z_str} 티커실패")
                            continue

                        val_per_leg = c['val'] / 2
                        s1, s2 = ('Sell', 'Buy') if z > 0 else ('Buy', 'Sell')
                        orders = [
                            (c['a'], s1,
                             val_per_leg / safe_float(tk1.get('last'), 1),
                             1 if s1 == 'Buy' else 2),
                            (c['b'], s2,
                             val_per_leg / safe_float(tk2.get('last'), 1),
                             1 if s2 == 'Buy' else 2),
                        ]
                        log(f"🎯 [{pid}] 진입 Z={z_str} "
                            f"({'A숏/B롱' if z > 0 else 'A롱/B숏'})")
                        ok = place_pair_orders(orders, reduce=False)
                        if ok:
                            in_position[pid] = True
                            entry_z[pid]     = float(z)
                            entry_time[pid]  = time.time()
                            entry_side[pid]  = 1 if z > 0 else -1
                            save_state()

                    cool_remain = max(0, COOL_DOWN - (now_t - last_close[pid]))
                    closed_mark = "💤" if (pid == 'METALS' and metals_closed) else ""
                    status_report.append(
                        f"{pid}{closed_mark} "
                        f"Z={z_str} "
                        f"쿨={cool_remain:.0f}s"
                    )

                # ── 포지션 있음: 청산 로직 ────────────────
                else:
                    # 순수익 계산 (미실현 + 실현누적 - 청산수수료)
                    total_net = 0.0
                    for p in cur_pair:
                        _side = 1 if p['side'] == 'Buy' else -1
                        qty   = safe_float(p.get('size'))
                        ent_p = safe_float(p.get('avgPrice'))
                        mk_p  = safe_float(p.get('markPrice'))
                        total_net += (
                            (mk_p - ent_p) * qty * _side
                            + safe_float(p.get('curRealisedPnl'))
                            - (mk_p * qty * 0.0006)
                        )

                    hold_h = (time.time() - entry_time[pid]) / 3600 \
                             if entry_time[pid] > 0 else 0
                    max_hold = c['max_hold']

                    # 청산 조건: 목표 순익 OR Time Stop
                    tp_hit   = (total_net >= c['tar'])
                    time_hit = (hold_h >= max_hold)

                    status_report.append(
                        f"{pid} "
                        f"Net={total_net:.1f}$ "
                        f"Z={z_str} "
                        f"hold={hold_h:.1f}h/{max_hold}h"
                    )

                    if tp_hit or time_hit:
                        reason = f"익절({total_net:.1f}$)" if tp_hit \
                                 else f"TimeStop({hold_h:.1f}h)"
                        log(f"💰 [{pid}] 청산 — {reason}")

                        close_orders = [
                            (p['symbol'],
                             'Sell' if p['side'] == 'Buy' else 'Buy',
                             safe_float(p.get('size')),
                             int(safe_float(p.get('positionIdx'), 0)))
                            for p in cur_pair
                        ]
                        ok = place_pair_orders(close_orders, reduce=True)
                        if ok:
                            last_close[pid] = time.time()
                            reset_pair(pid)
                            save_state()

            loop_cnt += 1
            if loop_cnt % 30 == 0:
                log(f"🔎 감시중 | {' | '.join(status_report)}")
            time.sleep(1)

        except Exception as e:
            log(f"⚠️ 메인 루프 에러: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
