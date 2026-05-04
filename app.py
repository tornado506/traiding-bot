import io, os, re, time
from datetime import datetime
import pandas as pd
import streamlit as st
from pybit.unified_trading import HTTP
from streamlit_autorefresh import st_autorefresh

# [1. 설정 및 연결]
# 계정 1: 메인 봇 (bybit7.py / 원금 $50,000)
K1, S1 = "O1QpHJH4wdsjPiCe1x", "XdHD6eGaNz1iSnLF0j6nVEETZMDiSvGnai75"
ss1 = HTTP(testnet=False, demo=True, api_key=K1, api_secret=S1)
ss1.endpoint = "https://api-demo.bybit.com"

# 계정 2: 페어 봇 (bybit_pair.py / 원금 $5,000)
K2, S2 = "PdCEy7g1TSfU5SY5aQ", "lYE2y2y7c79wG3K0kntZiwgwDwhdHY6mNUHo"
ss2 = HTTP(testnet=False, demo=True, api_key=K2, api_secret=S2)
ss2.endpoint = "https://api-demo.bybit.com"

START_CASH_MAIN = 50000.0
START_CASH_SUB  = 5000.0

# 로그 파일 경로 설정
LOG_FILES = {"MAIN_LOG": "bybit7.log", "PAIR_LOG": "pair_bot.log"}
FEE_RATE = 0.0006 

st.set_page_config(page_title="Sniper Bot Center v13.0", layout="wide")
# 5초마다 실시간 업데이트
st_autorefresh(interval=5000, key="refresh_v13_final")

