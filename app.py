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

# [1. 설정 및 연결 - 듀얼 계정]
# 계정 1: 메인 봇 (bybit7)
K1, S1 = "O1QpHJH4wdsjPiCe1x", "XdHD6eGaNz1iSnLF0j6nVEETZMDiSvGnai75"
ss1 = HTTP(testnet=False, demo=True, api_key=K1, api_secret=S1)
ss1.endpoint = "https://api-demo.bybit.com"

# 계정 2: 페어 봇 (bybit_pair)
K2, S2 = "PdCEy7g1TSfU5SY5aQ", "lYE2y2y7c79wG3K0kntZiwgwDwhdHY6mNUHo"
ss2 = HTTP(testnet=False, demo=True, api_key=K2, api_secret=S2)
ss2.endpoint = "https://api-demo.bybit.com"

LOG_FILES = {"PAIR": "pair_bot.log", "BYBIT5": "bybit7.log"}

CONFIGS = {
    "BTCUSDT":  {"half_unit": 0.051}, "XAUUSDT":  {"half_unit": 0.86},
    "ETHUSDT":  {"half_unit": 1.73}, "SOLUSDT":  {"half_unit": 47.6},
    "XAGUSDT":  {"half_unit": 53.0}, "DOGEUSDT": {"half_unit": 37000.0},
}

CHART_SIZE = (10.5, 3.5) 
FEE_RATE = 0.0007 

st.set_page_config(page_title="Dual Sniper Monitor v11.9.7", layout="wide")
st_autorefresh(interval=5000, key="refresh_dual_v11_9")

