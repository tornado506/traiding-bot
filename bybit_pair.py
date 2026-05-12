"""
Bybit Pair Trading Bot - v14.0 Final
=====================================
페어트레이딩 + 트레일링 스탑 + 타임스탑 공존 구조

[v13 대비 변경사항]
  ❌ 제거: 고정 익절가 (tar: $15)
  ✅ 추가: 수익 보존형 트레일링 스탑 (종목별 차등)
  ✅ 추가: peak_net 추적 (포지션별 최고 순수익 기록)
  ✅ 추가: 트레일링 발동 후 타임스탑 정지 + 48시간 추가 상한
  ✅ 수정: 수수료 계산 — 진입+청산 왕복 수수료 모두 반영
  ✅ 추가: trailing_active, trailing_start_time 상태 영속화

[트레일링 스탑 설계 원칙]
  - 트레일링 미발동 구간: 타임스탑 정상 작동 (손실 포지션 안전망)
  - 트레일링 발동 이후:   타임스탑 정지, 추가 48시간 상한으로 대체
  - Callback 발생 시:     peak_net 대비 callback 이하로 떨어지면 청산

[종목별 파라미터 — 순수익 기준 / 왕복수수료 $12 완전 반영]
  CRYPTO  (BTC/ETH):   Activation 순수익 $22 / Callback $8  / 최저보장 $14
  METALS  (XAU/XAG):   Activation 순수익 $16 / Callback $6  / 최저보장 $10
  ALTCOINS(SOL/AVAX):  Activation 순수익 $22 / Callback $9  / 최저보장 $13

  * Activation 총손익 기준 = 순수익 + 왕복수수료 $12
    CRYPTO:   $22 + $12 = $34
    METALS:   $16 + $12 = $28
    ALTCOINS: $22 + $12 = $34
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
    'AVAXUSDT': 0,
}

# ══════════════════════════════════════════════════════
# [2] 전략 파라미터
# ══════════════════════════════════════════════════════
FEE_RATE = 0.0006   # 편도 수수료율 (메이커 0.02% + 슬리피지 여유)

PAIRS = {
    'CRYPTO': {
        'a':        'BTCUSDT',
        'b':        'ETHUSDT',
        'val':      10000,          # 포지션 총액 ($) — 각 레그 $5,000
        'ze':       2.2,            # 진입 Z 임계값
        'max_hold': 96,             # 타임스탑 (시간) — 트레일링 미발동 구간에만 작동
        # ── 트레일링 스탑 (순수익 기준) ──────────────
        'trail_activation': 22.0,   # 순수익 이 값 도달 시 트레일링 발동
        'trail_callback':    8.0,   # 발동 후 peak 대비 이 값 하락 시 청산
        'trail_max_hold':   48,     # 트레일링 발동 후 추가 최대 보유 시간
    },
    'METALS': {
        'a':        'XAUUSDT',
        'b':        'XAGUSDT',
        'val':      10000,
        'ze':       2.2,
        'max_hold': 120,
        'trail_activation': 16.0,
        'trail_callback':    6.0,
        'trail_max_hold':   48,
    },
    'ALTCOINS': {
        'a':        'SOLUSDT',
        'b':        'AVAXUSDT',
        'val':      10000,
        'ze':       2.4,
        'max_hold': 84,
        'trail_activation': 22.0,
        'trail_callback':    9.0,
        'trail_max_hold':   48,
    },
}

COOL_DOWN      = 900    # 청산 후 재진입 쿨타임 (초)
STATS_INTERVAL = 300    # Z-Score 재계산 주기 (초)
STATE_FILE     = 'bot_state.json'

# ══════════════════════════════════════════════════════
# [3] 런타임 상태
# ══════════════════════════════════════════════════════
in_position  = {p: False for p in PAIRS}
entry_z      = {p: 0.0   for p in PAIRS}
entry_time   = {p: 0.0   for p in PAIRS}
entry_side   = {p: 0     for p in PAIRS}
last_close   = {p: 0.0   for p in PAIRS}

# ── 트레일링 스탑 전용 상태 ─────────────────────────
peak_net           = {p: 0.0   for p in PAIRS}  # 포지션 보유 중 최고 순수익
trailing_active    = {p: False for p in PAIRS}  # 트레일링 발동 여부
trailing_start_time= {p: 0.0   for p in PAIRS}  # 트레일링 발동 시각

# Z-Score 캐시
last_stats_time = {p: 0.0          for p in PAIRS}
stats_cache     = {p: (None, None) for p in PAIRS}


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
            'in_position':       {k: bool(v)  for k, v in in_position.items()},
            'entry_z':           {k: float(v) for k, v in entry_z.items()},
            'entry_time':        {k: float(v) for k, v in entry_time.items()},
            'entry_side':        {k: int(v)   for k, v in entry_side.items()},
            'last_close':        {k: float(v) for k, v in last_close.items()},
            # ── 트레일링 상태 영속화 ──────────────────
            'peak_net':          {k: float(v) for k, v in peak_net.items()},
            'trailing_active':   {k: bool(v)  for k, v in trailing_active.items()},
            'trailing_start_time':{k: float(v) for k, v in trailing_start_time.items()},
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
        entry_z.update(s.get('entry_z',     {}))
        entry_time.update(s.get('entry_time',   {}))
        entry_side.update(s.get('entry_side',   {}))
        last_close.update(s.get('last_close',   {}))
        # ── 트레일링 상태 복구 ────────────────────────
        peak_net.update(          {k: float(v) for k, v in s.get('peak_net',           {}).items()})
        trailing_active.update(   {k: bool(v)  for k, v in s.get('trailing_active',    {}).items()})
        trailing_start_time.update({k: float(v) for k, v in s.get('trailing_start_time',{}).items()})
        log("✅ 상태 복구 완료")
    except FileNotFoundError:
        log("ℹ️  저장 파일 없음, 초기 상태로 시작")
    except Exception as e:
        log(f"⚠️ 상태 로드 에러: {e}")


def reset_pair(pid: str):
    """포지션 청산 후 해당 페어 상태 전체 초기화"""
    in_position[pid]        = False
    entry_z[pid]            = 0.0
    entry_time[pid]         = 0.0
    entry_side[pid]         = 0
    # 트레일링 상태도 함께 초기화
    peak_net[pid]           = 0.0
    trailing_active[pid]    = False
    trailing_start_time[pid]= 0.0


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
                    entry_time[pid] = time.time() - (c['max_hold'] * 3600 / 2)
                log(f"🔧 [{pid}] 고아 포지션 복구 "
                    f"(A={a_pos['side']}, side={entry_side[pid]})")
        elif not cur_pair and in_position[pid]:
            log(f"🔧 [{pid}] 포지션 없음 감지 → 상태 리셋")
            reset_pair(pid)


# ══════════════════════════════════════════════════════
# [6] 수수료 계산
# ══════════════════════════════════════════════════════
def calc_net_pnl(cur_pair: list) -> float:
    """
    순수익 계산 — 진입 + 청산 왕복 수수료 완전 반영
    ────────────────────────────────────────────────
    기존(v13): 청산 수수료만 편도 차감 → 실제보다 ~$18 과대 계상
    수정(v14): 진입(ent_p) + 청산(mk_p) 양방향 모두 차감
               총 왕복 수수료 약 $12 (포지션 $10,000 기준)
    """
    total_net = 0.0
    for p in cur_pair:
        _side = 1 if p['side'] == 'Buy' else -1
        qty   = safe_float(p.get('size'))
        ent_p = safe_float(p.get('avgPrice'))
        mk_p  = safe_float(p.get('markPrice'))
        total_net += (
            (mk_p - ent_p) * qty * _side
            + safe_float(p.get('curRealisedPnl'))
            - (ent_p * qty * FEE_RATE)   # ✅ 진입 수수료
            - (mk_p  * qty * FEE_RATE)   # ✅ 청산 수수료
        )
    return total_net


# ══════════════════════════════════════════════════════
# [7] 15분봉 Z-Score 계산
# ══════════════════════════════════════════════════════
def get_stats(s1: str, s2: str, pid: str):
    """15분봉 100개 OLS Z-Score 계산. 반환: (z, df) 또는 (None, None)"""
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
# [8] 주문 실행
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
    """
    두 다리 병렬 실행.
    반쪽 체결 감지 시 성공한 다리를 즉시 역청산 → 원상복구.
    """
    results: dict = {}

    def worker(sym, side, qty, p_idx):
        results[sym] = _place_market(sym, side, qty, p_idx, reduce)

    threads = [threading.Thread(target=worker, args=o) for o in orders]
    for t in threads: t.start()
    for t in threads: t.join()

    failed  = [sym for sym, ok in results.items() if not ok]
    success = [sym for sym, ok in results.items() if ok]

    if failed and not reduce:
        log(f"⚠️ 반쪽 체결 감지! 실패:{failed} → 성공 다리 역청산 중")
        side_map = {o[0]: o[1] for o in orders}
        qty_map  = {o[0]: o[2] for o in orders}
        idx_map  = {o[0]: o[3] for o in orders}
        for sym in success:
            rev = 'Sell' if side_map[sym].lower() == 'buy' else 'Buy'
            _place_market(sym, rev, qty_map[sym], idx_map[sym], reduce=True)
        return False

    return all(results.values())


# ══════════════════════════════════════════════════════
# [9] 트레일링 스탑 로직
# ══════════════════════════════════════════════════════
def check_trailing_stop(pid: str, net: float, now_t: float) -> tuple:
    """
    트레일링 스탑 상태 업데이트 및 청산 여부 판단.

    반환: (should_close: bool, reason: str)

    ── 동작 원리 ──────────────────────────────────────
    [트레일링 미발동 구간]
      - peak_net 갱신만 수행
      - 타임스탑은 메인 루프에서 정상 작동

    [트레일링 발동 조건]
      - 순수익(net) >= trail_activation 최초 도달 시
      - trailing_active = True, trailing_start_time 기록

    [트레일링 발동 이후]
      - 타임스탑 정지 (메인 루프에서 trailing_active 체크)
      - 대신 trail_max_hold(48h) 추가 상한 적용
      - peak_net 계속 갱신
      - net <= peak_net - trail_callback 이면 청산

    [최저 보장 순수익]
      CRYPTO:   peak_net - $8  (최소 $14 보장)
      METALS:   peak_net - $6  (최소 $10 보장)
      ALTCOINS: peak_net - $9  (최소 $13 보장)
    """
    c = PAIRS[pid]

    # peak_net 갱신 (항상)
    if net > peak_net[pid]:
        peak_net[pid] = net

    # 트레일링 발동 체크
    if not trailing_active[pid] and net >= c['trail_activation']:
        trailing_active[pid]     = True
        trailing_start_time[pid] = now_t
        log(f"🎯 [{pid}] 트레일링 스탑 발동! "
            f"순수익={net:.1f}$ "
            f"(Activation=${c['trail_activation']}) "
            f"→ 타임스탑 정지, {c['trail_max_hold']}h 추가 상한 시작")
        save_state()
        return False, ""

    # 트레일링 발동 이후 청산 조건 체크
    if trailing_active[pid]:

        # ① trail_max_hold 초과 시 강제 청산
        trail_elapsed_h = (now_t - trailing_start_time[pid]) / 3600
        if trail_elapsed_h >= c['trail_max_hold']:
            return True, (
                f"트레일링 추가보유 상한 "
                f"({trail_elapsed_h:.1f}h/{c['trail_max_hold']}h) "
                f"순수익={net:.1f}$"
            )

        # ② Callback 발생 시 청산
        callback_trigger = peak_net[pid] - c['trail_callback']
        if net <= callback_trigger:
            return True, (
                f"트레일링 Callback "
                f"peak={peak_net[pid]:.1f}$ "
                f"현재={net:.1f}$ "
                f"(하락={peak_net[pid]-net:.1f}$ / "
                f"허용={c['trail_callback']}$) "
                f"확정순수익≈{net:.1f}$"
            )

    return False, ""


# ══════════════════════════════════════════════════════
# [10] 메인 루프
# ══════════════════════════════════════════════════════
def main():
    load_state()

    log("=" * 65)
    log("🚀 Pair Engine v14.0 [트레일링 스탑 + 타임스탑 공존]")
    for pid, c in PAIRS.items():
        log(f"   {pid}: {c['a']}/{c['b']} "
            f"ze={c['ze']} val=${c['val']} "
            f"maxhold={c['max_hold']}h "
            f"trail_act=${c['trail_activation']} "
            f"callback=${c['trail_callback']}")
    log(f"   쿨타임={COOL_DOWN}s  Z재계산={STATS_INTERVAL}s")
    log("=" * 65)

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
            now_t = time.time()

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
                        (wd == 5 and hr >= 6) or
                        (wd == 6) or
                        (wd == 0 and hr < 7)
                    )

                # ── Z-Score 캐시 갱신 (300초 주기) ───────
                if now_t - last_stats_time[pid] >= STATS_INTERVAL:
                    z, df = get_stats(c['a'], c['b'], pid)
                    stats_cache[pid]     = (z, df)
                    last_stats_time[pid] = now_t

                z, df = stats_cache[pid]
                z_str = f"{z:.2f}" if z is not None else "N/A"

                # ══════════════════════════════════════════
                # [A] 포지션 없음 — 진입 로직
                # ══════════════════════════════════════════
                if not cur_pair:
                    if in_position[pid]:
                        reset_pair(pid)
                        save_state()

                    cooldown_ok = (now_t - last_close[pid] > COOL_DOWN)
                    can_enter   = (
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
                            in_position[pid]  = True
                            entry_z[pid]      = float(z)
                            entry_time[pid]   = time.time()
                            entry_side[pid]   = 1 if z > 0 else -1
                            peak_net[pid]     = 0.0        # peak 초기화
                            save_state()
                        else:
                            last_close[pid] = time.time()
                            log(f"⚠️ [{pid}] 진입 실패 — {COOL_DOWN}초 쿨타임 적용")

                    cool_remain = max(0, COOL_DOWN - (now_t - last_close[pid]))
                    closed_mark = "💤" if (pid == 'METALS' and metals_closed) else ""
                    status_report.append(
                        f"{pid}{closed_mark} Z={z_str} 쿨={cool_remain:.0f}s"
                    )

                # ══════════════════════════════════════════
                # [B] 포지션 있음 — 청산 로직
                # ══════════════════════════════════════════
                else:
                    # 순수익 계산 (왕복 수수료 완전 반영)
                    net = calc_net_pnl(cur_pair)

                    hold_h   = (now_t - entry_time[pid]) / 3600 \
                               if entry_time[pid] > 0 else 0
                    max_hold = c['max_hold']

                    # ── 트레일링 스탑 상태 업데이트 ──────
                    should_close, trail_reason = check_trailing_stop(pid, net, now_t)

                    # ── 타임스탑 판단 ─────────────────────
                    # 트레일링 발동 이후에는 타임스탑 완전 정지
                    # 트레일링 미발동 구간에서만 max_hold 체크
                    time_hit  = (
                        not trailing_active[pid]
                        and hold_h >= max_hold
                    )

                    # 트레일링 발동 후 추가 보유 시간 표시용
                    trail_h = (
                        (now_t - trailing_start_time[pid]) / 3600
                        if trailing_active[pid] and trailing_start_time[pid] > 0
                        else 0
                    )

                    # 상태 로그
                    trail_mark = (
                        f" 🎯trail={trail_h:.1f}h/{c['trail_max_hold']}h"
                        f" peak={peak_net[pid]:.1f}$"
                        if trailing_active[pid]
                        else ""
                    )
                    status_report.append(
                        f"{pid} "
                        f"Net={net:.1f}$ "
                        f"Z={z_str} "
                        f"hold={hold_h:.1f}h/{max_hold}h"
                        f"{trail_mark}"
                    )

                    # ── 청산 실행 ─────────────────────────
                    close_reason = None
                    if should_close:
                        close_reason = trail_reason
                    elif time_hit:
                        close_reason = f"TimeStop({hold_h:.1f}h/{max_hold}h) 순수익={net:.1f}$"

                    if close_reason:
                        log(f"💰 [{pid}] 청산 — {close_reason}")
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
