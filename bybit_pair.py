"""
Bybit Pair Trading Bot - v10.0 Final
=====================================
개선 사항 (vs v9.8):
  Q1. 병렬 주문 실행 (threading) + 반쪽 포지션 감지
  Q2. JSON 상태 영속화 + 기동 시 포지션 역산 복구
  Q3. OLS 이원화 창 (500봉 장기 + 100봉 단기) + Beta 괴리 필터
  Q4. 슬리피지 버퍼 반영 + 진입 Limit Chase (PostOnly → 폴백 Market)
  Q5. is_real_profit → 스프레드 회귀 방향성 검증으로 교체
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
    # 크립토: 진입 Z=2.65 / 순익 목표 $20 (슬리피지 버퍼 $8 포함)
    'CRYPTO': {
        'a': 'BTCUSDT', 'b': 'ETHUSDT',
        'val': 10000,
        'tar': 20.0,        # Q4: 기존 12.0 → 슬리피지 버퍼 +8 반영
        'ze':  2.65,
        'slip': 8.0,        # 슬리피지 버퍼 (정보용)
    },
    # 메탈: 진입 Z=2.2 / 순익 목표 $16 (슬리피지 버퍼 $6 포함)
    'METALS': {
        'a': 'XAUUSDT', 'b': 'XAGUSDT',
        'val': 10000,
        'tar': 16.0,        # Q4: 기존 10.0 → 슬리피지 버퍼 +6 반영
        'ze':  2.2,
        'slip': 6.0,
    },
}

COOL_DOWN    = 900          # 쿨타임 15분
BETA_DIV_MAX = 0.15         # Q3: Beta 괴리 허용 한도 15%
COINT_INTERVAL = 3600       # Q3: 공적분 검정 주기 (1시간)
LIMIT_CHASE_BPS  = 3        # Q4: Limit Chase 슬리피지 허용폭 (0.03%)
LIMIT_CHASE_WAIT = 30       # Q4: 지정가 미체결 폴백 대기(초)

STATE_FILE = 'bot_state.json'

# ──────────────────────────────────────────────
# [3] 런타임 상태 (JSON 영속화 대상)
# ──────────────────────────────────────────────
entry_z_map    = {p: 0.0   for p in PAIRS}   # 현재 포지션 진입 Z
entry_spread   = {p: 0.0   for p in PAIRS}   # Q5: 진입 시 스프레드 값
last_entry_z   = {p: 0.0   for p in PAIRS}
last_side      = {p: 0     for p in PAIRS}
last_close     = {p: 0.0   for p in PAIRS}
last_coint_chk = {p: 0.0   for p in PAIRS}   # Q3: 마지막 공적분 검정 시각
coint_ok       = {p: True  for p in PAIRS}   # Q3: 공적분 유효 여부


# ──────────────────────────────────────────────
# [4] 유틸
# ──────────────────────────────────────────────
def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ──────────────────────────────────────────────
# [5] Q2: 상태 영속화
# ──────────────────────────────────────────────
def save_state():
    state = {
        'entry_z_map':  entry_z_map,
        'entry_spread': entry_spread,
        'last_entry_z': last_entry_z,
        'last_side':    last_side,
        'last_close':   last_close,
        'coint_ok':     coint_ok,
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log(f"⚠️ 상태 저장 실패: {e}")


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        entry_z_map.update(s.get('entry_z_map',  {}))
        entry_spread.update(s.get('entry_spread', {}))
        last_entry_z.update(s.get('last_entry_z', {}))
        last_side.update(s.get('last_side',    {}))
        last_close.update(s.get('last_close',  {}))
        coint_ok.update(s.get('coint_ok',    {}))
        log("✅ 상태 복구 완료")
    except FileNotFoundError:
        log("ℹ️  저장 파일 없음, 초기 상태로 시작")
    except Exception as e:
        log(f"⚠️ 상태 로드 실패: {e}")


def verify_state_vs_positions(all_pos):
    """
    Q2: 기동 시 실제 거래소 포지션과 메모리 상태를 비교하여
    entry_z_map이 0인데 포지션이 살아있는 고아 상태를 감지한다.
    """
    for pid, c in PAIRS.items():
        cur_pair = [p for p in all_pos if p.get('symbol') in [c['a'], c['b']]]
        if cur_pair and entry_z_map[pid] == 0:
            log(f"⚠️  [{pid}] 고아 포지션 감지 — entry_z_map=0이나 포지션 존재. "
                f"손절선 fallback 적용 (ze={c['ze']})")
            # fallback: 현재 z 방향으로 진입했다고 가정
            entry_z_map[pid] = c['ze']


# ──────────────────────────────────────────────
# [6] Q3: 이원화 OLS + Beta 괴리 필터 + 공적분 검정
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
    return res.params.iloc[1], res.params.iloc[0]   # beta, alpha


def _calc_z(df, beta, alpha):
    spr = df['c1'] - (alpha + beta * df['c2'])
    std = spr.std()
    if std == 0:
        return None, None
    return spr.iloc[-1] / std, spr.iloc[-1]          # z, raw_spread


def check_coint(pid, s1, s2):
    """공적분 검정 — 1시간마다 실행"""
    now = time.time()
    if now - last_coint_chk[pid] < COINT_INTERVAL:
        return
    try:
        r1 = ex_pub.fetch_ohlcv(s1, '15m', limit=200)
        r2 = ex_pub.fetch_ohlcv(s2, '15m', limit=200)
        df = _merge_df(r1, r2)
        pvalue = ts.coint(df['c1'], df['c2'])[1]
        coint_ok[pid] = pvalue < 0.05
        last_coint_chk[pid] = now
        log(f"🔬 [{pid}] 공적분 검정 p={pvalue:.4f} → {'✅ 유효' if coint_ok[pid] else '❌ 무효'}")
        save_state()
    except Exception as e:
        log(f"⚠️ 공적분 검정 실패 [{pid}]: {e}")


def get_stats_dual(s1, s2, pid):
    """
    장기 500봉 + 단기 100봉 이원화 창.
    Beta 괴리가 15% 초과하면 구조 변화로 판단, None 반환.
    반환: (z, raw_spread) 또는 (None, None)
    """
    try:
        r1_long = ex_pub.fetch_ohlcv(s1, '15m', limit=500)
        r2_long = ex_pub.fetch_ohlcv(s2, '15m', limit=500)
        df_long  = _merge_df(r1_long, r2_long)
        df_short = df_long.tail(100).copy()

        beta_long,  alpha_long  = _calc_beta_alpha(df_long)
        beta_short, alpha_short = _calc_beta_alpha(df_short)

        # Beta 괴리 검사
        divergence = abs(beta_long - beta_short) / (abs(beta_long) + 1e-9)
        if divergence > BETA_DIV_MAX:
            log(f"⚠️ [{pid}] Beta 괴리 {divergence:.1%} > {BETA_DIV_MAX:.0%}, 진입 보류")
            return None, None

        z, raw_spr = _calc_z(df_short, beta_short, alpha_short)
        return z, raw_spr

    except Exception as e:
        log(f"⚠️ get_stats_dual 오류: {e}")
        return None, None


# ──────────────────────────────────────────────
# [7] Q4: Limit Chase 주문 (PostOnly → Market 폴백)
# ──────────────────────────────────────────────
def place_order_limit_chase(sym, side, qty, p_idx, reduce=False):
    """
    진입 주문: PostOnly Limit → 30초 미체결 시 Market 폴백.
    청산 주문: reduceOnly Market (즉시 체결 우선).
    """
    if reduce:
        # 청산은 즉시성 우선 → Market
        return _place_market(sym, side, qty, p_idx, reduce=True)

    # 진입: Limit Chase
    try:
        ticker  = ex_pub.fetch_ticker(sym)
        price   = ticker['last']
        bps     = LIMIT_CHASE_BPS / 10000
        lp      = price * (1 + bps) if side.lower() == 'buy' else price * (1 - bps)
        lp_str  = str(round(lp, PRECISION.get(sym, 2)))
        qty_str = str(round(float(qty), PRECISION.get(sym, 2)))

        res = session.place_order(
            category="linear", symbol=sym,
            side=side.capitalize(), orderType="Limit",
            qty=qty_str, price=lp_str,
            positionIdx=p_idx,
            timeInForce="PostOnly",
            reduceOnly=reduce,
        )
        order_id = res.get('result', {}).get('orderId')
        if not order_id:
            raise ValueError(f"주문 ID 없음: {res}")

        # 체결 대기
        deadline = time.time() + LIMIT_CHASE_WAIT
        while time.time() < deadline:
            time.sleep(2)
            info = session.get_open_orders(category="linear", symbol=sym, orderId=order_id)
            orders = info.get('result', {}).get('list', [])
            if not orders:
                return True   # 체결 완료 (목록에서 사라짐)

        # 미체결 → 취소 후 Market 폴백
        session.cancel_order(category="linear", symbol=sym, orderId=order_id)
        log(f"⚡ Limit 미체결 → Market 폴백 ({sym})")
        return _place_market(sym, side, qty, p_idx, reduce)

    except Exception as e:
        log(f"⚠️ Limit Chase 오류 ({sym}): {e} → Market 폴백")
        return _place_market(sym, side, qty, p_idx, reduce)


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
        log(f"⚠️ Market 주문 오류 ({sym}): {e}")
        return False


# ──────────────────────────────────────────────
# [8] Q1: 병렬 주문 실행
# ──────────────────────────────────────────────
def place_pair_orders(orders: list, reduce=False):
    """
    orders: [(sym, side, qty, p_idx), ...]
    두 다리를 스레드로 동시 실행.
    결과 확인 후 한 쪽만 체결된 반쪽 포지션을 감지한다.
    """
    results = {}

    def worker(sym, side, qty, p_idx):
        results[sym] = place_order_limit_chase(sym, side, qty, p_idx, reduce)

    threads = [threading.Thread(target=worker, args=o) for o in orders]
    for t in threads: t.start()
    for t in threads: t.join()

    failed = [sym for sym, ok in results.items() if not ok]
    if failed:
        log(f"⚠️ 반쪽 포지션 감지! 실패 심볼: {failed} → 성공 다리 즉시 청산 시도")
        success = [sym for sym, ok in results.items() if ok]
        for sym in success:
            # 성공한 다리를 reduceOnly Market으로 즉시 역청산
            side_map = {o[0]: o[1] for o in orders}
            rev_side = 'Sell' if side_map[sym].lower() == 'buy' else 'Buy'
            qty_map  = {o[0]: o[2] for o in orders}
            idx_map  = {o[0]: o[3] for o in orders}
            _place_market(sym, rev_side, qty_map[sym], idx_map[sym], reduce=True)
        return False
    return True


# ──────────────────────────────────────────────
# [9] Q5: 스프레드 회귀 방향성 검증
# ──────────────────────────────────────────────
def check_spread_reversion(pid, z):
    """
    진입 Z의 절반 이상 회귀했는지 확인.
    양수 진입(A숏/B롱) → Z가 낮아져야 수익
    음수 진입(A롱/B숏) → Z가 높아져야 수익
    """
    ent_z = entry_z_map[pid]
    if ent_z == 0 or z is None:
        return False
    if ent_z > 0:
        return z < ent_z * 0.5    # 절반 이상 하락 회귀
    else:
        return z > ent_z * 0.5    # 절반 이상 상승 회귀


# ──────────────────────────────────────────────
# [10] 메인 루프
# ──────────────────────────────────────────────
def main():
    load_state()   # Q2: 기동 시 상태 복구

    log(f"🚀 Pair Engine v10.0 Final 가동 "
        f"(BTC Z={PAIRS['CRYPTO']['ze']} tar=${PAIRS['CRYPTO']['tar']} | "
        f"GOLD Z={PAIRS['METALS']['ze']} tar=${PAIRS['METALS']['tar']})")

    loop_cnt = 0
    while True:
        try:
            # 포지션 조회
            res_pos  = session.get_positions(category="linear", settleCoin="USDT")
            all_pos  = [p for p in res_pos['result']['list'] if float(p.get('size', 0)) > 0]

            # Q2: 최초 1회 고아 포지션 검증
            if loop_cnt == 0:
                verify_state_vs_positions(all_pos)

            status_report = []

            for pid, c in PAIRS.items():
                cur_pair = [p for p in all_pos if p.get('symbol') in [c['a'], c['b']]]

                # Q3: 공적분 검정 (1시간 주기)
                check_coint(pid, c['a'], c['b'])

                # Q3: 이원화 Z-Score 계산
                z, raw_spr = get_stats_dual(c['a'], c['b'], pid)
                z_str = f"{z:.2f}" if z is not None else "N/A"

                # ── 포지션 없음: 진입 로직 ──────────────────
                if not cur_pair:
                    now = time.time()
                    cooldown_ok = (now - last_close[pid] > COOL_DOWN)

                    can_enter = False
                    if z is not None and cooldown_ok and coint_ok[pid]:
                        curr_side = 1 if z > 0 else -1
                        # Better-Z 재진입 필터
                        same_dir_better = (curr_side == last_side[pid] and abs(z) > abs(last_entry_z[pid]))
                        diff_dir        = (curr_side != last_side[pid])
                        if (same_dir_better or diff_dir) and abs(z) >= c['ze']:
                            can_enter = True

                    if can_enter:
                        log(f"🎯 [{pid}] 진입 (Z:{z_str}, 이전Z:{last_entry_z[pid]:.2f})")
                        tk1 = ex_pub.fetch_ticker(c['a'])
                        tk2 = ex_pub.fetch_ticker(c['b'])
                        s1, s2 = ('Sell', 'Buy') if z > 0 else ('Buy', 'Sell')

                        orders = [
                            (c['a'], s1, (c['val']/2)/tk1['last'], 1 if s1=='Buy' else 2),
                            (c['b'], s2, (c['val']/2)/tk2['last'], 1 if s2=='Buy' else 2),
                        ]
                        # Q1: 병렬 Limit Chase 진입
                        ok = place_pair_orders(orders, reduce=False)
                        if ok:
                            entry_z_map[pid]  = z
                            entry_spread[pid] = raw_spr if raw_spr is not None else 0.0
                            last_side[pid]    = curr_side
                            save_state()   # Q2: 진입 후 즉시 저장

                    status_report.append(f"{pid}: Z={z_str}")

                # ── 포지션 있음: 청산 로직 ───────────────────
                else:
                    total_net = 0.0
                    for p in cur_pair:
                        side  = 1 if p['side'] == 'Buy' else -1
                        qty   = float(p['size'])
                        ent_p = float(p['avgPrice'])
                        mk_p  = float(p['markPrice'])
                        # 순수익 = 미실현 + 실현 누적 - 청산 예상 수수료
                        total_net += (
                            (mk_p - ent_p) * qty * side
                            + float(p.get('curRealisedPnl', 0))
                            - (mk_p * qty * 0.0006)
                        )

                    ent_z = entry_z_map[pid] if entry_z_map[pid] != 0 \
                            else (c['ze'] if (z or 0) > 0 else -c['ze'])
                    dynamic_sl = ent_z + 2.5 if ent_z > 0 else ent_z - 2.5

                    # Q5: 스프레드 회귀 방향성 검증
                    spread_ok = check_spread_reversion(pid, z)

                    # 손절 조건
                    stop_loss_hit = (z is not None and abs(z) >= abs(dynamic_sl))

                    status_report.append(
                        f"{pid}: Net={total_net:.1f}$ Z={z_str} "
                        f"SL={dynamic_sl:.2f} rev={'✅' if spread_ok else '❌'}"
                    )

                    # 청산 실행
                    should_close = (total_net >= c['tar'] and spread_ok) or stop_loss_hit
                    if should_close:
                        reason = "익절" if total_net >= c['tar'] else "손절"
                        log(f"💰 [{pid}] {reason} 청산 (순익:{total_net:.1f}$, Z:{z_str})")

                        close_orders = [
                            (p['symbol'],
                             'Sell' if p['side'] == 'Buy' else 'Buy',
                             p['size'],
                             int(p.get('positionIdx', 0)))
                            for p in cur_pair
                        ]
                        # Q1: 병렬 청산 (reduceOnly Market)
                        place_pair_orders(close_orders, reduce=True)

                        last_close[pid]    = time.time()
                        last_entry_z[pid]  = ent_z
                        entry_z_map[pid]   = 0.0
                        entry_spread[pid]  = 0.0
                        save_state()   # Q2: 청산 후 즉시 저장

            loop_cnt += 1
            if loop_cnt % 10 == 0:
                log(f"🔎 감시중 | {' | '.join(status_report)}")
            time.sleep(1)

        except Exception as e:
            log(f"⚠️ 메인 루프 에러: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