# [2. CSS 스타일]
st.markdown(
    """
    <style>
    .stApp {background-color: #0e1117 !important;}
    [data-testid="stHeader"], [data-testid="stToolbar"] {display: none !important;}
    .block-container {padding-top: 0.3rem !important; max-width: 98% !important;}
    * {font-family: 'Courier New', monospace;}
    
    .main-title { color: #ffffff !important; font-size: 32px !important; font-weight: bold !important; margin-bottom: 25px !important; text-align: center; }
    h2, h4, .section-header { color: #ffffff !important; font-weight: bold !important; font-size: 18px !important; margin-top: 15px !important; margin-bottom: 10px !important; }

    .total-balance-card {
        background: linear-gradient(135deg, #1e2631 0%, #0e1117 100%);
        padding: 15px; border-radius: 12px; text-align: center;
        border: 1px solid #00ffc8; margin-bottom: 30px !important;
    }
    .equity-label { color: #8b949e !important; font-size: 11px !important; }
    .equity-value { color: #00ffc8 !important; font-size: 30px !important; font-weight: bold !important; }
    
    .clean-log {
        background-color: #000000 !important; color: #00ffc8 !important;
        padding: 10px !important; border: none !important; white-space: pre !important; 
        overflow: auto !important; font-size: 13px !important; line-height: 1.4 !important;
        height: 400px !important; margin-bottom: 35px;
    }
    .clean-log code { background-color: transparent !important; color: inherit !important; padding: 0 !important; }
    .pair-log { color: #ff9800 !important; border-left: 3.2px solid #ff9800 !important; }
    
    hr {border-top: 1px solid #333; margin: 25px 0;}
    ::-webkit-scrollbar { width: 0px; height: 0px; }
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

# ★ 듀얼 계정 지능형 합산 잔액 로직 ★
def get_dual_intelligent_balance():
    try:
        # 계정 1 계산
        bal1 = ss1.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        cash1 = float(bal1['result']['list'][0]['coin'][0]['walletBalance'])
        pos1 = ss1.get_positions(category="linear", settleCoin="USDT")['result']['list']
        pnl1 = sum(float(p.get('unrealisedPnl', 0)) for p in pos1 if float(p.get("size", 0)) > 0)
        
        # 계정 2 계산
        bal2 = ss2.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        cash2 = float(bal2['result']['list'][0]['coin'][0]['walletBalance'])
        pos2 = ss2.get_positions(category="linear", settleCoin="USDT")['result']['list']
        pnl2 = sum(float(p.get('unrealisedPnl', 0)) for p in pos2 if float(p.get("size", 0)) > 0)
        
        return f"${(cash1 + pnl1):,.2f} / ${(cash2 + pnl2):,.2f}"
    except: return "$0.00 / $0.00"

def get_live_status():
    try:
        now = datetime.now().strftime('%m-%d %H:%M:%S')
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XAUUSDT", "XAGUSDT", "DOGEUSDT"]
        rsi_lines = [f"[{now}] 🔎 {s:<10} 감시 중... RSI: {get_rsi_live(s)}" for s in symbols]
        rsi_head = "<div>" + "</div><div>".join(rsi_lines) + "</div>"

        # 두 계정의 포지션을 모두 가져옴
        p_list1 = ss1.get_positions(category="linear", settleCoin="USDT")['result']['list']
        p_list2 = ss2.get_positions(category="linear", settleCoin="USDT")['result']['list']
        
        pos_lines = []
        for p in (p_list1 + p_list2): # 두 리스트 합치기
            sz = float(p.get("size", 0))
            if sz > 0:
                sym = p['symbol']; side = f"({p['side']})"
                pnl = float(p.get('unrealisedPnl', 0))
                pnl_color = "#00ff00" if pnl >= 0 else "#ff4b4b"
                pnl_html = f"<span style='color:{pnl_color}; font-weight:bold;'>{pnl:>7.1f}$</span>"
                avg = f"{float(p.get('avgPrice', 0)):.1f}"
                base = CONFIGS.get(sym, {}).get("half_unit", 0.01)
                step_label = "Full" if sz >= base * 1.5 else "Half"
                line = f"[{now}] 🛰️ {sym:<10} {side:<6} | Net: {pnl_html} | {step_label} | Qty: {sz:<7} | Avg: {avg}"
                pos_lines.append(f"<div>{line}</div>")
        
        content = rsi_head + "<div style='margin-top:10px; border-top:1px dashed #444; padding-top:10px;'>" + ("".join(pos_lines) if pos_lines else f"<div>[{now}] 🛰️ 현재 오픈된 포지션이 없습니다.</div>") + "</div>"
        return content
    except: return "<div class='status-text'>🛰️ API 연결 대기 중...</div>"

@st.cache_data(ttl=10)
def get_kline(sym, itv, lim):
    try:
        r = ss1.get_kline(category="linear", symbol=sym, interval=itv, limit=lim)["result"]["list"]
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
    if not os.path.exists(path): return "로그 파일 대기 중..."
    today_mm_dd = datetime.now().strftime('%m-%d')
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            processed = []
            seen = set()
            for l in reversed(lines):
                line = l.strip()
                if not line or "nohup" in line or "매물대" in line or "에러" in line: continue
                time_match = re.search(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})', line)
                if time_match: display_time = f"{time_match.group(2)}-{time_match.group(3)} {time_match.group(4)}"
                else:
                    only_time = re.search(r'(\d{2}:\d{2}:\d{2})', line)
                    display_time = f"{today_mm_dd} {only_time.group(1)}" if only_time else "Unknown"
                msg = line.split(" - ")[-1] if " - " in line else line
                if "PAIR" in log_type:
                    if "Z=" in msg:
                        sections = msg.split('|')
                        compact = []
                        for sec in sections:
                            label = ""
                            if "CRYPTO" in sec: label = "CRYPTO"
                            elif "METALS" in sec: label = "METALS"
                            elif "ALTCOINS" in sec: label = "ALTCOINS"
                            z_val = re.search(r'Z=([\d.-]+)', sec); net_pnl = re.search(r'Net=([\d.-]+)\$', sec)
                            if label and z_val:
                                net_str = f"({net_pnl.group(1)}$)" if net_pnl else ""
                                compact.append(f"{label}{net_str}:Z={z_val.group(1)}")
                        msg = " | ".join(compact) if compact else msg.replace("🔎 감시중 |", "").strip()
                    else: msg = msg.replace("🔎 감시중 |", "").replace("🔎 감시중", "").strip()
                display_line = f"[{display_time}] {msg}"
                content = re.sub(r'\[.*?\]', '', display_line).strip()
                if content in seen: continue
                seen.add(content); processed.append(display_line)
                if len(processed) >= limit: break
            return "\n\n" + "\n".join(processed)
    except: return "Log Error"

# --- [4] 화면 렌더링 ---

st.markdown('<div class="main-title">🤖 Sniper Bot Monitor v11.9.7</div>', unsafe_allow_html=True)

# 1. 지능형 실시간 듀얼 잔액 (Main / Sub)
dual_total = get_dual_intelligent_balance()
st.markdown(f"""
    <div class="total-balance-card">
        <div class="equity-label">Intelligent Balance (Main / Sub-Pair)</div>
        <div class="equity-value">{dual_total}</div>
    </div>
    """, unsafe_allow_html=True)

# 2. 메인 봇 로그
st.markdown('<div class="section-header">🤖 Main Bot Trading History</div>', unsafe_allow_html=True)
live_header = get_live_status()
main_history = get_final_logs(LOG_FILES['BYBIT5'], "BYBIT5", limit=60)
st.markdown(f"<div class='clean-log'>{live_header}\n{'-'*95}{main_history}</div>", unsafe_allow_html=True)

# 3. 페어봇 로그
st.subheader("⚖️ Pair Bot Trading History")
pair_history = get_final_logs(LOG_FILES['PAIR'], "PAIR_RAW", limit=100)
st.markdown(f"<div class='clean-log pair-log'>{pair_history}</div>", unsafe_allow_html=True)

st.markdown("<hr>", unsafe_allow_html=True)

# 4. 차트
st.markdown('<div class="section-header">📊 Market Analysis Charts</div>', unsafe_allow_html=True)
tf_choice = st.radio("Chart Timeframe", ["15M", "1H"], horizontal=True, key="v11_9_tf")
itv = "60" if tf_choice == "1H" else "15"
img_crypto = draw_pair_chart("BTCUSDT", "ETHUSDT", "#007bff", "#dc3545", "CRYPTO Correl", itv)
if img_crypto: st.image(img_crypto, use_container_width=True)
st.markdown("<br>", unsafe_allow_html=True)
img_metal = draw_pair_chart("XAUUSDT", "XAGUSDT", "#ffc107", "#6f42c1", "METALS Correl", itv)
if img_metal: st.image(img_metal, use_container_width=True)

st.caption(f"Last Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | v11.9.7 Dual-Account Unified")