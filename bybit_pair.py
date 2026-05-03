"""
Bybit Pair Trading Bot - v11.0 Final
=====================================
v10.4 대비 전략 전면 재설계:

  [NEW-1] 포지션 크기 동적 조절
          - 풀사이즈 $8,000 / 하프사이즈 $4,000
          - 공적분 p-value + 베타 괴리율로 자동 결정
          - 진입 차단이 아닌 크기 조절로 기회 보존

  [NEW-2] 2단계 분할 진입 (Z=2.0 / Z=2.65)
          - 1차: Z=2.0 도달 시 하프사이즈로 선진입
          - 2차: Z=2.65 도달 시 잔여 하프사이즈 추가 (풀사이즈 완성)
          - 사전 계획된 최대 리스크 안에서 평균 진입가 개선

  [NEW-3] 이중 타임프레임 Z-Score
          - 1시간봉 200개(약 8일): 신호 방향 결정
          - 15분봉 100개(약 25시간): 진입 타이밍 정밀화
          - 두 프레임 방향 일치 시에만 진입 허용

  [NEW-4] 공적분 검정 → 주간 검토로 격하
          - 매 시간 게이트 → 168시간(7일) 주기 모니터링
          - 진입 차단 조건에서 제거, 포지션 크기 조절 인자로만 활용
          - 공적분 무효 시에도 하프사이즈로 참여 가능

  [NEW-5] 목표 순익 $40 / R:R 재조정
          - 목표: $40 (슬리피지 버퍼 포함)
          - 손절폭: Entry Z + 변동성 배율 (1.5~2.5 범위로 축소)
          - 예상 R:R 약 1:0.5 이상으로 개선

  [NEW-6] 변동성 연동 수익 목표
          - 진입 시점 스프레드 표준편차 기반으로 목표 동적 조정
          - 고변동성 장세: 목표 상향 / 저변동성 장세: 목표 하향

  [NEW-7] 분할 진입 상태 관리
          - stage: 0(미진입) / 1(1차진입) / 2(풀진입)
          - 각 단계별 진입 Z, 진입 시각, 포지션 크기 별도 추적
          - JSON 영속화로 재시작 시에도 단계 정확히 복구

  기존 유지:
  - 병렬 주문 + 반쪽 체결 역청산 [FIX-4]
  - 포지션 방향 역산 복구 [FIX-5]
  - 예외 세분화 [FIX-6]
  - 48시간 Time Stop [FIX-8]
  - Better-Z 재진입 필터 [FIX-2]
  - Numpy/JSON 타입 보정
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

PRECISION = {'BTCUSDT': 3, 'ETHUSDT': 2, 'XAUUSDT': 2, 'XAGUSDT': 1}

# ══════════════════════════════════════════════════════
# [2] 전략 파라미터
# ══════════════════════════════════════════════════════
PAIRS = {
    'CRYPTO': {
        'a':        'BTCUSDT',
        'b':        'ETHUSDT',
        'full_val': 8000,       # [NEW-1] 풀사이즈 포지션 총액 ($)
        'half_val': 4000,       # [NEW-1] 하프사이즈 포지션 총액 ($)
        'tar':      40.0,       # [NEW-5] 목표 순익 ($) — 슬리피지 버퍼 포함
        'ze1':      2.6,        # [NEW-2] 1차 진입 Z 임계값
        'ze2':      3.0,       # [NEW-2] 2차 진입 Z 임계값 (풀사이즈 완성)
    },
    'METALS': {
        'a':        'XAUUSDT',
        'b':        'XAGUSDT',
        'full_val': 8000,
        'half_val': 4000,
        'tar':      40.0,
        'ze1':      2.2,        # 메탈은 변동성 낮으므로 1차 진입 임계값 소폭 하향
        'ze2':      2.6,
    },
}

# ── 시스템 파라미터 ──────────────────────────────────
COOL_DOWN         = 900         # 청산 후 재진입 쿨타임 (초, 15분)
MAX_HOLD_HOURS    = 48          # Time Stop 최대 보유 시간
TAR_OVERRIDE_MULT = 1.5         # 목표 150% 달성 시 spread_ok 무시 익절

# [NEW-4] 공적분 검정 주기: 매 시간 → 주 1회
COINT_INTERVAL    = 168 * 3600  # 168시간 = 7일

# [NEW-1] 포지션 크기 결정 기준
BETA_DIV_HALF     = 0.20        # 베타 괴리 20% 초과 → 하프사이즈
BETA_DIV_SKIP     = 0.50        # 베타 괴리 50% 초과 → 완전 차단 (구조 붕괴 수준)
COINT_P_HALF      = 0.10        # 공적분 p-value > 0.10 → 하프사이즈
COINT_P_SKIP      = 0.30        # 공적분 p-value > 0.30 → 완전 차단

# [NEW-5] 손절 배율 범위 (변동성 연동)
SL_MULT_MIN       = 1.5         # 저변동성: Entry Z + 1.5
SL_MULT_MAX       = 2.5         # 고변동성: Entry Z + 2.5

STATE_FILE        = 'bot_state.json'

# ══════════════════════════════════════════════════════
# [3] 런타임 상태
# ══════════════════════════════════════════════════════
# 분할 진입 단계: 0=미진입 / 1=1차진입(하프) / 2=풀진입
stage          = {p: 0    for p in PAIRS}

# 1차/2차 진입 Z (단계별 별도 추적)
entry_z1       = {p: 0.0  for p in PAIRS}   # 1차 진입 Z
entry_z2       = {p: 0.0  for p in PAIRS}   # 2차 진입 Z (0이면 미실행)

# 현재 포지션 진입 Z (청산 기준용 — 가중평균)
entry_z_map    = {p: 0.0  for p in PAIRS}

entry_spread   = {p: 0.0  for p in PAIRS}   # 진입 시 스프레드 raw값
entry_time     = {p: 0.0  for p in PAIRS}   # 최초 진입 시각
entry_val      = {p: 0.0  for p in PAIRS}   # 현재 투입 총액 (하프/풀 추적)
last_entry_z   = {p: 0.0  for p in PAIRS}   # 직전 청산 포지션 진입 Z
last_side      = {p: 0    for p in PAIRS}   # 직전 진입 방향 (1/-1)
last_close     = {p: 0.0  for p in PAIRS}   # 마지막 청산 시각
last_coint_chk = {p: 0.0  for p in PAIRS}   # 마지막 공적분 검정 시각
coint_pval     = {p: 0.05 for p in PAIRS}   # 마지막 공적분 p-value (기본: 유효)


# ══════════════════════════════════════════════════════
# [4] 유틸
# ══════════════════════════════════════════════════════
def log(msg: str):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def safe_float(v, default=0.0) -> float:
    """None/예외 안전 float 변환"""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════
# [5] 상태 영속화
# ══════════════════════════════════════════════════════
def save_state():
    """모든 런타임 상태를 JSON으로 저장. Numpy 타입 → Python 기본 타입 변환."""
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
        stage.update({k: int(v)   for k, v in s.get('stage', {}).items()})
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
    """청산 후 해당 페어 상태 초기화"""
    stage[pid]         = 0
    entry_z1[pid]      = 0.0
    entry_z2[pid]      = 0.0
    entry_z_map[pid]   = 0.0
    entry_spread[pid]  = 0.0
    entry_time[pid]    = 0.0
    entry_val[pid]     = 0.0


def verify_state_vs_positions(all_pos):
    """
    기동 시 실제 포지션 방향을 역산하여 상태 복구.
    A종목 side(Buy/Sell)로 진입 방향 추론 → entry_z_map 부호 결정.
    """
    for pid, c in PAIRS.items():
        cur_pair = [p for p in all_pos if p.get('symbol') in [c['a'], c['b']]]
        if not cur_pair:
            continue
        if entry_z_map[pid] == 0:
            a_pos = next((p for p in cur_pair if p['symbol'] == c['a']), None)
            if a_pos:
                inferred_sign  = 1 if a_pos['side'] == 'Sell' else -1
                entry_z_map[pid] = float(c['ze2']) * inferred_sign
                entry_z1[pid]    = float(c['ze1']) * inferred_sign
                if entry_time[pid] == 0:
                    entry_time[pid] = time.time() - (MAX_HOLD_HOURS * 3600 / 2)
                log(f"🔧 [{pid}] 고아 포지션 복구: "
                    f"entry_z={entry_z_map[pid]:.2f} (A={a_pos['side']})")
        # stage 복구: 포지션이 있으면 최소 1단계
        if stage[pid] == 0 and cur_pair:
            stage[pid] = 1
            log(f"🔧 [{pid}] stage 복구: 0 → 1 (포지션 감지)")


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
    """
    [NEW-4] 공적분 검정 — 주 1회(168시간) 주기.
    결과는 coint_pval에 저장되며 포지션 크기 결정에만 활용.
    진입 자체를 차단하지 않음.
    """
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
        log(f"🔬 [{pid}] 주간 공적분 검정 p={pval:.4f} "
            f"({'정상' if pval < COINT_P_HALF else '주의' if pval < COINT_P_SKIP else '경고'})")
        save_state()
    except ccxt.NetworkError as e:
        log(f"🌐 [{pid}] 공적분 검정 네트워크 오류: {e}")
    except Exception as e:
        log(f"⚠️ [{pid}] 공적분 검정 에러: {e}")


def get_stats_dual(s1: str, s2: str, pid: str):
    """
    [NEW-3] 이중 타임프레임 Z-Score 계산.
      - 1시간봉 200개: 신호 방향 결정 (큰 그림)
      - 15분봉 100개 : 진입 타이밍 정밀화 (세밀한 타이밍)
      - 두 프레임 Z 방향이 일치해야 신호 유효

    반환: (z_15m, z_1h, raw_spread, divergence, df_15m)
      모든 값이 None이면 계산 실패.
    """
    try:
        # ── 1시간봉 (방향 결정) ────────────────────────────
        r1_1h = ex_pub.fetch_ohlcv(s1, '1h', limit=200)
        r2_1h = ex_pub.fetch_ohlcv(s2, '1h', limit=200)

        if len(r1_1h) < 100 or len(r2_1h) < 100:
            log(f"⚠️ [{pid}] 1h 데이터 부족 ({len(r1_1h)}/{len(r2_1h)}봉)")
            return None, None, None, None, None

        df_1h = _merge_df(r1_1h, r2_1h)
        if len(df_1h) < 100:
            log(f"⚠️ [{pid}] 1h 병합 후 데이터 부족 ({len(df_1h)}행)")
            return None, None, None, None, None

        beta_1h, alpha_1h = _calc_beta_alpha(df_1h)
        z_1h, _, _        = _calc_z_spread(df_1h, beta_1h, alpha_1h)
        if z_1h is None:
            return None, None, None, None, None

        # ── 15분봉 (타이밍 정밀화) ─────────────────────────
        # 500봉 장기로 베타 괴리 계산, 단기 100봉으로 Z 계산
        r1_long = ex_pub.fetch_ohlcv(s1, '15m', limit=500)
        r2_long = ex_pub.fetch_ohlcv(s2, '15m', limit=500)

        if len(r1_long) < 150 or len(r2_long) < 150:
            log(f"⚠️ [{pid}] 15m 데이터 부족 ({len(r1_long)}/{len(r2_long)}봉)")
            return None, None, None, None, None

        df_long  = _merge_df(r1_long, r2_long)
        if len(df_long) < 150:
            return None, None, None, None, None

        df_15m        = df_long.tail(100).copy().reset_index(drop=True)
        beta_long, _  = _calc_beta_alpha(df_long)
        beta_15m, alpha_15m = _calc_beta_alpha(df_15m)

        divergence = abs(beta_long - beta_15m) / (abs(beta_long) + 1e-9)
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
    [NEW-1] 공적분 p-value + 베타 괴리율로 포지션 크기 결정.
    진입 차단이 아닌 크기 조절로 기회 보존.

    반환: (size_label, val_per_leg)
      'full'  → 풀사이즈, val_per_leg = full_val / 2
      'half'  → 하프사이즈, val_per_leg = half_val / 2
      'skip'  → 완전 차단 (구조 붕괴 수준)
    """
    pval = coint_pval[pid]

    # 완전 차단 조건 (구조 붕괴 수준)
    if divergence > BETA_DIV_SKIP:
        return 'skip', 0.0
    if pval > COINT_P_SKIP:
        return 'skip', 0.0

    # 하프사이즈 조건
    if divergence > BETA_DIV_HALF or pval > COINT_P_HALF:
        return 'half', c['half_val'] / 2

    # 풀사이즈
    return 'full', c['full_val'] / 2


