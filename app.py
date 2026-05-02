# --- START OF FILE app.py ---

import io, os, re, time
from datetime import datetime
import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from pybit.unified_trading import HTTP
from streamlit_autorefresh import st_autorefresh

# 서버용 렌더링 설정
matplotlib.use("Agg")

# [1. 설정 및 연결 - 내 계정 정보]
K, S = "O1QpHJH4wdsjPiCe1x", "XdHD6eGaNz1iSnLF0j6nVEETZMDiSvGnai75"
ss = HTTP(testnet=False, demo=True, api_key=K, api_secret=S)
ss.endpoint = "https://api-demo.bybit.com"

# ★ 로그 파일 설정
LOG_FILES = {"PAIR": "pair_bot.log", "BYBIT5": "bybit7.log"}

# ★ 사용자 지정 종목별 진입 비중 (Step 계산용)
CONFIGS = {
    "BTCUSDT":  {"min_unit": 0.01},
    "XAUUSDT":  {"min_unit": 0.2},
    "ETHUSDT":  {"min_unit": 0.2},
    "SOLUSDT":  {"min_unit": 6.0},
    "XAGUSDT":  {"min_unit": 7.0},
    "DOGEUSDT": {"min_unit": 5000.0},
}

# ★ 그래프 사이즈 30% 축소 유지
CHART_SIZE = (10.5, 4.0) 
FEE_RATE = 0.0007 

st.set_page_config(page_title="Sniper Pro Monitor v9.2", layout="wide")
st_autorefresh(interval=10000, key="refresh_v92")

# [2. CSS 스타일 - 모바일 상하좌우 드래그 및 디자인]
st.markdown(
    """
    <style>
    .stApp {background-color: #0e1117 !important;}
    [data-testid="stHeader"], [data-testid="stToolbar"] {display: none !important;}
    .block-container {padding-top: 0.5rem !important; max-width: 98% !important;}
    * {color: #ffffff !important; font-family: 'Courier New', monospace;}
    
    .main-title {
        color: #00ffc8 !important;
        font-size: 22px !important;
        font-weight: bold !important;
        margin-bottom: 10px !important;
    }

    .total-balance-card {
        background: linear-gradient(135deg, #1e2631 0%, #0e1117 100%);
        padding: 15px; border-radius: 15px; text-align: center;
        border: 1px solid #3e4452; margin-bottom: 15px;
    }
    
    .asset-card {
        background-color: #161b22; padding: 12px; border-radius: 12px;
        border: 1px solid #30363d; margin-bottom: 10px;
    }
    .symbol-name { font-size: 15px; font-weight: bold; color: #00ffc8; }
    .net-profit { font-size: 18px; font-weight: bold; margin: 3px 0; }
    .plus { color: #00ff00 !important; }
    .minus { color: #ff4b4b !important; }
    
    /* ★ 로그 박스 테두리 제거 및 상하좌우 드래그 기능 ★ */
    .clean-log {
        background-color: #000000 !important; 
        color: #00ffc8 !important;
        padding: 10px !important; 
        border: none !important; /* 테두리 박스줄 제거 */
        white-space: pre !important; /* 가로줄 유지 */
        overflow: auto !important; /* 상하좌우 스크롤 */
        -webkit-overflow-scrolling: touch !important;
        font-size: 14px !important; 
        line-height: 1.5 !important;
        height: 400px !important; 
        margin-bottom: 25px;
    }
    
    .pair-log { color: #ffcc00 !important; border-left: 3px solid #ffcc00 !important; }
    
    h2, h4 {color: #00ffc8 !important; font-weight: bold !important; margin-top: 5px !important;}
    hr {border-top: 1px solid #333; margin: 20px 0;}
    ::-webkit-scrollbar { width: 0px; height: 0px; } /* 스크롤바 숨김 */
    </style>
    """,
    unsafe_allow_html=True,
)

# --- [3] 데이터 함수 ---

def get_live_data():
    try:
        bal = ss.get_wallet_balance(accountType="UNIFIED", coin="USDT")['result']['list'][0]
        # 사진에 나온 Margin Balance(가용 자산)를 가져옵니다.
        equity = float(bal.get('totalMarginBalance', bal['totalEquity']))
        
        pos_res = ss.get_positions(category="linear", settleCoin="USDT")['result']['list']
        active_pos = []
        status_lines = []
        now_time = datetime.now().strftime('%m-%d %H:%M:%S')
        
        for p in pos_res:
            sz = float(p.get("size", 0))
            if sz > 0:
                sym, side = p['symbol'], p['side']
                avg = float(p['avgPrice'])
                pnl = float(p['unrealisedPnl'])
                
                # ★ 종목별 설정값(min_unit) 적용하여 Step 계산
                base = CONFIGS.get(sym, {}).get("min_unit", 0.01)
                step = 1
                ratio = sz / base
                for s in range(1, 8):
                    if ratio >= (2**(s-1)) * 0.9: step = s
                
                active_pos.append({"sym": sym, "side": side, "net": pnl, "step": step, "avg": avg})
                status_lines.append(f"[{now_time}] 🛰️ {sym:10}({side:4}) | Net:{round(pnl, 1):>6}$ | {step}Step | Qty:{sz:<7} | Avg:{avg}")
        
        header = "\n".join(status_lines) if status_lines else f"[{now_time}] 🛰️ 현재 오픈된 포지션이 없습니다."
        return equity, active_pos, header
    except: return 0, [], "Data Error"

