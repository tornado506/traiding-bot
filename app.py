import io, os, re, time
from datetime import datetime
import pandas as pd
import streamlit as st
from pybit.unified_trading import HTTP
from streamlit_autorefresh import st_autorefresh

# [1. 설정 및 연결]
K1, S1 = "O1QpHJH4wdsjPiCe1x", "XdHD6eGaNz1iSnLF0j6nVEETZMDiSvGnai75"
ss1 = HTTP(testnet=False, demo=True, api_key=K1, api_secret=S1)
ss1.endpoint = "https://api-demo.bybit.com"

K2, S2 = "PdCEy7g1TSfU5SY5aQ", "lYE2y2y7c79wG3K0kntZiwgwDwhdHY6mNUHo"
ss2 = HTTP(testnet=False, demo=True, api_key=K2, api_secret=S2)
ss2.endpoint = "https://api-demo.bybit.com"

# ★ 원금 설정 (수익금 계산의 기준점) ★
START_CASH_MAIN = 50000.0
START_CASH_SUB  = 5000.0

LOG_FILES = {"MAIN_LOG": "bybit7.log"}
FEE_RATE = 0.0006 

st.set_page_config(page_title="Bybit Sniper Monitor v12.1", layout="wide")
st_autorefresh(interval=5000, key="refresh_v12_1")

