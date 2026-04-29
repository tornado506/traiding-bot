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

# [1. 설정 및 연결]
K, S = "MZxVDs6SlVHsDndKXE", "ba3OlKPxWgdMjEVddU844DrAREqWY3MgRVVv"
ss = HTTP(testnet=False, demo=True, api_key=K, api_secret=S)
ss.endpoint = "https://api-demo.bybit.com"

# ★ 로그 파일 설정
LOG_FILES = {"PAIR": "pair_bot.log", "BYBIT5": "user2_bybit7.log"}
CHART_SIZE = (15, 5.5) 
FEE_RATE = 0.0007 

st.set_page_config(page_title="Sniper Pro Monitor v7.4", layout="wide")
st_autorefresh(interval=10000, key="refresh_ui_v7_4")

# [2. CSS 스타일]
st.markdown(
    """
    <style>
    .stApp {background-color: #0e1117 !important;}
    [data-testid="stHeader"], [data-testid="stToolbar"] {display: none !important;}
    .block-container {padding-top: 1rem !important; max-width: 98% !important;}
    * {color: #ffffff !important; font-family: 'Courier New', monospace;}
    
    .total-balance-card {
        background: linear-gradient(135deg, #1e2631 0%, #0e1117 100%);
        padding: 25px; border-radius: 20px; text-align: center;
        border: 1px solid #3e4452; margin-bottom: 25px;
    }
    
    .asset-card {
        background-color: #161b22; padding: 18px; border-radius: 15px;
        border: 1px solid #30363d; margin-bottom: 12px;
    }
    .symbol-name { font-size: 18px; font-weight: bold; color: #00ffc8; }
    
    .net-profit { font-size: 22px; font-weight: bold; margin: 8px 0; }
    .plus { color: #00ffc8 !important; }
    .minus { color: #ff4b4b !important; }
    
    .clean-log {
        background-color: #000000 !important; color: #00ffc8 !important;
        padding: 15px !important; 
        padding-bottom: 120px !important; 
        border: 1px solid #1a1c24 !important;
        border-radius: 8px !important; 
        white-space: pre !important; 
        font-size: 13px !important; 
        line-height: 1.5 !important;
        height: 500px !important; 
        overflow-y: scroll !important; 
        margin-bottom: 40px;
    }
    .event-log-box { color: #ffcc00 !important; border-left: 5px solid #ffcc00 !important; }
    h2, h4 {color: #00ffc8 !important; font-weight: bold !important; margin-bottom: 15px !important;}
    hr {border-top: 1px solid #333; margin: 25px 0;}
    
    ::-webkit-scrollbar { width: 10px; }
    ::-webkit-scrollbar-thumb { background: #444; border-radius: 10px; }
    ::-webkit-scrollbar-thumb:hover { background: #00ffc8; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- [3] 데이터 함수 ---

def get_live_data():
    try:
        bal = ss.get_wallet_balance(accountType="UNIFIED", coin="USDT")['result']['list'][0]
        equity = float(bal['totalEquity'])
        pos_res = ss.get_positions(category="linear", settleCoin="USDT")['result']['list']
        active_pos = []
        b5_status_lines = []
        now_time = datetime.now().strftime('%m-%d %H:%M:%S')
        for p in pos_res:
            sz = float(p.get("size", 0))
            if sz > 0:
                sym, side = p['symbol'], p['side']
                avg, mark = float(p['avgPrice']), float(p['markPrice'])
                pnl = float(p['unrealisedPnl'])
                net = pnl - (sz * mark * FEE_RATE)
                # base_qty 추정 로직 (비트 0.001, 나머지 0.1)
                base = 0.001 if "BTC" in sym else 0.1
                step = 1
                for s in range(1, 8):
                    if (sz / base) >= (2**(s-1)) * 0.8: step = s
                active_pos.append({"sym": sym, "side": side, "net": net, "step": step, "avg": avg, "mark": mark})
                b5_status_lines.append(f"[{now_time}] 🛰️ {sym:8}({side:4}) | Net:{round(net, 1):>6}$ | {step}Step | Qty:{sz:<5} | Avg:{avg:.1f}")
        b5_header = "\n".join(b5_status_lines) if b5_status_lines else f"[{now_time}] 🛰️ 현재 잡힌 포지션이 없습니다."
        return equity, active_pos, b5_header
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
    if not os.path.exists(path): return "Waiting for logs..."
    mm_dd = datetime.now().strftime('%m-%d')
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            processed = []
            for l in lines:
                line = l.strip()
                if not line or "nohup" in line: continue
                if re.match(r'^\d{4}-\d{2}-\d{2}', line):
                    line = f"[{line[5:19]}] {line[20:]}"
                if "PAIR" in log_type:
                    line = line.replace("🔎 감시중 |", "").replace("🔎 감시중", "").strip()
                if log_type == "BYBIT5":
                    keywords = ["🎯", "🚀", "⏸", "💰", "갱신", "Step", "체결!", "익절 완료", "오류", "전략 시작"]
                    if not any(k in line for k in keywords): continue
                if log_type == "PAIR_EVENT":
                    if not any(e in line for e in ["🎯", "💰", "🚨", "🏁", "✅"]): continue
                processed.append(line)
            return "\n".join(processed[-limit:]) + "\n" * 12
    except: return "Log Error"

# --- [4] 화면 렌더링 ---

# 1. 최상단: 지갑 잔액 카드
equity, positions, b5_live_header = get_live_data()
st.markdown(f"""<div class="total-balance-card"><p style="color: #8b949e; font-size:14px;">Total Wallet Equity</p><h1 style="color: white; font-size: 38px;">${equity:,.2f}</h1></div>""", unsafe_allow_html=True)

# 2. 보유 포지션 카드
if positions:
    for p in positions:
        pnl_class = "plus" if p['net'] >= 0 else "minus"
        st.markdown(f"""<div class="asset-card"><span class="symbol-name">{p['sym']}</span><div class="net-profit {pnl_class}">${p['net']:+.2f}</div><div style="color: #8b949e; font-size: 11px;">Entry: {p['avg']:.1f} | Mark: {p['mark']:.1f} | {p['step']} Step</div></div>""", unsafe_allow_html=True)

# ★ 3. 바이비트 트레이딩 히스토리 로그창 (포지션 카드 바로 아래로 이동)
st.subheader("🤖 Bybit5 Trading History")
bybit5_history = get_final_logs(LOG_FILES['BYBIT5'], "BYBIT5")
combined_b5 = f"{b5_live_header}\n{'-'*85}\n{bybit5_history}"
st.markdown(f"<div class='clean-log'>{combined_b5}</div>", unsafe_allow_html=True)

st.markdown("<hr>", unsafe_allow_html=True)

# 4. 차트 및 페어봇 관련 데이터 (구분선 아래로 이동)
st.subheader("⚖️ Pair Correlation Monitor")
tf = st.selectbox("Timeframe", ["15M", "1H"], index=0)
itv = "60" if tf == "1H" else "15"
for s1, s2, c1, c2, title in [("BTCUSDT", "ETHUSDT", "#007bff", "#dc3545", "CRYPTO"), ("XAUUSDT", "XAGUSDT", "#ffc107", "#6f42c1", "METALS")]:
    img = draw_pair_chart(s1, s2, c1, c2, title, itv)
    if img: st.image(img, use_container_width=True)

st.subheader("⚖️ Pair Raw Status")
st.markdown(f"<div class='clean-log'>{get_final_logs(LOG_FILES['PAIR'], 'PAIR_RAW')}</div>", unsafe_allow_html=True)

st.subheader("🚀 Key Events History")
st.markdown(f"<div class='clean-log event-log-box'>{get_final_logs(LOG_FILES['PAIR'], 'PAIR_EVENT')}</div>", unsafe_allow_html=True)

st.caption(f"Last UI Update: {datetime.now().strftime('%m-%d %H:%M:%S')} | v7.4 Layout Fixed")