@st.cache_data(ttl=10)
def get_kline(sym, itv, lim):
    try:
        r = ss.get_kline(category="linear", symbol=sym, interval=itv, limit=lim)["result"]["list"]
        return pd.DataFrame(r, columns=["ts","o","h","l","c","v","t"]).apply(pd.to_numeric).iloc[::-1].reset_index(drop=True)
    except: return pd.DataFrame()

def draw_pair_chart(s1, s2, c1, c2, title, interval):
    d1, d2 = get_kline(s1, interval, 80), get_kline(s2, interval, 80)
    if d1.empty or d2.empty: return None
    try:
        times = pd.to_datetime(d1["ts"], unit="ms") + pd.Timedelta(hours=9)
        z1, z2 = (d1["c"] - d1["c"].mean()) / (d1["c"].std() + 1e-9), (d2["c"] - d2["c"].mean()) / (d2["c"].std() + 1e-9)
        fig, ax = plt.subplots(figsize=CHART_SIZE)
        fig.patch.set_facecolor('#0e1117'); ax.set_facecolor('#0e1117')
        ax.grid(True, ls=":", alpha=0.2, color='white'); ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.plot(times, z1, label=s1, color=c1, lw=1.8); ax.plot(times, z2, label=s2, color=c2, lw=1.8)
        for y, c in [(0,"#555"),(2.5,"red"),(-2.5,"green")]: ax.axhline(y=y, color=c, ls=":", alpha=0.5)
        ax.legend(loc="upper left", fontsize=9, frameon=False); plt.setp(ax.legend().get_texts(), color='white')
        buf = io.BytesIO(); plt.savefig(buf, format="png", bbox_inches="tight", dpi=100, facecolor='#0e1117'); plt.close(fig); buf.seek(0)
        return buf
    except: return None

def get_final_logs(path, log_type="PAIR_RAW", limit=150):
    if not os.path.exists(path): return "로그 대기 중..."
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            processed = []
            seen = set()
            for l in reversed(lines):
                line = l.strip()
                if not line or "nohup" in line or "매물대" in line: continue
                if "NoneType" in line or "에러" in line: continue 

                # 로그 포맷 통일
                time_match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', line)
                if time_match:
                    display_line = f"[{time_match.group(0)[5:19]}] {line.split(' - ')[-1]}"
                else: display_line = line

                if log_type == "BYBIT5":
                    keywords = ["🎯", "🚀", "💰", "📉", "본전", "Step", "익절 완료"]
                    if not any(k in line for k in keywords): continue
                
                # 중복 제거
                content = re.sub(r'\[.*?\]', '', display_line).strip()
                if content in seen: continue
                seen.add(content)

                processed.append(display_line)
                if len(processed) >= limit: break
            return "\n".join(processed)
    except: return "Log Error"

# --- [4] 화면 렌더링 ---

st.markdown('<div class="main-title">🤖 Sniper Bot Monitor v9.2</div>', unsafe_allow_html=True)

# 1. 자산 카드
equity, positions, b5_live_header = get_live_data()
st.markdown(f"""<div class="total-balance-card"><p style="color: #8b949e; font-size:12px;">My Total Equity (Margin Balance)</p><h1 style="color: white; font-size: 32px;">${equity:,.2f}</h1></div>""", unsafe_allow_html=True)

# 2. 포지션 카드
if positions:
    cols = st.columns(len(positions))
    for i, p in enumerate(positions):
        pnl_class = "plus" if p['net'] >= 0 else "minus"
        with cols[i]:
            st.markdown(f"""<div class="asset-card"><span class="symbol-name">{p['sym']}</span><div class="net-profit {pnl_class}">${p['net']:+.2f}</div><div style="color: #8b949e; font-size: 11px;">Entry: {p['avg']} | {p['step']} Step</div></div>""", unsafe_allow_html=True)

# 3. 로그 섹션 (상하좌우 드래그 가능)
st.subheader("🤖 Main Bot Trading History")
bybit5_history = get_final_logs(LOG_FILES['BYBIT5'], "BYBIT5", limit=60)
st.markdown(f"<div class='clean-log'>{b5_live_header}\n{'-'*100}\n{bybit5_history}</div>", unsafe_allow_html=True)

st.subheader("⚖️ Pair Bot Trading History")
pair_history = get_final_logs(LOG_FILES['PAIR'], "PAIR_RAW", limit=40)
st.markdown(f"<div class='clean-log pair-log'>{pair_history}</div>", unsafe_allow_html=True)

st.markdown("<hr>", unsafe_allow_html=True)

# 4. 분석 차트 섹션 (상-하 배치)
st.subheader("📊 Market Analysis Charts")
tf_choice = st.radio("Timeframe", ["15M", "1H"], horizontal=True, key="global_tf")
itv = "60" if tf_choice == "1H" else "15"

img_crypto = draw_pair_chart("BTCUSDT", "ETHUSDT", "#007bff", "#dc3545", "CRYPTO Correl", itv)
if img_crypto: st.image(img_crypto, use_container_width=True)

st.markdown("<br>", unsafe_allow_html=True)

img_metal = draw_pair_chart("XAUUSDT", "XAGUSDT", "#ffc107", "#6f42c1", "METALS Correl", itv)
if img_metal: st.image(img_metal, use_container_width=True)

st.caption(f"Last Update: {datetime.now().strftime('%m-%d %H:%M:%S')} | v9.2 Multi-Symbol Fixed")