def calc_adaptive_sl(ent_z: float, df_15m: pd.DataFrame) -> tuple:
    """
    [NEW-5] 변동성 연동 손절선.
    최근 20봉 가격 변동성 → 손절 배율 SL_MULT_MIN~SL_MULT_MAX 범위 동적 조정.
    반환: (dynamic_sl, vol_mult)
    """
    try:
        recent    = df_15m['c1'].tail(20)
        vol_ratio = float(recent.std() / (recent.mean() + 1e-9))
        # 정규화: vol_ratio 0.005(저) ~ 0.02(고) 기준
        normalized = min(max((vol_ratio - 0.005) / 0.015, 0.0), 1.0)
        vol_mult   = SL_MULT_MIN + normalized * (SL_MULT_MAX - SL_MULT_MIN)
    except Exception:
        vol_mult = (SL_MULT_MIN + SL_MULT_MAX) / 2

    vol_mult = round(vol_mult, 2)
    sl = ent_z + vol_mult if ent_z > 0 else ent_z - vol_mult
    return float(sl), vol_mult


def calc_dynamic_target(pid: str, c: dict, entry_spread_val: float,
                        df_15m: pd.DataFrame) -> float:
    """
    [NEW-6] 변동성 연동 수익 목표.
    진입 시 스프레드 표준편차 기반으로 목표 동적 조정.
    base_tar($40)를 중심으로 ±30% 범위에서 조정.
    """
    try:
        spr_series = df_15m['c1'] - df_15m['c2']  # 근사 스프레드
        spr_std    = float(spr_series.std())
        spr_mean   = float(spr_series.mean())
        vol_ratio  = spr_std / (abs(spr_mean) + 1e-9)

        # 기준 대비 변동성 배율 (0.7 ~ 1.3 범위로 클리핑)
        adj = min(max(vol_ratio / 0.01, 0.7), 1.3)
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
        # positionIdx 오류 시 0으로 폴백
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
    """
    두 다리를 threading으로 병렬 실행.
    반쪽 체결 감지 시 성공 다리 즉시 역청산.
    orders: [(sym, side, qty, p_idx), ...]
    """
    results: dict = {}

    def worker(sym, side, qty, p_idx):
        results[sym] = _place_market(sym, side, qty, p_idx, reduce)

    threads = [threading.Thread(target=worker, args=o) for o in orders]
    for t in threads: t.start()
    for t in threads: t.join()

    failed  = [sym for sym, ok in results.items() if not ok]
    success = [sym for sym, ok in results.items() if ok]

    if failed:
        log(f"⚠️ 반쪽 체결 감지! 실패:{failed} → 성공 다리 역청산 중...")
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
    """진입 Z(가중평균)의 50% 이상 평균 회귀 여부."""
    ent_z = entry_z_map[pid]
    if ent_z == 0 or z is None:
        return False
    return (z < ent_z * 0.5) if ent_z > 0 else (z > ent_z * 0.5)