# [2. CSS 스타일]
st.markdown("""
<style>
.stApp {background-color: #0e1117 !important;}
[data-testid="stHeader"], [data-testid="stToolbar"] {display: none !important;}
.block-container {padding-top: 1rem !important; max-width: 95% !important; margin: 0 auto !important;}
* {font-family: 'Courier New', monospace; color: #ffffff !important;}

/* 코드박스/흰색 하이라이트 버그 완전 차단 */
code, pre { background-color: transparent !important; border: none !important; color: inherit !important; padding: 0 !important; margin: 0 !important; }

.total-balance-card {
    background: linear-gradient(135deg, #1e2631 0%, #0e1117 100%);
    padding: 25px; border-radius: 15px; text-align: center;
    border: 1px solid #3e4452; margin-bottom: 30px;
}
.status-container {
    background-color: #161b22 !important;
    padding: 20px; border-radius: 12px;
    border: 1px solid #30363d !important;
    margin-bottom: 30px;
}
.pos-row {
    display: flex; align-items: center;
    padding: 8px 0; border-bottom: 1px solid #222;
    font-size: 14px;
}
.col-time { width: 150px; color: #8b949e; }
.col-sym  { width: 190px; font-weight: bold; }
.col-net  { width: 160px; text-align: right; margin-right: 25px; }
.col-qty  { width: 140px; }
.col-avg  { width: 160px; }

.log-box {
    background-color: #000000 !important;
    color: #00ffc8 !important;
    padding: 15px; border-radius: 8px;
    white-space: pre-wrap; font-size: 13px; line-height: 1.5;
    height: 450px; overflow-y: auto; border: 1px solid #1a1c24;
    margin-bottom: 25px;
}
.pair-box { color: #ff9800 !important; border-left: 4px solid #ff9800 !important; }
.profit-plus { color: #00ffc8 !important; font-weight: bold; }
.profit-minus { color: #ff4b4b !important; font-weight: bold; }
.rsi-tag { color: #00ffc8 !important; font-weight: bold; margin-right: 20px; font-size: 15px; }
.alive-tag { color: #00ffc8 !important; font-size: 14px; margin-left: 15px; font-weight: bold; }
.dead-tag { color: #ff4b4b !important; font-size: 14px; margin-left: 15px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- [3] 데이터 처리 함수 ---

def check_bot_alive(file_name):
    """
    pgrep 명령어를 사용하여 시스템 전체에서 해당 파일이 실행 중인지 확인.
    리눅스 서버에서 프로세스를 감시하는 가장 확실한 방법입니다.
    """
    try:
        # pgrep -f 는 파일명이 포함된 전체 실행 커맨드를 검색합니다.
        exit_code = os.system(f"pgrep -f {file_name} > /dev/null")
        return exit_code == 0 # 0이면 살아있음, 그 외엔 죽었음
    except:
        return False

def get_rsi_live(symbol):
    try:
        r = ss1.get_kline(category="linear", symbol=symbol, interval="15", limit=100)["result"]["list"]
        df = pd.DataFrame(r, columns=["ts","o","h","l","c","v","t"]).apply(pd.to_numeric).iloc[::-1]
        delta = df['c'].diff()
        gain = delta.where(delta > 0, 0); loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        rsi = 100 - (100 / (1 + (avg_gain / (avg_loss + 1e-9))))
        return round(rsi.iloc[-1], 1)
    except: return "--"

def get_profit_data():
    try:
        b1 = ss1.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        c1 = float(b1['result']['list'][0]['coin'][0]['walletBalance'])
        p1 = ss1.get_positions(category="linear", settleCoin="USDT")['result']['list']
        u1 = sum(float(pos.get('unrealisedPnl', 0)) for pos in p1 if float(pos.get("size", 0)) > 0)
        
        b2 = ss2.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        c2 = float(b2['result']['list'][0]['coin'][0]['walletBalance'])
        p2 = ss2.get_positions(category="linear", settleCoin="USDT")['result']['list']
        u2 = sum(float(pos.get('unrealisedPnl', 0)) for pos in p2 if float(pos.get("size", 0)) > 0)
        
        return (c1+u1), (c1+u1-START_CASH_MAIN), (c2+u2), (c2+u2-START_CASH_SUB), (p1+p2)
    except: return 0,0,0,0, []

# --- [4] 화면 렌더링 ---
st.markdown('<h2 style="text-align:center; font-size:30px; margin-bottom:20px;">🤖 Sniper Bot Unified Center</h2>', unsafe_allow_html=True)

curr1, prof1, curr2, prof2, all_positions = get_profit_data()

# 1. 듀얼 실시간 수익금 카드
c1_class = "profit-plus" if prof1 >= 0 else "profit-minus"
c2_class = "profit-plus" if prof2 >= 0 else "profit-minus"
st.markdown(f"""
<div class="total-balance-card">
    <div style="color:#8b949e; font-size:13px; margin-bottom:12px;">Real-time Cash + PnL (Main / Sub)</div>
    <span style="font-size:28px; font-weight:bold;">${curr1:,.2f}</span> 
    <span class="{c1_class}" style="font-size:20px;">({"+" if prof1>=0 else ""}{prof1:,.2f})</span>
    <span style="font-size:28px; margin: 0 20px;">/</span>
    <span style="font-size:28px; font-weight:bold;">${curr2:,.2f}</span> 
    <span class="{c2_class}" style="font-size:20px;">({"+" if prof2>=0 else ""}{prof2:,.2f})</span>
</div>
""", unsafe_allow_html=True)

# 2. 실시간 지표 및 포지션 상태
st.markdown('<h4 style="margin-bottom:12px;">📡 Real-time Market & Position Status</h4>', unsafe_allow_html=True)
now_dt = datetime.now().strftime('%m-%d %H:%M:%S')
symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XAUUSDT", "XAGUSDT", "DOGEUSDT"]
rsi_html = "".join([f'<span class="rsi-tag">[{s[:3]}:{get_rsi_live(s)}]</span>' for s in symbols])

pos_html_list = []
for p in all_positions:
    sz = float(p.get("size", 0))
    if sz > 0:
        sym, side, pnl = p['symbol'], p['side'], float(p.get('unrealisedPnl', 0))
        p_color = "#00ffc8" if pnl >= 0 else "#ff4b4b"
        avg_p = float(p["avgPrice"])
        pos_html_list.append(f'<div class="pos-row"><div class="col-time">[{now_dt}]</div><div class="col-sym">🛰️ {sym} ({side})</div><div class="col-net">Net: <span style="color:{p_color}; font-weight:bold;">{pnl:>7.1f}$</span></div><div class="col-qty">Qty: {sz}</div><div class="col-avg">Avg: {avg_p:.2f}</div></div>')
st.markdown(f'<div class="status-container"><div style="margin-bottom:20px; padding-bottom:15px; border-bottom:1px dashed #444;">{rsi_html}</div>{"".join(pos_html_list) if pos_html_list else "<div style=\"color:#8b949e; padding:10px;\">오픈된 포지션이 없습니다.</div>"}</div>', unsafe_allow_html=True)

# 3. 메인봇 트레이딩 로그 (pgrep 기반 감시)
is_main_alive = check_bot_alive("bybit7.py")
status_main = "<span class='alive-tag'>🟢 봇 감시 중</span>" if is_main_alive else "<span class='dead-tag'>🔴 봇 중단됨</span>"
st.markdown(f'<h4 style="margin-bottom:12px;">🤖 Main Bot History {status_main}</h4>', unsafe_allow_html=True)
main_log_content = "로그 파일 대기 중..."
if os.path.exists(LOG_FILES["MAIN_LOG"]):
    with open(LOG_FILES["MAIN_LOG"], "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
        processed = []
        for l in reversed(lines):
            if any(k in l for k in ["🎯", "🚀", "💰", "📉", "익절", "진입", "전략", "본전"]):
                t_m = re.search(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})', l)
                d_t = f"[{t_m.group(2)}-{t_m.group(3)} {t_m.group(4)}]" if t_m else ""
                msg = l.split(" - ")[-1].strip() if " - " in l else l.strip()
                processed.append(f"{d_t} {msg}")
            if len(processed) >= 60: break
        main_log_content = "\n".join(processed)
st.markdown(f'<div class="log-box">{main_log_content}</div>', unsafe_allow_html=True)

# 4. 페어봇 트레이딩 로그 (pgrep 기반 감시)
is_pair_alive = check_bot_alive("bybit_pair.py")
status_pair = "<span class='alive-tag'>🟢 봇 감시 중</span>" if is_pair_alive else "<span class='dead-tag'>🔴 봇 중단됨</span>"
st.markdown(f'<h4 style="margin-bottom:12px;">⚖️ Pair Bot History {status_pair}</h4>', unsafe_allow_html=True)
pair_log_content = "로그 파일 대기 중..."
if os.path.exists(LOG_FILES["PAIR_LOG"]):
    with open(LOG_FILES["PAIR_LOG"], "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
        processed_pair = []
        for l in reversed(lines):
            if "🔎 감시중" in l or any(k in l for k in ["🎯", "💰", "🚨"]):
                t_m = re.search(r'\[(\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\]', l)
                if not t_m:
                    t_m = re.search(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})', l)
                    display_ts = f"[{t_m.group(2)}-{t_m.group(3)} {t_m.group(4)}]" if t_m else f"[{now_dt}]"
                else:
                    display_ts = f"[{t_m.group(1)} {t_m.group(2)}]"
                
                msg = l.split("] ")[-1].strip() if "] " in l else l.strip()
                msg = msg.replace("🔎 감시중 | ", "")
                
                if "Z=" in msg:
                    secs = msg.split('|')
                    compact = []
                    for s in secs:
                        label = "CRYPTO" if "CRYPTO" in s else "METALS" if "METALS" in s else "ALTS" if "ALTCOINS" in s else ""
                        z_v = re.search(r'Z=([\d.-]+)', s); net = re.search(r'Net=([\d.-]+)\$', s)
                        if label and z_v:
                            n_s = f"({net.group(1)}$)" if net else ""
                            compact.append(f"{label}{n_s}:Z={z_v.group(1)}")
                    msg = " | ".join(compact) if compact else msg
                
                processed_pair.append(f"{display_ts} {msg}")
            if len(processed_pair) >= 80: break
        pair_log_content = "\n".join(processed_pair)
st.markdown(f'<div class="log-box pair-box">{pair_log_content}</div>', unsafe_allow_html=True)

st.caption(f"Last Update: {now_dt} | v13.0 System-Root Tracking Edition")