# [2. CSS 스타일 - 폰트 색상 강화]
st.markdown(
    """
    <style>
    .stApp {background-color: #0e1117 !important;}
    [data-testid="stHeader"], [data-testid="stToolbar"] {display: none !important;}
    .block-container {padding-top: 0.5rem !important; max-width: 98% !important;}
    * {font-family: 'Courier New', monospace; color: #ffffff !important;} /* 기본 글자색 흰색 강제 */
    
    .main-title { font-size: 28px !important; font-weight: bold !important; margin-bottom: 20px !important; text-align: center; }

    .total-balance-card {
        background: linear-gradient(135deg, #1e2631 0%, #0e1117 100%);
        padding: 20px; border-radius: 15px; text-align: center;
        border: 1px solid #3e4452; margin-bottom: 25px !important;
    }
    .equity-label { color: #8b949e !important; font-size: 12px !important; margin-bottom: 8px; }
    
    .profit-plus { color: #00ffc8 !important; font-weight: bold; }
    .profit-minus { color: #ff4b4b !important; font-weight: bold; }
    .base-white { color: #ffffff !important; font-weight: bold; font-size: 24px; }
    
    .asset-card {
        background-color: #161b22; padding: 20px; border-radius: 12px;
        border: 1px solid #30363d; margin-bottom: 10px;
    }
    
    /* 포지션 텍스트 색상 명시 */
    .status-line { color: #e0e0e0 !important; font-size: 14px; line-height: 1.8; }
    
    .clean-log {
        background-color: #000000 !important; color: #00ffc8 !important;
        padding: 12px !important; border-radius: 8px; white-space: pre !important; 
        overflow: auto !important; font-size: 13px !important; line-height: 1.5 !important;
        height: 450px !important; margin-bottom: 30px;
    }
    
    hr { border-top: 1px solid #333; margin: 20px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- [3] 데이터 처리 함수 ---

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

def get_profit_html():
    try:
        bal1 = ss1.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        cash1 = float(bal1['result']['list'][0]['coin'][0]['walletBalance'])
        pos1 = ss1.get_positions(category="linear", settleCoin="USDT")['result']['list']
        pnl1 = sum(float(p.get('unrealisedPnl', 0)) for p in pos1 if float(p.get("size", 0)) > 0)
        curr1 = cash1 + pnl1
        prof1 = curr1 - START_CASH_MAIN
        
        bal2 = ss2.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        cash2 = float(bal2['result']['list'][0]['coin'][0]['walletBalance'])
        pos2 = ss2.get_positions(category="linear", settleCoin="USDT")['result']['list']
        pnl2 = sum(float(p.get('unrealisedPnl', 0)) for p in pos2 if float(p.get("size", 0)) > 0)
        curr2 = cash2 + pnl2
        prof2 = curr2 - START_CASH_SUB
        
        class1 = "profit-plus" if prof1 >= 0 else "profit-minus"
        sign1 = "+" if prof1 >= 0 else "-"
        class2 = "profit-plus" if prof2 >= 0 else "profit-minus"
        sign2 = "+" if prof2 >= 0 else "-"
        
        return f"""
        <span class="base-white">${curr1:,.2f}</span> 
        <span class="{class1}" style="font-size:18px;">({sign1}${abs(prof1):,.2f})</span>
        <span class="base-white" style="margin: 0 15px;">/</span>
        <span class="base-white">${curr2:,.2f}</span> 
        <span class="{class2}" style="font-size:18px;">({sign2}${abs(prof2):,.2f})</span>
        """
    except: return "Loading..."

def get_live_status():
    try:
        now = datetime.now().strftime('%m-%d %H:%M:%S')
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XAUUSDT", "XAGUSDT", "DOGEUSDT"]
        
        # 지표 상단 표시
        rsi_head = " ".join([f"<span style='color:#00ffc8; font-weight:bold; margin-right:15px;'>[{s[:3]}:{get_rsi_live(s)}]</span>" for s in symbols])

        # 포지션 병합
        pos_all = ss1.get_positions(category="linear", settleCoin="USDT")['result']['list'] + \
                  ss2.get_positions(category="linear", settleCoin="USDT")['result']['list']
        
        pos_lines = []
        for p in pos_all:
            sz = float(p.get("size", 0))
            if sz > 0:
                sym = p['symbol']; side = f"({p['side']})"
                pnl = float(p.get('unrealisedPnl', 0))
                pnl_color = "#00ffc8" if pnl >= 0 else "#ff4b4b"
                avg = f"{float(p.get('avgPrice', 0)):.2f}"
                
                # 가독성을 위해 각 줄을 span으로 감싸고 색상 명시
                line = f"""
                <div class="status-line">
                    <span style="color:#8b949e;">[{now}]</span> 
                    <span style="color:#ffffff; font-weight:bold;">🛰️ {sym:<10} {side:<6}</span> | 
                    Net: <span style="color:{pnl_color}; font-weight:bold;">{pnl:>7.1f}$</span> | 
                    Qty: <span style="color:#ffffff;">{sz:<7}</span> | 
                    Avg: <span style="color:#ffffff;">{avg}</span>
                </div>
                """
                pos_lines.append(line)
        
        final_pos_html = "".join(pos_lines) if pos_lines else f"<div style='color:#8b949e;'>[{now}] 대기 중... 오픈된 포지션이 없습니다.</div>"
        
        return f"<div>{rsi_head}</div><div style='margin-top:15px; border-top:1px dashed #333; padding-top:15px;'>{final_pos_html}</div>"
    except: return "API Error"

def get_final_logs(path, limit=100):
    if not os.path.exists(path): return "로그 파일 대기 중..."
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            processed = []
            for l in reversed(lines):
                line = l.strip()
                if not line or "nohup" in line or "매물대" in line: continue
                
                # 핵심 로그만 필터링
                if any(k in line for k in ["🎯", "🚀", "💰", "📉", "익절", "진입", "전략 시작", "본전"]):
                    # 시간 추출 시도
                    time_match = re.search(r'(\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                    msg = line.split(" - ")[-1] if " - " in line else line
                    processed.append(f"{msg}")
                if len(processed) >= limit: break
            return "\n".join(processed)
    except: return "로그 읽기 오류"

# --- [4] 화면 렌더링 ---

st.markdown('<div class="main-title">🤖 Bybit Sniper Bot Monitor</div>', unsafe_allow_html=True)

# 1. 수익금 추적 카드
st.markdown(f"""<div class="total-balance-card"><div class="equity-label">Current Value & Profit (Main / Sub)</div>{get_profit_html()}</div>""", unsafe_allow_html=True)

# 2. 포지션 상태 (글자색 수정 완료)
st.markdown('#### 📡 Real-time Market & Position Status')
st.markdown(f"<div class='asset-card'>{get_live_status()}</div>", unsafe_allow_html=True)

# 3. 트레이딩 로그
st.markdown('#### 🤖 Trading History & Logs')
st.markdown(f"<div class='clean-log'>{get_final_logs(LOG_FILES['MAIN_LOG'])}</div>", unsafe_allow_html=True)

st.caption(f"Last Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | v12.1 Final Fixed")