def should_close(pid: str, c: dict, total_net: float,
                 z_15m, df_15m, dynamic_tar: float) -> tuple:
    """
    청산 조건 통합 판단.
    반환: (close_flag: bool, reason: str)

    트리거:
      1. 정상 익절: total_net >= dynamic_tar AND spread_ok
      2. 강제 익절: total_net >= dynamic_tar * 1.5 (spread_ok 무관)
      3. 손절: Z가 dynamic_sl 초과 (변동성 연동)
      4. Time Stop: 48시간 초과
      (공적분은 포지션 크기 조절 인자로만 사용, 청산 조건 제거)
    """
    if z_15m is None:
        # Z 계산 실패 시 Time Stop만 평가
        hold_h = (time.time() - entry_time[pid]) / 3600 if entry_time[pid] > 0 else 0
        if hold_h >= MAX_HOLD_HOURS:
            return True, f"TimeStop({hold_h:.1f}h, Z계산불가)"
        return False, ""

    spread_ok = check_spread_reversion(pid, z_15m)
    ent_z     = entry_z_map[pid] if entry_z_map[pid] != 0 \
                else (c['ze2'] if z_15m > 0 else -c['ze2'])

    dynamic_sl, vol_mult = calc_adaptive_sl(ent_z, df_15m) \
                           if df_15m is not None \
                           else (ent_z + 2.0, 2.0)

    hold_h = (time.time() - entry_time[pid]) / 3600 if entry_time[pid] > 0 else 0

    # 조건 순서: 익절 → 강제익절 → 손절 → 시간초과
    if total_net >= dynamic_tar and spread_ok:
        return True, f"익절(순익:{total_net:.1f}$/{dynamic_tar}$, 회귀✅)"

    if total_net >= dynamic_tar * TAR_OVERRIDE_MULT:
        return True, f"강제익절(순익:{total_net:.1f}$, {TAR_OVERRIDE_MULT}x)"

    if abs(z_15m) >= abs(dynamic_sl):
        return True, f"손절(Z:{z_15m:.2f}≥SL:{dynamic_sl:.2f}, x{vol_mult})"

    if hold_h >= MAX_HOLD_HOURS:
        return True, f"TimeStop({hold_h:.1f}h≥{MAX_HOLD_HOURS}h)"

    return False, ""


