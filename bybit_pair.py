"""
Bybit Pair Trading Bot - v10.4 Final
=====================================
v10.3 대비 수정 사항:
  [FIX-1] TP AND 데드락: 목표 150% 달성 시 spread_ok 무시하고 무조건 익절
  [FIX-2] Better-Z 재진입 필터 복원 (v10.3에서 누락됨)
  [FIX-3] 공적분 붕괴 시 보유 포지션 즉시 청산
  [FIX-4] 반쪽 체결 역청산 로직 복원 (v10.3에서 제거됨)
  [FIX-5] verify_state_vs_positions: 실제 포지션 방향 역산으로 entry_z 복구
  [FIX-6] get_stats_dual: 예외 세분화 + 데이터 부족 명시적 체크
  [FIX-7] 변동성 연동 다이나믹 손절선 (고정 +2.5 → ATR 기반)
  [FIX-8] 48시간 Time Stop (시간 스케일 이탈 강제 청산)
"""

import ccxt
import pandas as pd
import statsmodels.api as sm
import statsmodels.tsa.stattools as ts
import time
import datetime
import json
import threading
from pybit.unified_trading import HTTP

# ──────────────────────────────────────────────
# [1] 설정 및 인스턴스
# ──────────────────────────────────────────────
API_KEY = 'PdCEy7g1TSfU5SY5aQ'.strip()
SECRET  = 'lYE2y2y7c79wG3K0kntZiwgwDwhdHY6mNUHo'.strip()

ex_pub  = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'linear'}})
session = HTTP(testnet=False, demo=True, api_key=API_KEY, api_secret=SECRET)
session.endpoint = "https://api-demo.bybit.com"

PRECISION = {'BTCUSDT': 3, 'ETHUSDT': 2, 'XAUUSDT': 2, 'XAGUSDT': 1}

# ──────────────────────────────────────────────
# [2] 전략 파라미터
# ──────────────────────────────────────────────
PAIRS = {
    'CRYPTO': {
        'a': 'BTCUSDT', 'b': 'ETHUSDT',
        'val': 10000,
        'tar': 20.0,        # 목표 순익 (슬리피지 버퍼 포함)
        'ze':  2.65,        # 진입 Z 임계값
        'slip': 8.0,
    },
    'METALS': {
        'a': 'XAUUSDT', 'b': 'XAGUSDT',
        'val': 10000,
        'tar': 16.0,
        'ze':  2.2,
        'slip': 6.0,
    },
}

COOL_DOWN        = 900      # 쿨타임 15분 (초)
BETA_DIV_MAX     = 0.5     # Beta 괴리 허용 한도 50%
COINT_INTERVAL   = 3600     # 공적분 검정 주기 1시간 (초)
TAR_OVERRIDE_MULT = 1.5     # [FIX-1] 목표의 150% 도달 시 spread_ok 무시
MAX_HOLD_HOURS   = 48       # [FIX-8] 최대 보유 시간 (시간)
STATE_FILE       = 'bot_state.json'

# ──────────────────────────────────────────────
# [3] 런타임 상태
# ──────────────────────────────────────────────
entry_z_map    = {p: 0.0  for p in PAIRS}   # 현재 포지션 진입 Z
entry_spread   = {p: 0.0  for p in PAIRS}   # 진입 시 스프레드 raw값
entry_time     = {p: 0.0  for p in PAIRS}   # [FIX-8] 진입 시각 (time.time())
last_entry_z   = {p: 0.0  for p in PAIRS}   # 직전 청산 포지션 진입 Z
last_side      = {p: 0    for p in PAIRS}   # 직전 진입 방향 (1/-1)
last_close     = {p: 0.0  for p in PAIRS}   # 마지막 청산 시각
last_coint_chk = {p: 0.0  for p in PAIRS}   # 마지막 공적분 검정 시각
coint_ok       = {p: True for p in PAIRS}   # 공적분 유효 여부

