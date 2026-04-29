import ccxt, pandas as pd, statsmodels.api as sm, time, datetime, json
from pybit.unified_trading import HTTP

# [설정]
API_KEY, SECRET = 'PdCEy7g1TSfU5SY5aQ'.strip(), 'lYE2y2y7c79wG3K0kntZiwgwDwhdHY6mNUHo'.strip()
ex_pub = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'linear'}})
session = HTTP(testnet=False, demo=True, api_key=API_KEY, api_secret=SECRET)
session.endpoint = "https://api-demo.bybit.com"

PRECISION = {'BTCUSDT': 3, 'ETHUSDT': 2, 'XAUUSDT': 2, 'XAGUSDT': 1}
PAIRS = {
    'CRYPTO': {'a': 'BTCUSDT', 'b': 'ETHUSDT', 'val': 10000, 'tar': 12.0, 'ze': 2.5, 'zs_limit': 5.0},
    'METALS': {'a': 'XAUUSDT', 'b': 'XAGUSDT', 'val': 10000, 'tar': 10.0, 'ze': 2.0, 'zs_limit': 4.5}
}
entry_z_map = {p: 0 for p in PAIRS}
last_close = {p: 0 for p in PAIRS}
COOL_DOWN = 900 

def log(msg): print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def get_stats(s1, s2):
    try:
        r1, r2 = ex_pub.fetch_ohlcv(s1, '15m', limit=100), ex_pub.fetch_ohlcv(s2, '15m', limit=100)
        df1, df2 = pd.DataFrame(r1, columns=['ts','o','h','l','c1','v']), pd.DataFrame(r2, columns=['ts','o','h','l','c2','v'])
        df = pd.merge(df1[['ts','c1']], df2[['ts','c2']], on='ts').dropna()
        if len(df) < 50: return None
        y, x = df['c1'], sm.add_constant(df['c2'])
        res = sm.OLS(y, x).fit()
        beta, alpha = res.params.iloc[1], res.params.iloc[0]
        spr = df['c1'] - (alpha + beta * df['c2'])
        return spr.iloc[-1] / spr.std()
    except: return None

def place_order(sym, side, qty, p_idx, reduce=False):
    try:
        qty_str = str(round(float(qty), PRECISION.get(sym, 2)))
        res = session.place_order(category="linear", symbol=sym, side=side.capitalize(),
            orderType="Market", qty=qty_str, positionIdx=p_idx, reduceOnly=reduce)
        if res.get('retCode') == 10001:
            session.place_order(category="linear", symbol=sym, side=side.capitalize(),
                orderType="Market", qty=qty_str, positionIdx=0, reduceOnly=reduce)
        return True
    except: return False

def main():
    log("🚀 Pair Engine v9.0 [Restored] 가동 시작")
    loop_cnt = 0
    while True:
        try:
            res_pos = session.get_positions(category="linear", settleCoin="USDT")
            all_pos = [p for p in res_pos['result']['list'] if float(p.get('size', 0)) > 0]
            status_report = []
            for pid in PAIRS:
                c = PAIRS[pid]
                cur_pair = [p for p in all_pos if p.get('symbol') in [c['a'], c['b']]]
                z = get_stats(c['a'], c['b'])
                z_str = f"{z:.2f}" if z is not None else "N/A"
                if not cur_pair:
                    wait_rem = COOL_DOWN - (time.time() - last_close.get(pid, 0))
                    if wait_rem > 0: status_report.append(f"{pid}: Wait({int(wait_rem/60)}m)")
                    else:
                        status_report.append(f"{pid}: Z={z_str}")
                        if z is not None and abs(z) >= c['ze']:
                            log(f"🎯 {pid} 신호 진입 (Z:{z_str})")
                            entry_z_map[pid] = z
                            tk1, tk2 = ex_pub.fetch_ticker(c['a']), ex_pub.fetch_ticker(c['b'])
                            s1, s2 = ('Sell','Buy') if z > 0 else ('Buy','Sell')
                            place_order(c['a'], s1, (c['val']/2)/tk1['last'], 1 if s1=='Buy' else 2)
                            place_order(c['b'], s2, (c['val']/2)/tk2['last'], 1 if s2=='Buy' else 2)
                else:
                    total_net, has_adv = 0.0, False
                    for p in cur_pair:
                        side, qty, entry_p, curr_p = (1 if p['side'] == 'Buy' else -1), float(p['size']), float(p['avgPrice']), float(p['markPrice'])
                        total_net += ((curr_p - entry_p) * qty * side + float(p.get('curRealisedPnl', 0)) - (curr_p * qty * 0.0006))
                        if (side == 1 and curr_p > entry_p) or (side == -1 and curr_p < entry_p): has_adv = True
                    ent_z = entry_z_map.get(pid, c['ze'] if z > 0 else -c['ze'])
                    status_report.append(f"{pid}: Net({total_net:.1f}$, Z:{z_str})")
                    if (total_net >= c['tar'] and has_adv) or (z is not None and abs(z) >= abs(ent_z + 2.5 if ent_z > 0 else ent_z - 2.5)):
                        log(f"💰 {pid} 청산 (순익:{total_net:.1f}$)")
                        for p in cur_pair: place_order(p['symbol'], 'Sell' if p['side'] == 'Buy' else 'Buy', p['size'], int(p.get('positionIdx', 0)), True)
                        last_close[pid] = time.time()
            loop_cnt += 1
            if loop_cnt % 10 == 0: log(f"🔎 감시중 | {' | '.join(status_report)}")
            time.sleep(1)
        except Exception as e: log(f"⚠️ 에러: {e}"); time.sleep(5)

if __name__ == "__main__": main()