# ══════════════════════════════════════════════════════
# [9] 진입 로직 (분할 진입 핵심)
# ══════════════════════════════════════════════════════
def try_entry(pid: str, c: dict, z_15m: float, z_1h: float,
              raw_spr: float, divergence: float, df_15m: pd.DataFrame):
    """
    [NEW-2] 2단계 분할 진입 로직.

    Stage 0 → 1차 진입 (Z1 도달, 하프사이즈):
      - ze1 이상 도달
      - 1h / 15m 방향 일치
      - 쿨타임 통과
      - Better-Z 필터 통과
      - 포지션 크기 결정 (풀/하프/스킵)

    Stage 1 → 2차 진입 (Z2 도달, 잔여 하프사이즈):
      - 1차 진입 방향과 동일 방향으로 Z가 더 벌어짐
      - ze2 이상 도달
      - 이미 풀사이즈면 추가 진입 안 함
    """
    curr_side  = 1 if z_15m > 0 else -1
    z_abs      = abs(z_15m)
    size_label, val_per_leg = determine_position_size(pid, c, divergence)

    if size_label == 'skip':
        return  # 구조 붕괴 수준 → 완전 차단

    # ── Stage 0: 1차 진입 시도 ────────────────────────
    if stage[pid] == 0:
        now         = time.time()
        cooldown_ok = (now - last_close[pid] > COOL_DOWN)
        if not cooldown_ok:
            return

        # 1h / 15m 방향 일치 확인 [NEW-3]
        direction_match = (
            (z_1h > 0 and z_15m > 0) or
            (z_1h < 0 and z_15m < 0)
        )
        if not direction_match:
            return

        # Better-Z 재진입 필터
        same_dir_better = (
            curr_side == last_side[pid] and
            z_abs > abs(last_entry_z[pid])
        )
        diff_dir = (curr_side != last_side[pid])
        if not (same_dir_better or diff_dir):
            return

        # 1차 진입 임계값 확인
        if z_abs < c['ze1']:
            return

        # 1차는 항상 하프사이즈 (size_label 무관)
        half_val = c['half_val'] / 2
        try:
            tk1 = ex_pub.fetch_ticker(c['a'])
            tk2 = ex_pub.fetch_ticker(c['b'])
        except Exception as e:
            log(f"⚠️ [{pid}] 1차 진입 티커 조회 실패: {e}")
            return

        s1, s2 = ('Sell', 'Buy') if z_15m > 0 else ('Buy', 'Sell')
        orders = [
            (c['a'], s1, half_val / safe_float(tk1.get('last'), 1),
             1 if s1 == 'Buy' else 2),
            (c['b'], s2, half_val / safe_float(tk2.get('last'), 1),
             1 if s2 == 'Buy' else 2),
        ]
        log(f"🎯 [{pid}] 1차 진입 (Z15m:{z_15m:.2f} Z1h:{z_1h:.2f} "
            f"div:{divergence:.1%} size:half {size_label}표시)")
        ok = place_pair_orders(orders, reduce=False)
        if ok:
            stage[pid]         = 1
            entry_z1[pid]      = float(z_15m)
            entry_z_map[pid]   = float(z_15m)   # 초기 가중평균 = 1차 진입 Z
            entry_spread[pid]  = safe_float(raw_spr)
            entry_time[pid]    = time.time()
            entry_val[pid]     = float(c['half_val'])
            last_side[pid]     = int(curr_side)
            save_state()

    # ── Stage 1: 2차 진입 시도 ────────────────────────
    elif stage[pid] == 1:
        # 이미 풀사이즈 투입됐으면 추가 진입 안 함
        if entry_val[pid] >= c['full_val'] * 0.99:
            return

        # 같은 방향으로 Z가 더 벌어졌는지 확인
        same_dir = (curr_side == last_side[pid])
        if not same_dir:
            return

        # 2차 진입 임계값 확인
        if z_abs < c['ze2']:
            return

        # 2차 잔여: full_val - 이미 투입된 half_val
        remain_val = c['full_val'] - entry_val[pid]
        if remain_val < c['half_val'] * 0.5:
            return  # 잔여 너무 작으면 생략

        remain_per_leg = remain_val / 2
        try:
            tk1 = ex_pub.fetch_ticker(c['a'])
            tk2 = ex_pub.fetch_ticker(c['b'])
        except Exception as e:
            log(f"⚠️ [{pid}] 2차 진입 티커 조회 실패: {e}")
            return

        s1, s2 = ('Sell', 'Buy') if z_15m > 0 else ('Buy', 'Sell')
        orders = [
            (c['a'], s1, remain_per_leg / safe_float(tk1.get('last'), 1),
             1 if s1 == 'Buy' else 2),
            (c['b'], s2, remain_per_leg / safe_float(tk2.get('last'), 1),
             1 if s2 == 'Buy' else 2),
        ]
        log(f"➕ [{pid}] 2차 진입 (Z15m:{z_15m:.2f} → 풀사이즈 완성 "
            f"size_label:{size_label})")
        ok = place_pair_orders(orders, reduce=False)
        if ok:
            stage[pid]       = 2
            entry_z2[pid]    = float(z_15m)
            # 가중평균 Z 업데이트 (투입 금액 가중)
            total_val = entry_val[pid] + remain_val
            entry_z_map[pid] = float(
                (entry_z1[pid] * entry_val[pid] + z_15m * remain_val) / total_val
            )
            entry_val[pid]   = float(total_val)
            save_state()


