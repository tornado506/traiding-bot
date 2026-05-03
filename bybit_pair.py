"""
Bybit Pair Trading Bot - v11.1 Final
=====================================
v11.0 대비 버그 수정 및 개선:

  [BUG-1] PRECISION 딕셔너리 주석 내 중괄호 정리
  [BUG-2] METALS 휴장 로직 continue → 진입만 차단, 청산은 항상 실행
  [BUG-3] try_entry 호출 시 cur_pair 존재 여부 체크 추가 (유령 2차 진입 방지)
  [BUG-4] API 호출 캐시 도입 (STATS_INTERVAL=60초) — 루프 지연 5~10초 해소
  [BUG-5] calc_dynamic_target XAU/XAG 가격 왜곡 수정 (OLS 잔차 기반 스프레드)
  [BUG-6] MAX_HOLD_HOURS 페어별 분리 (METALS:96h, ALTCOINS:72h, CRYPTO:48h)
  [BUG-7] 시작 로그에 ALTCOINS 페어 추가

  데모 트레이드용 파라미터 조정:
  - COOL_DOWN: 900s → 300s (5분)
  - STATS_INTERVAL: 60s (Z-Score 1분 주기 갱신)
  - CRYPTO ze1/ze2: 1.9/2.4 유지
  - METALS ze1/ze2: 2.2/3.0 유지
  - ALTCOINS ze1/ze2: 2.1/2.8 유지
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

# ══════════════════════════════════════════════════════
# [1] 인스턴스 설정
# ══════════════════════════════════════════════════════
API_KEY = 'PdCEy7g1TSfU5SY5aQ'.strip()
SECRET  = 'lYE2y2y7c79wG3K0kntZiwgwDwhdHY6mNUHo'.strip()

ex_pub  = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'linear'}})
session = HTTP(testnet=False, demo=True, api_key=API_KEY, api_secret=SECRET)
session.endpoint = "https://api-demo.bybit.com"

# [BUG-1] 주석 내 중괄호 정리
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
        'full_val': 8000,
        'half_val': 4000,
        'tar':      40.0,
        'ze1':      1.9,      # 1차 진입 Z
        'ze2':      2.4,      # 2차 진입 Z
        'max_hold': 48,       # [BUG-6] 페어별 Time Stop (시간)
    },
    'METALS': {
        'a':        'XAUUSDT',
        'b':        'XAGUSDT',
        'full_val': 8000,
        'half_val': 4000,
        'tar':      40.0,
        'ze1':      2.2,
        'ze2':      3.0,
        'max_hold': 96,       # [BUG-6] 금/은 회귀 속도 느림 → 96시간
    },
    'ALTCOINS': {
        'a':        'SOLUSDT',
        'b':        'AVAXUSDT',
        'full_val': 8000,
        'half_val': 4000,
        'tar':      40.0,
        'ze1':      2.1,
        'ze2':      2.8,
        'max_hold': 72,       # [BUG-6] 중간 회귀 속도 → 72시간
    },
}

# ── 시스템 파라미터 ──────────────────────────────────
COOL_DOWN         = 900         # [데모] 청산 후 재진입 쿨타임 5분 (실운용: 900)
TAR_OVERRIDE_MULT = 1.5         # 목표 150% 달성 시 spread_ok 무시 익절
COINT_INTERVAL    = 168 * 3600  # 공적분 검정 주기 7일
STATS_INTERVAL    = 300          # [BUG-4] Z-Score 재계산 주기 60초

# 포지션 크기 결정 기준
BETA_DIV_HALF     = 0.20
BETA_DIV_SKIP     = 0.50
COINT_P_HALF      = 0.10
COINT_P_SKIP      = 0.30

# 손절 배율 범위 (변동성 연동)
SL_MULT_MIN       = 1.5
SL_MULT_MAX       = 2.5

STATE_FILE        = 'bot_state.json'

# ══════════════════════════════════════════════════════
# [3] 런타임 상태
# ══════════════════════════════════════════════════════
stage          = {p: 0    for p in PAIRS}
entry_z1       = {p: 0.0  for p in PAIRS}
entry_z2       = {p: 0.0  for p in PAIRS}
entry_z_map    = {p: 0.0  for p in PAIRS}
entry_spread   = {p: 0.0  for p in PAIRS}
entry_time     = {p: 0.0  for p in PAIRS}
entry_val      = {p: 0.0  for p in PAIRS}
last_entry_z   = {p: 0.0  for p in PAIRS}
last_side      = {p: 0    for p in PAIRS}
last_close     = {p: 0.0  for p in PAIRS}
last_coint_chk = {p: 0.0  for p in PAIRS}
coint_pval     = {p: 0.05 for p in PAIRS}

# [BUG-4] Z-Score API 캐시
last_stats_time = {p: 0.0                        for p in PAIRS}
stats_cache     = {p: (None, None, None, None, None) for p in PAIRS}


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
            'stage':        {k: int(v)   for k, v in stage.items()},
            'entry_z1':     {k: float(v) for k, v in entry_z1.items()},
            'entry_z2':     {k: float(v) for k, v in entry_z2.items()},
            'entry_z_map':  {k: float(v) for k, v in entry_z_map.items()},
            'entry_spread': {k: float(v) for k, v in entry_spread.items()},
            'entry_time':   {k: float(v) for k, v in entry_time.items()},
            'entry_val':    {k: float(v) for k, v in entry_val.items()},
            'last_entry_z': {k: float(v) for k, v in last_entry_z.items()},
            'last_side':    {k: int(v)   for k, v in last_side.items()},
            'last_close':   {k: float(v) for k, v in last_close.items()},
            'coint_pval':   {k: float(v) for k, v in coint_pval.items()},
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log(f"⚠️ 상태 저장 에러: {e}")


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        stage.update({k: int(v) for k, v in s.get('stage', {}).items()})
        entry_z1.update(s.get('entry_z1',    {}))
        entry_z2.update(s.get('entry_z2',    {}))
        entry_z_map.update(s.get('entry_z_map',  {}))
        entry_spread.update(s.get('entry_spread', {}))
        entry_time.update(s.get('entry_time',   {}))
        entry_val.update(s.get('entry_val',    {}))
        last_entry_z.update(s.get('last_entry_z', {}))
        last_side.update(s.get('last_side',    {}))
        last_close.update(s.get('last_close',  {}))
        coint_pval.update(s.get('coint_pval',  {}))
        log("✅ 상태 복구 완료")
    except FileNotFoundError:
        log("ℹ️  저장 파일 없음, 초기 상태로 시작")
    except Exception as e:
        log(f"⚠️ 상태 로드 에러: {e}")


def reset_pair_state(pid: str):
    stage[pid]        = 0
    entry_z1[pid]     = 0.0
    entry_z2[pid]     = 0.0
    entry_z_map[pid]  = 0.0
    entry_spread[pid] = 0.0
    entry_time[pid]   = 0.0
    entry_val[pid]    = 0.0


def verify_state_vs_positions(all_pos):
    """기동 시 실제 포지션 방향을 역산하여 상태 복구."""
    for pid, c in PAIRS.items():
        cur_pair = [p for p in all_pos if p.get('symbol') in [c['a'], c['b']]]
        if not cur_pair:
            continue
        if entry_z_map[pid] == 0:
            a_pos = next((p for p in cur_pair if p['symbol'] == c['a']), None)
            if a_pos:
                inferred_sign    = 1 if a_pos['side'] == 'Sell' else -1
                entry_z_map[pid] = float(c['ze2']) * inferred_sign
                entry_z1[pid]    = float(c['ze1']) * inferred_sign
                if entry_time[pid] == 0:
                    entry_time[pid] = time.time() - (c['max_hold'] * 3600 / 2)
                log(f"🔧 [{pid}] 고아 포지션 복구: "
                    f"entry_z={entry_z_map[pid]:.2f} (A={a_pos['side']})")
        if stage[pid] == 0 and cur_pair:
            stage[pid] = 1
            log(f"🔧 [{pid}] stage 복구: 0 → 1")


# ══════════════════════════════════════════════════════
# [6] 지표 계산
# ══════════════════════════════════════════════════════
def _merge_df(r1: list, r2: list) -> pd.DataFrame:
    df = pd.merge(
        pd.DataFrame(r1, columns=['ts','o','h','l','c1','v'])[['ts','c1']],
        pd.DataFrame(r2, columns=['ts','o','h','l','c2','v'])[['ts','c2']],
        on='ts'
    ).dropna()
    return df.reset_index(drop=True)


def _calc_beta_alpha(df: pd.DataFrame):
    y   = df['c1']
    x   = sm.add_constant(df['c2'])
    res = sm.OLS(y, x).fit()
    return float(res.params.iloc[1]), float(res.params.iloc[0])


def _calc_z_spread(df: pd.DataFrame, beta: float, alpha: float):
    spr = df['c1'] - (alpha + beta * df['c2'])
    std = float(spr.std())
    if std < 1e-9:
        return None, None, None
    return float(spr.iloc[-1] / std), float(spr.iloc[-1]), std


def check_coint(pid: str, s1: str, s2: str):
    """공적분 검정 — 주 1회. 결과는 포지션 크기 결정에만 활용."""
    now = time.time()
    if now - last_coint_chk[pid] < COINT_INTERVAL:
        return
    try:
        r1 = ex_pub.fetch_ohlcv(s1, '1h', limit=200)
        r2 = ex_pub.fetch_ohlcv(s2, '1h', limit=200)
        df = _merge_df(r1, r2)
        if len(df) < 100:
            log(f"⚠️ [{pid}] 공적분 검정 데이터 부족 ({len(df)}행)")
            return
        pval = float(ts.coint(df['c1'], df['c2'])[1])
        coint_pval[pid]     = pval
        last_coint_chk[pid] = now
        status = '정상' if pval < COINT_P_HALF else '주의' if pval < COINT_P_SKIP else '경고'
        log(f"🔬 [{pid}] 공적분 p={pval:.4f} ({status})")
        save_state()
    except ccxt.NetworkError as e:
        log(f"🌐 [{pid}] 공적분 네트워크 오류: {e}")
    except Exception as e:
        log(f"⚠️ [{pid}] 공적분 검정 에러: {e}")


def get_stats_dual(s1: str, s2: str, pid: str):
    """
    이중 타임프레임 Z-Score.
    1h 200봉: 신호 방향 / 15m 500봉(장기)+100봉(단기): 타이밍 + 베타 괴리
    반환: (z_15m, z_1h, raw_spread, divergence, df_15m)
    """
    try:
        # 1시간봉 — 방향 결정
        r1_1h = ex_pub.fetch_ohlcv(s1, '1h', limit=200)
        r2_1h = ex_pub.fetch_ohlcv(s2, '1h', limit=200)
        if len(r1_1h) < 100 or len(r2_1h) < 100:
            log(f"⚠️ [{pid}] 1h 데이터 부족")
            return None, None, None, None, None

        df_1h = _merge_df(r1_1h, r2_1h)
        if len(df_1h) < 100:
            return None, None, None, None, None

        beta_1h, alpha_1h = _calc_beta_alpha(df_1h)
        z_1h, _, _        = _calc_z_spread(df_1h, beta_1h, alpha_1h)
        if z_1h is None:
            return None, None, None, None, None

        # 15분봉 — 타이밍 정밀화
        r1_long = ex_pub.fetch_ohlcv(s1, '15m', limit=500)
        r2_long = ex_pub.fetch_ohlcv(s2, '15m', limit=500)
        if len(r1_long) < 150 or len(r2_long) < 150:
            log(f"⚠️ [{pid}] 15m 데이터 부족")
            return None, None, None, None, None

        df_long = _merge_df(r1_long, r2_long)
        if len(df_long) < 150:
            return None, None, None, None, None

        df_15m              = df_long.tail(100).copy().reset_index(drop=True)
        beta_long, _        = _calc_beta_alpha(df_long)
        beta_15m, alpha_15m = _calc_beta_alpha(df_15m)

        divergence        = abs(beta_long - beta_15m) / (abs(beta_long) + 1e-9)
        z_15m, raw_spr, _ = _calc_z_spread(df_15m, beta_15m, alpha_15m)

        if z_15m is None:
            return None, None, None, None, None

        return float(z_15m), float(z_1h), raw_spr, float(divergence), df_15m

    except ccxt.NetworkError as e:
        log(f"🌐 [{pid}] get_stats 네트워크 오류: {e}")
        return None, None, None, None, None
    except ccxt.ExchangeError as e:
        log(f"🏦 [{pid}] get_stats 거래소 오류: {e}")
        return None, None, None, None, None
    except Exception as e:
        log(f"⚠️ [{pid}] get_stats 예외: {e}")
        return None, None, None, None, None


def determine_position_size(pid: str, c: dict, divergence: float) -> tuple:
    """
    공적분 p-value + 베타 괴리율로 포지션 크기 결정.
    'full' / 'half' / 'skip' 반환.
    """
    pval = coint_pval[pid]
    if divergence > BETA_DIV_SKIP or pval > COINT_P_SKIP:
        return 'skip', 0.0
    if divergence > BETA_DIV_HALF or pval > COINT_P_HALF:
        return 'half', c['half_val'] / 2
    return 'full', c['full_val'] / 2


def calc_adaptive_sl(ent_z: float, df_15m: pd.DataFrame) -> tuple:
    """변동성 연동 손절선. 배율 SL_MULT_MIN~SL_MULT_MAX 동적 조정."""
    try:
        recent     = df_15m['c1'].tail(20)
        vol_ratio  = float(recent.std() / (recent.mean() + 1e-9))
        normalized = min(max((vol_ratio - 0.005) / 0.015, 0.0), 1.0)
        vol_mult   = SL_MULT_MIN + normalized * (SL_MULT_MAX - SL_MULT_MIN)
    except Exception:
        vol_mult = (SL_MULT_MIN + SL_MULT_MAX) / 2
    vol_mult = round(vol_mult, 2)
    sl = ent_z + vol_mult if ent_z > 0 else ent_z - vol_mult
    return float(sl), vol_mult


def calc_dynamic_target(pid: str, c: dict, df_15m: pd.DataFrame) -> float:
    """
    [BUG-5] 변동성 연동 수익 목표.
    OLS 잔차 기반 스프레드 표준편차를 사용 — XAU/XAG 가격 왜곡 해소.
    base_tar($40) 기준 ±30% 범위 조정.
    """
    if df_15m is None:
        return c['tar']
    try:
        beta, alpha = _calc_beta_alpha(df_15m)
        residuals   = df_15m['c1'] - (alpha + beta * df_15m['c2'])
        spr_std     = float(residuals.std())
        spr_mean    = float(abs(residuals.mean()))
        vol_ratio   = spr_std / (spr_mean + 1e-9)
        adj         = min(max(vol_ratio / 0.5, 0.7), 1.3)
        return round(c['tar'] * adj, 1)
    except Exception:
        return c['tar']


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
        log(f"⚠️ Market 주문 에러 ({sym} {side}): {e}")
        return False


def place_pair_orders(orders: list, reduce: bool = False) -> bool:
    """두 다리 병렬 실행. 반쪽 체결 시 성공 다리 즉시 역청산."""
    results: dict = {}

    def worker(sym, side, qty, p_idx):
        results[sym] = _place_market(sym, side, qty, p_idx, reduce)

    threads = [threading.Thread(target=worker, args=o) for o in orders]
    for t in threads: t.start()
    for t in threads: t.join()

    failed  = [sym for sym, ok in results.items() if not ok]
    success = [sym for sym, ok in results.items() if ok]

    if failed:
        log(f"⚠️ 반쪽 체결 감지! 실패:{failed} → 역청산")
        side_map = {o[0]: o[1] for o in orders}
        qty_map  = {o[0]: o[2] for o in orders}
        idx_map  = {o[0]: o[3] for o in orders}
        for sym in success:
            rev = 'Sell' if side_map[sym].lower() == 'buy' else 'Buy'
            _place_market(sym, rev, qty_map[sym], idx_map[sym], reduce=True)
        return False
    return True


# ══════════════════════════════════════════════════════
# [8] 청산 조건 판단
# ══════════════════════════════════════════════════════
def check_spread_reversion(pid: str, z: float) -> bool:
    """진입 Z 가중평균의 50% 이상 평균 회귀 여부."""
    ent_z = entry_z_map[pid]
    if ent_z == 0 or z is None:
        return False
    return (z < ent_z * 0.5) if ent_z > 0 else (z > ent_z * 0.5)


def should_close(pid: str, c: dict, total_net: float,
                 z_15m, df_15m, dynamic_tar: float) -> tuple:
    """
    청산 조건 통합 판단.
    1. 정상 익절: total_net >= dynamic_tar AND spread_ok
    2. 강제 익절: total_net >= dynamic_tar * 1.5
    3. 손절: Z >= dynamic_sl (변동성 연동)
    4. Time Stop: 페어별 max_hold 시간 초과
    """
    # [BUG-6] 페어별 max_hold 참조
    max_hold = c.get('max_hold', 48)
    hold_h   = (time.time() - entry_time[pid]) / 3600 if entry_time[pid] > 0 else 0

    if z_15m is None:
        if hold_h >= max_hold:
            return True, f"TimeStop({hold_h:.1f}h, Z계산불가)"
        return False, ""

    spread_ok  = check_spread_reversion(pid, z_15m)
    ent_z      = entry_z_map[pid] if entry_z_map[pid] != 0 \
                 else (c['ze2'] if z_15m > 0 else -c['ze2'])

    dynamic_sl, vol_mult = calc_adaptive_sl(ent_z, df_15m) \
                           if df_15m is not None \
                           else (ent_z + 2.0, 2.0)

    if total_net >= dynamic_tar and spread_ok:
        return True, f"익절(순익:{total_net:.1f}$/{dynamic_tar}$, 회귀✅)"

    if total_net >= dynamic_tar * TAR_OVERRIDE_MULT:
        return True, f"강제익절(순익:{total_net:.1f}$, {TAR_OVERRIDE_MULT}x)"

    if abs(z_15m) >= abs(dynamic_sl):
        return True, f"손절(Z:{z_15m:.2f}≥SL:{dynamic_sl:.2f}, x{vol_mult})"

    if hold_h >= max_hold:
        return True, f"TimeStop({hold_h:.1f}h≥{max_hold}h)"

    return False, ""


# ══════════════════════════════════════════════════════
# [9] 진입 로직 (2단계 분할 진입)
# ══════════════════════════════════════════════════════
def try_entry(pid: str, c: dict, z_15m: float, z_1h: float,
              raw_spr, divergence: float, df_15m: pd.DataFrame,
              cur_pair: list):
    """
    Stage 0 → 1: ze1 도달 시 하프사이즈 1차 진입
    Stage 1 → 2: ze2 도달 시 잔여 하프사이즈 추가 (풀사이즈 완성)
    [BUG-3] cur_pair 인자 추가 — Stage 1에서 포지션 실재 여부 확인
    """
    curr_side  = 1 if z_15m > 0 else -1
    z_abs      = abs(z_15m)
    size_label, _ = determine_position_size(pid, c, divergence)

    if size_label == 'skip':
        return

    # ── Stage 0: 1차 진입 ─────────────────────────────
    if stage[pid] == 0:
        if time.time() - last_close[pid] <= COOL_DOWN:
            return

        # 1h / 15m 방향 일치 확인
        if not ((z_1h > 0 and z_15m > 0) or (z_1h < 0 and z_15m < 0)):
            return

        # Better-Z 재진입 필터
        same_dir_better = (curr_side == last_side[pid] and
                           z_abs > abs(last_entry_z[pid]))
        diff_dir        = (curr_side != last_side[pid])
        if not (same_dir_better or diff_dir):
            return

        if z_abs < c['ze1']:
            return

        half_val = c['half_val'] / 2
        try:
            tk1 = ex_pub.fetch_ticker(c['a'])
            tk2 = ex_pub.fetch_ticker(c['b'])
        except Exception as e:
            log(f"⚠️ [{pid}] 1차 티커 조회 실패: {e}")
            return

        s1, s2 = ('Sell', 'Buy') if z_15m > 0 else ('Buy', 'Sell')
        orders = [
            (c['a'], s1, half_val / safe_float(tk1.get('last'), 1),
             1 if s1 == 'Buy' else 2),
            (c['b'], s2, half_val / safe_float(tk2.get('last'), 1),
             1 if s2 == 'Buy' else 2),
        ]
        log(f"🎯 [{pid}] 1차 진입 Z15m:{z_15m:.2f} Z1h:{z_1h:.2f} "
            f"div:{divergence:.1%} size:{size_label}")
        ok = place_pair_orders(orders, reduce=False)
        if ok:
            stage[pid]        = 1
            entry_z1[pid]     = float(z_15m)
            entry_z_map[pid]  = float(z_15m)
            entry_spread[pid] = safe_float(raw_spr)
            entry_time[pid]   = time.time()
            entry_val[pid]    = float(c['half_val'])
            last_side[pid]    = int(curr_side)
            save_state()

    # ── Stage 1: 2차 진입 ─────────────────────────────
    elif stage[pid] == 1:
        # [BUG-3] 실제 포지션이 있어야만 2차 진입 허용
        if not cur_pair:
            log(f"⚠️ [{pid}] Stage1이나 포지션 없음 — stage 리셋")
            reset_pair_state(pid)
            save_state()
            return

        if entry_val[pid] >= c['full_val'] * 0.99:
            return

        if curr_side != last_side[pid]:
            return

        if z_abs < c['ze2']:
            return

        remain_val = c['full_val'] - entry_val[pid]
        if remain_val < c['half_val'] * 0.5:
            return

        remain_per_leg = remain_val / 2
        try:
            tk1 = ex_pub.fetch_ticker(c['a'])
            tk2 = ex_pub.fetch_ticker(c['b'])
        except Exception as e:
            log(f"⚠️ [{pid}] 2차 티커 조회 실패: {e}")
            return

        s1, s2 = ('Sell', 'Buy') if z_15m > 0 else ('Buy', 'Sell')
        orders = [
            (c['a'], s1, remain_per_leg / safe_float(tk1.get('last'), 1),
             1 if s1 == 'Buy' else 2),
            (c['b'], s2, remain_per_leg / safe_float(tk2.get('last'), 1),
             1 if s2 == 'Buy' else 2),
        ]
        log(f"➕ [{pid}] 2차 진입 Z15m:{z_15m:.2f} → 풀사이즈 완성")
        ok = place_pair_orders(orders, reduce=False)
        if ok:
            stage[pid]      = 2
            entry_z2[pid]   = float(z_15m)
            total_val       = entry_val[pid] + remain_val
            entry_z_map[pid] = float(
                (entry_z1[pid] * entry_val[pid] + z_15m * remain_val) / total_val
            )
            entry_val[pid]  = float(total_val)
            save_state()


# ══════════════════════════════════════════════════════
# [10] 메인 루프
# ══════════════════════════════════════════════════════
def main():
    load_state()

    log("=" * 65)
    log("🚀 Pair Engine v11.1 Final 가동 [데모 모드]")
    # [BUG-7] ALTCOINS 포함 전체 페어 시작 로그
    for pid, c in PAIRS.items():
        log(f"   {pid}: {c['a']}/{c['b']} "
            f"ze1={c['ze1']} ze2={c['ze2']} "
            f"full=${c['full_val']} half=${c['half_val']} "
            f"tar=${c['tar']} maxhold={c['max_hold']}h")
    log(f"   쿨타임={COOL_DOWN}s  Z재계산={STATS_INTERVAL}s")
    log("=" * 65)

    loop_cnt = 0
    df_cache = {p: None for p in PAIRS}  # calc_adaptive_sl / dynamic_target 용

    # 최초 공적분 검정 강제 실행
    for pid in PAIRS:
        last_coint_chk[pid] = 0.0

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

                # ── [BUG-2] METALS 휴장 처리 ──────────────
                # 진입만 차단, 청산/공적분/Z계산은 항상 실행
                metals_closed = False
                if pid == 'METALS':
                    now_dt = datetime.datetime.now()
                    wd, hr = now_dt.weekday(), now_dt.hour
                    metals_closed = (
                        (wd == 5 and hr >= 6) or   # 토 06시~
                        (wd == 6) or               # 일 전체
                        (wd == 0 and hr < 7)       # 월 07시 이전
                    )
                    if metals_closed and not cur_pair and loop_cnt % 60 == 0:
                        log(f"💤 [{pid}] 금/은 휴장 (토06~월07 KST) — 진입 대기")

                # 공적분 검정 (주 1회)
                check_coint(pid, c['a'], c['b'])

                # [BUG-4] Z-Score 캐시 — STATS_INTERVAL마다 갱신
                now_t = time.time()
                if now_t - last_stats_time[pid] >= STATS_INTERVAL:
                    result = get_stats_dual(c['a'], c['b'], pid)
                    stats_cache[pid]     = result
                    last_stats_time[pid] = now_t
                    if result[4] is not None:      # df_15m
                        df_cache[pid] = result[4]

                z_15m, z_1h, raw_spr, divergence, df_15m = stats_cache[pid]

                z_str   = f"{z_15m:.2f}" if z_15m is not None else "N/A"
                z1h_str = f"{z_1h:.2f}"  if z_1h  is not None else "N/A"
                div_str = f"{divergence:.1%}" if divergence is not None else "N/A"

                # ── 진입 로직 ─────────────────────────────
                # [BUG-2] 휴장 시 진입 차단 / [BUG-3] cur_pair 전달
                can_try_entry = (
                    stage[pid] < 2
                    and z_15m is not None
                    and z_1h  is not None
                    and not (pid == 'METALS' and metals_closed)  # 휴장 차단
                )
                if can_try_entry:
                    # Stage 1이면 cur_pair 있어야 진입 시도
                    if stage[pid] == 0 or (stage[pid] == 1 and cur_pair):
                        try_entry(pid, c, z_15m, z_1h,
                                  raw_spr, divergence, df_15m, cur_pair)

                # ── 청산 로직 (휴장 여부 무관, 항상 실행) ──
                if cur_pair:
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

                    hold_h      = (time.time() - entry_time[pid]) / 3600 \
                                  if entry_time[pid] > 0 else 0
                    dynamic_tar = calc_dynamic_target(pid, c, df_cache[pid])
                    close_flag, reason = should_close(
                        pid, c, total_net, z_15m, df_cache[pid], dynamic_tar
                    )

                    ent_z = entry_z_map[pid] if entry_z_map[pid] != 0 \
                            else (c['ze2'] if (z_15m or 0) > 0 else -c['ze2'])
                    dsl, vmult = calc_adaptive_sl(ent_z, df_cache[pid]) \
                                 if df_cache[pid] is not None \
                                 else (ent_z + 2.0, 2.0)

                    spread_ok = check_spread_reversion(pid, z_15m)
                    status_report.append(
                        f"{pid}[S{stage[pid]}] "
                        f"Net={total_net:.1f}$ "
                        f"Z15={z_str} Z1h={z1h_str} "
                        f"SL={dsl:.2f}(x{vmult}) "
                        f"tar={dynamic_tar}$ "
                        f"rev={'✅' if spread_ok else '❌'} "
                        f"hold={hold_h:.1f}h"
                    )

                    if close_flag:
                        log(f"💰 [{pid}] 청산 — {reason}")
                        close_orders = [
                            (p['symbol'],
                             'Sell' if p['side'] == 'Buy' else 'Buy',
                             safe_float(p.get('size')),
                             int(safe_float(p.get('positionIdx'), 0)))
                            for p in cur_pair
                        ]
                        place_pair_orders(close_orders, reduce=True)
                        last_close[pid]   = time.time()
                        last_entry_z[pid] = float(ent_z)
                        reset_pair_state(pid)
                        save_state()

                else:
                    size_label, _ = determine_position_size(
                        pid, c, divergence if divergence is not None else 0.0
                    )
                    closed_mark = "💤" if (pid == 'METALS' and metals_closed) else ""
                    status_report.append(
                        f"{pid}[S{stage[pid]}]{closed_mark} "
                        f"Z15={z_str} Z1h={z1h_str} "
                        f"div={div_str} "
                        f"size={size_label} "
                        f"cp={coint_pval[pid]:.3f}"
                    )

            loop_cnt += 1
            if loop_cnt % 30 == 0:
                log(f"🔎 감시중 | {' | '.join(status_report)}")
            time.sleep(1)

        except Exception as e:
            log(f"⚠️ 메인 루프 에러: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