# ──────────────────────────────────────────────
# [4] 유틸
# ──────────────────────────────────────────────
def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ──────────────────────────────────────────────
# [5] 상태 영속화
# ──────────────────────────────────────────────
def save_state():
    """Numpy 타입을 Python 기본 타입으로 변환하여 JSON 직렬화 오류 방지"""
    try:
        state = {
            'entry_z_map':  {k: float(v) for k, v in entry_z_map.items()},
            'entry_spread': {k: float(v) for k, v in entry_spread.items()},
            'entry_time':   {k: float(v) for k, v in entry_time.items()},
            'last_entry_z': {k: float(v) for k, v in last_entry_z.items()},
            'last_side':    {k: int(v)   for k, v in last_side.items()},
            'last_close':   {k: float(v) for k, v in last_close.items()},
            'coint_ok':     {k: bool(v)  for k, v in coint_ok.items()},
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log(f"⚠️ 상태 저장 에러: {e}")


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        entry_z_map.update(s.get('entry_z_map',  {}))
        entry_spread.update(s.get('entry_spread', {}))
        entry_time.update(s.get('entry_time',   {}))
        last_entry_z.update(s.get('last_entry_z', {}))
        last_side.update(s.get('last_side',    {}))
        last_close.update(s.get('last_close',  {}))
        coint_ok.update(s.get('coint_ok',    {}))
        log("✅ 상태 복구 완료")
    except FileNotFoundError:
        log("ℹ️  저장 파일 없음, 초기 상태로 시작")
    except Exception as e:
        log(f"⚠️ 상태 로드 에러: {e}")


def verify_state_vs_positions(all_pos):
    """
    [FIX-5] 기동 시 실제 포지션 방향을 역산하여 entry_z_map을 복구.
    v10.3: 항상 양수(c['ze'])로 고정 → 음수 진입 포지션에서 SL/TP 방향 오류 발생.
    v10.4: A종목 side(Buy/Sell)로 진입 방향을 추론하여 부호를 결정.
    """
    for pid, c in PAIRS.items():
        cur_pair = [p for p in all_pos if p.get('symbol') in [c['a'], c['b']]]
        if cur_pair and entry_z_map[pid] == 0:
            a_pos = next((p for p in cur_pair if p['symbol'] == c['a']), None)
            if a_pos:
                # A종목 Sell → 양수 Z 진입 / A종목 Buy → 음수 Z 진입
                inferred_sign = 1 if a_pos['side'] == 'Sell' else -1
                entry_z_map[pid] = float(c['ze']) * inferred_sign
                # 진입 시각 복구 불가 → 현재 시각에서 MAX_HOLD 절반으로 보수 설정
                if entry_time[pid] == 0:
                    entry_time[pid] = time.time() - (MAX_HOLD_HOURS * 3600 / 2)
                log(f"🔧 [{pid}] 고아 포지션 복구: entry_z={entry_z_map[pid]:.2f} "
                    f"(A={a_pos['side']}), time_stop 24h 후 적용")


# ──────────────────────────────────────────────
# [6] 지표 계산
# ──────────────────────────────────────────────
def _merge_df(r1, r2):
    df = pd.merge(
        pd.DataFrame(r1, columns=['ts','o','h','l','c1','v'])[['ts','c1']],
        pd.DataFrame(r2, columns=['ts','o','h','l','c2','v'])[['ts','c2']],
        on='ts'
    ).dropna()
    return df


def _calc_beta_alpha(df):
    y = df['c1']
    x = sm.add_constant(df['c2'])
    res = sm.OLS(y, x).fit()
    return float(res.params.iloc[1]), float(res.params.iloc[0])


def _calc_z(df, beta, alpha):
    spr = df['c1'] - (alpha + beta * df['c2'])
    std = float(spr.std())
    if std == 0:
        return None, None
    return float(spr.iloc[-1] / std), float(spr.iloc[-1])


def check_coint(pid, s1, s2):
    """공적분 검정 — 1시간 주기. 결과를 coint_ok에 반영."""
    now = time.time()
    if now - last_coint_chk[pid] < COINT_INTERVAL:
        return
    try:
        r1 = ex_pub.fetch_ohlcv(s1, '15m', limit=200)
        r2 = ex_pub.fetch_ohlcv(s2, '15m', limit=200)
        df = _merge_df(r1, r2)
        if len(df) < 100:
            log(f"⚠️ [{pid}] 공적분 검정 데이터 부족 ({len(df)}행)")
            return
        pvalue = ts.coint(df['c1'], df['c2'])[1]
        coint_ok[pid] = bool(pvalue < 0.1)
        last_coint_chk[pid] = now
        log(f"🔬 [{pid}] 공적분 p={pvalue:.4f} → "
            f"{'✅ 유효' if coint_ok[pid] else '❌ 무효'}")
        save_state()
    except Exception as e:
        log(f"⚠️ [{pid}] 공적분 검정 에러: {e}")


def get_stats_dual(s1, s2, pid):
    """
    [FIX-6] 장기 500봉 + 단기 100봉 이원화 OLS.
    예외를 세분화하여 네트워크/거래소/데이터 오류를 구분 로깅.
    반환: (z, raw_spread, is_diverged, divergence_ratio)
    """
    try:
        r1_long = ex_pub.fetch_ohlcv(s1, '15m', limit=500)
        r2_long = ex_pub.fetch_ohlcv(s2, '15m', limit=500)

        # 데이터 부족 명시적 체크
        if len(r1_long) < 150 or len(r2_long) < 150:
            log(f"⚠️ [{pid}] OHLCV 데이터 부족 ({len(r1_long)}/{len(r2_long)}봉)")
            return None, None, True, 0.0

        df_long  = _merge_df(r1_long, r2_long)
        if len(df_long) < 150:
            log(f"⚠️ [{pid}] 병합 후 데이터 부족 ({len(df_long)}행)")
            return None, None, True, 0.0

        df_short = df_long.tail(100).copy()

        beta_long,  _            = _calc_beta_alpha(df_long)
        beta_short, alpha_short  = _calc_beta_alpha(df_short)

        divergence = abs(beta_long - beta_short) / (abs(beta_long) + 1e-9)
        is_div     = divergence > BETA_DIV_MAX

        z, raw_spr = _calc_z(df_short, beta_short, alpha_short)
        return z, raw_spr, is_div, divergence

    except ccxt.NetworkError as e:
        log(f"🌐 [{pid}] 네트워크 오류: {e}")
        return None, None, True, 0.0
    except ccxt.ExchangeError as e:
        log(f"🏦 [{pid}] 거래소 오류: {e}")
        return None, None, True, 0.0
    except Exception as e:
        log(f"⚠️ [{pid}] get_stats_dual 예외: {e}")
        return None, None, True, 0.0


def calc_adaptive_sl(ent_z, df_short):
    """
    [FIX-7] 변동성 연동 다이나믹 손절선.
    최근 20봉 가격 변동성 → 손절 배율 2.0~3.5 동적 조정.
    낮은 변동성 장세: 배율 축소(빠른 손절) / 높은 변동성 장세: 배율 확대(조기 손절 방지)
    """
    try:
        recent = df_short['c1'].tail(20)
        vol_ratio = float(recent.std() / (recent.mean() + 1e-9))
        # 정상(0.01 이하) → 2.0배 / 고변동(0.03 이상) → 3.5배
        vol_mult = 2.0 + min(vol_ratio / 0.01 * 0.5, 1.5)
    except Exception:
        vol_mult = 2.5   # 계산 실패 시 기존값 유지

    sl = ent_z + vol_mult if ent_z > 0 else ent_z - vol_mult
    return sl, round(vol_mult, 2)


# ──────────────────────────────────────────────
# [7] 주문 실행
# ──────────────────────────────────────────────
def _place_market(sym, side, qty, p_idx, reduce=False):
    try:
        qty_str = str(round(float(qty), PRECISION.get(sym, 2)))
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
        log(f"⚠️ Market 주문 에러 ({sym}): {e}")
        return False


def place_pair_orders(orders: list, reduce=False):
    """
    [FIX-4] 두 다리를 병렬 실행.
    반쪽 체결 감지 시 성공한 다리를 즉시 역청산.
    """
    results = {}

    def worker(sym, side, qty, p_idx):
        results[sym] = _place_market(sym, side, qty, p_idx, reduce)

    threads = [threading.Thread(target=worker, args=o) for o in orders]
    for t in threads: t.start()
    for t in threads: t.join()

    failed  = [sym for sym, ok in results.items() if not ok]
    success = [sym for sym, ok in results.items() if ok]

    if failed:
        log(f"⚠️ 반쪽 체결 감지! 실패: {failed} → 성공 다리 즉시 역청산")
        side_map = {o[0]: o[1] for o in orders}
        qty_map  = {o[0]: o[2] for o in orders}
        idx_map  = {o[0]: o[3] for o in orders}
        for sym in success:
            rev = 'Sell' if side_map[sym].lower() == 'buy' else 'Buy'
            _place_market(sym, rev, qty_map[sym], idx_map[sym], reduce=True)
        return False

    return True


# ──────────────────────────────────────────────
# [8] 청산 조건 판단
# ──────────────────────────────────────────────
def check_spread_reversion(pid, z):
    """진입 Z의 50% 이상 평균 회귀 여부 확인."""
    ent_z = entry_z_map[pid]
    if ent_z == 0 or z is None:
        return False
    return (z < ent_z * 0.5) if ent_z > 0 else (z > ent_z * 0.5)


def should_close(pid, c, total_net, z, df_short):
    """
    청산 조건 통합 판단.
    반환: (close_flag, reason_str)

    청산 트리거:
      1. 익절: total_net >= tar AND spread_ok (정상 익절)
      2. 익절 override: total_net >= tar * 1.5 (spread_ok 무관) [FIX-1]
      3. 손절: Z가 dynamic_sl 초과 [FIX-7 변동성 연동]
      4. 공적분 붕괴: coint_ok=False [FIX-3]
      5. 시간 초과: 보유 48시간 이상 [FIX-8]
    """
    spread_ok = check_spread_reversion(pid, z)
    ent_z     = entry_z_map[pid] if entry_z_map[pid] != 0 \
                else (c['ze'] if (z or 0) > 0 else -c['ze'])

    # [FIX-7] 변동성 연동 손절선
    if df_short is not None:
        dynamic_sl, vol_mult = calc_adaptive_sl(ent_z, df_short)
    else:
        dynamic_sl = ent_z + 2.5 if ent_z > 0 else ent_z - 2.5
        vol_mult   = 2.5

    # 조건 평가
    if total_net >= c['tar'] and spread_ok:
        return True, f"익절(순익:{total_net:.1f}$, 회귀확인)"

    if total_net >= c['tar'] * TAR_OVERRIDE_MULT:             # [FIX-1]
        return True, f"익절override(순익:{total_net:.1f}$, {TAR_OVERRIDE_MULT}x달성)"

    if z is not None and abs(z) >= abs(dynamic_sl):
        return True, f"손절(Z:{z:.2f}>SL:{dynamic_sl:.2f}, vol배율:{vol_mult})"

    if not coint_ok[pid]:                                      # [FIX-3]
        return True, "공적분붕괴 강제청산"

    hold_hours = (time.time() - entry_time[pid]) / 3600
    if entry_time[pid] > 0 and hold_hours >= MAX_HOLD_HOURS:  # [FIX-8]
        return True, f"시간초과({hold_hours:.1f}h≥{MAX_HOLD_HOURS}h)"

    return False, ""


# ──────────────────────────────────────────────
# [9] 메인 루프
# ──────────────────────────────────────────────
def main():
    load_state()
    log(f"🚀 Pair Engine v10.4 Final 가동 | "
        f"CRYPTO tar=${PAIRS['CRYPTO']['tar']} ze={PAIRS['CRYPTO']['ze']} | "
        f"METALS tar=${PAIRS['METALS']['tar']} ze={PAIRS['METALS']['ze']}")

    loop_cnt = 0
    df_short_cache = {p: None for p in PAIRS}   # get_stats에서 얻은 df를 SL 계산에 재사용

    while True:
        try:
            res_pos = session.get_positions(category="linear", settleCoin="USDT")
            all_pos = [p for p in res_pos['result']['list'] if float(p.get('size', 0)) > 0]

            if loop_cnt == 0:
                verify_state_vs_positions(all_pos)   # [FIX-5]

            status_report = []

            for pid, c in PAIRS.items():
                cur_pair = [p for p in all_pos if p.get('symbol') in [c['a'], c['b']]]

                # 공적분 검정 (1시간 주기)
                check_coint(pid, c['a'], c['b'])

                # 이원화 Z-Score 계산 [FIX-6]
                z, raw_spr, is_div, div_val = get_stats_dual(c['a'], c['b'], pid)
                z_str = f"{z:.2f}" if z is not None else "N/A"

                # df_short 캐시 업데이트 (adaptive SL용)
                if z is not None:
                    try:
                        r1 = ex_pub.fetch_ohlcv(c['a'], '15m', limit=100)
                        r2 = ex_pub.fetch_ohlcv(c['b'], '15m', limit=100)
                        df_short_cache[pid] = _merge_df(r1, r2)
                    except Exception:
                        pass   # 캐시 실패 시 이전 값 유지

                # ── 포지션 없음: 진입 로직 ──────────────────────
                if not cur_pair:
                    now         = time.time()
                    cooldown_ok = (now - last_close[pid] > COOL_DOWN)

                    can_enter = False
                    if z is not None and cooldown_ok and coint_ok[pid] and not is_div:
                        curr_side = 1 if z > 0 else -1

                        # [FIX-2] Better-Z 재진입 필터 복원
                        same_dir_better = (
                            curr_side == last_side[pid]
                            and abs(z) > abs(last_entry_z[pid])
                        )
                        diff_dir = (curr_side != last_side[pid])

                        if (same_dir_better or diff_dir) and abs(z) >= c['ze']:
                            can_enter = True

                    if can_enter:
                        log(f"🎯 [{pid}] 진입 "
                            f"(Z:{z_str}, 이전Z:{last_entry_z[pid]:.2f}, 괴리:{div_val:.1%})")
                        tk1 = ex_pub.fetch_ticker(c['a'])
                        tk2 = ex_pub.fetch_ticker(c['b'])
                        s1, s2 = ('Sell', 'Buy') if z > 0 else ('Buy', 'Sell')
                        orders = [
                            (c['a'], s1, (c['val']/2) / tk1['last'], 1 if s1=='Buy' else 2),
                            (c['b'], s2, (c['val']/2) / tk2['last'], 1 if s2=='Buy' else 2),
                        ]
                        ok = place_pair_orders(orders, reduce=False)
                        if ok:
                            entry_z_map[pid]  = float(z)
                            entry_spread[pid] = float(raw_spr) if raw_spr is not None else 0.0
                            entry_time[pid]   = time.time()
                            last_side[pid]    = int(1 if z > 0 else -1)
                            save_state()

                    hold_info = ""
                    status_report.append(
                        f"{pid}:Z={z_str}{'⚡' if is_div else ''} "
                        f"{'[coint❌]' if not coint_ok[pid] else ''}"
                    )

                # ── 포지션 있음: 청산 로직 ───────────────────────
                else:
                    total_net = 0.0
                    for p in cur_pair:
                        side  = 1 if p['side'] == 'Buy' else -1
                        qty   = float(p['size'])
                        ent_p = float(p['avgPrice'])
                        mk_p  = float(p['markPrice'])
                        total_net += (
                            (mk_p - ent_p) * qty * side
                            + float(p.get('curRealisedPnl', 0))
                            - (mk_p * qty * 0.0006)
                        )

                    hold_hours = (time.time() - entry_time[pid]) / 3600 \
                                 if entry_time[pid] > 0 else 0

                    close_flag, reason = should_close(
                        pid, c, total_net, z, df_short_cache[pid]
                    )

                    ent_z = entry_z_map[pid] if entry_z_map[pid] != 0 \
                            else (c['ze'] if (z or 0) > 0 else -c['ze'])
                    dynamic_sl, vol_mult = calc_adaptive_sl(
                        ent_z, df_short_cache[pid]
                    ) if df_short_cache[pid] is not None else (ent_z + 2.5, 2.5)

                    spread_ok = check_spread_reversion(pid, z)
                    status_report.append(
                        f"{pid}:Net={total_net:.1f}$ Z={z_str} "
                        f"SL={dynamic_sl:.2f}(x{vol_mult}) "
                        f"rev={'✅' if spread_ok else '❌'} "
                        f"hold={hold_hours:.1f}h"
                    )

                    if close_flag:
                        log(f"💰 [{pid}] 청산 — {reason}")
                        close_orders = [
                            (p['symbol'],
                             'Sell' if p['side'] == 'Buy' else 'Buy',
                             p['size'],
                             int(p.get('positionIdx', 0)))
                            for p in cur_pair
                        ]
                        place_pair_orders(close_orders, reduce=True)
                        last_close[pid]   = time.time()
                        last_entry_z[pid] = float(ent_z)
                        entry_z_map[pid]  = 0.0
                        entry_spread[pid] = 0.0
                        entry_time[pid]   = 0.0
                        save_state()

            loop_cnt += 1
            if loop_cnt % 15 == 0:
                log(f"🔎 감시중 | {' | '.join(status_report)}")
            time.sleep(1)

        except Exception as e:
            log(f"⚠️ 메인 루프 에러: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