# ══════════════════════════════════════════════════════
# [10] 메인 루프
# ══════════════════════════════════════════════════════
def main():
    load_state()
    log("=" * 60)
    log("🚀 Pair Engine v11.0 Final 가동")
    log(f"   CRYPTO: ze1={PAIRS['CRYPTO']['ze1']} ze2={PAIRS['CRYPTO']['ze2']} "
        f"full=${PAIRS['CRYPTO']['full_val']} half=${PAIRS['CRYPTO']['half_val']} "
        f"tar=${PAIRS['CRYPTO']['tar']}")
    log(f"   METALS: ze1={PAIRS['METALS']['ze1']} ze2={PAIRS['METALS']['ze2']} "
        f"full=${PAIRS['METALS']['full_val']} half=${PAIRS['METALS']['half_val']} "
        f"tar=${PAIRS['METALS']['tar']}")
    log("=" * 60)

    loop_cnt      = 0
    df_cache      = {p: None for p in PAIRS}   # df_15m 캐시 (adaptive SL용)

    # 최초 공적분 검정 강제 실행
    for pid, c in PAIRS.items():
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

                # ─── [수정] 금/은 시장 거래 시간 엄수 로직 (KST 기준) ──────────
                if pid == 'METALS' and not cur_pair:
                    now = datetime.datetime.now()
                    wd = now.weekday()  # 0:월, 4:금, 5:토, 6:일
                    hr = now.hour
                    
                    # 토요일(5) 06시부터 ~ 일요일(6) 전체 ~ 월요일(0) 07시 이전까지 차단
                    is_market_closed = (wd == 5 and hr >= 6) or (wd == 6) or (wd == 0 and hr < 7)
                    
                    if is_market_closed:
                        if loop_cnt % 60 == 0:
                            log(f"💤 [{pid}] 금/은 시장 휴장 (토06시~월07시) - 진입 대기 중")
                        continue

                # 공적분 검정 (주 1회)
                check_coint(pid, c['a'], c['b'])

                # 이중 타임프레임 Z-Score 계산
                z_15m, z_1h, raw_spr, divergence, df_15m = \
                    get_stats_dual(c['a'], c['b'], pid)

                # df 캐시 업데이트
                if df_15m is not None:
                    df_cache[pid] = df_15m

                z_str    = f"{z_15m:.2f}" if z_15m is not None else "N/A"
                z1h_str  = f"{z_1h:.2f}"  if z_1h  is not None else "N/A"
                div_str  = f"{divergence:.1%}" if divergence is not None else "N/A"

                # ── 포지션 없음 / Stage 0~1: 진입 로직 ───────
                if stage[pid] < 2 and z_15m is not None and z_1h is not None:
                    try_entry(pid, c, z_15m, z_1h, raw_spr, divergence, df_15m)

                # ── 포지션 있음: 청산 로직 ────────────────────
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

                    hold_h = (time.time() - entry_time[pid]) / 3600 \
                             if entry_time[pid] > 0 else 0

                    # 변동성 연동 목표 순익 계산
                    dynamic_tar = calc_dynamic_target(
                        pid, c, entry_spread[pid], df_cache[pid]
                    )

                    close_flag, reason = should_close(
                        pid, c, total_net, z_15m, df_cache[pid], dynamic_tar
                    )

                    # 손절선 표시용
                    ent_z = entry_z_map[pid] if entry_z_map[pid] != 0 \
                            else (c['ze2'] if (z_15m or 0) > 0 else -c['ze2'])
                    dsl, vmult = calc_adaptive_sl(ent_z, df_cache[pid]) \
                                 if df_cache[pid] is not None else (ent_z + 2.0, 2.0)

                    spread_ok = check_spread_reversion(pid, z_15m)
                    status_report.append(
                        f"{pid}[S{stage[pid]}] "
                        f"Net={total_net:.1f}$ "
                        f"Z15m={z_str} Z1h={z1h_str} "
                        f"SL={dsl:.2f}(x{vmult}) "
                        f"tar={dynamic_tar}$ "
                        f"rev={'✅' if spread_ok else '❌'} "
                        f"hold={hold_h:.1f}h "
                        f"div={div_str}"
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
                    # 포지션 없음 상태 표시
                    size_label, _ = determine_position_size(
                        pid, c, divergence if divergence is not None else 0.0
                    )
                    status_report.append(
                        f"{pid}[S{stage[pid]}] "
                        f"Z15m={z_str} Z1h={z1h_str} "
                        f"div={div_str} "
                        f"size={size_label} "
                        f"coint_p={coint_pval[pid]:.3f}"
                    )

            loop_cnt += 1
            if loop_cnt % 15 == 0:
                log(f"🔎 감시중 | {' | '.join(status_report)}")
            time.sleep(1)

        except Exception as e:
            log(f"⚠️ 메인 루프 에러: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
