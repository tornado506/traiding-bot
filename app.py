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
K, S = "O1QpHJH4wdsjPiCe1x", "XdHD6eGaNz1iSnLF0j6nVEETZMDiSvGnai75"
ss = HTTP(testnet=False, demo=True, api_key=K, api_secret=S)
ss.endpoint = "https://api-demo.bybit.com"

LOG_FILES = {"PAIR": "pair_bot.log", "BYBIT5": "bybit7.log"}

CONFIGS = {
    "BTCUSDT":  {"half_unit": 0.051},
    "XAUUSDT":  {"half_unit": 0.86},
    "ETHUSDT":  {"half_unit": 1.73},
    "SOLUSDT":  {"half_unit": 47.6},
    "XAGUSDT":  {"half_unit": 53.0},
    "DOGEUSDT": {"half_unit": 37000.0},
}

CHART_SIZE = (10.5, 3.5) 
FEE_RATE = 0.0007 

st.set_page_config(page_title="Sniper Bot Monitor v11.9", layout="wide")
st_autorefresh(interval=10000, key="refresh_v11_9_final")

# [2. CSS 스타일 - 제목 크기 확대 및 모든 헤더 흰색]
st.markdown(
    """
    <style>
    .stApp {background-color: #0e1117 !important;}
    [data-testid="stHeader"], [data-testid="stToolbar"] {display: none !important;}
    .block-container {padding-top: 0.3rem !important; max-width: 98% !important;}
    * {font-family: 'Courier New', monospace;}
    
    /* ★ 메인 제목: 흰색 및 크기 확대 (32px) ★ */
    .main-title {
        color: #ffffff !important;
        font-size: 32px !important;
        font-weight: bold !important;
        margin-bottom: 25px !important; /* 아래 여백 추가 */
        text-align: center;
    }

    /* ★ 모든 섹션 제목: 흰색 (#ffffff) ★ */
    h2, h4, .section-header {
        color: #ffffff !important; 
        font-weight: bold !important; 
        font-size: 18px !important; 
        margin-top: 15px !important;
        margin-bottom: 10px !important;
    }

    .total-balance-card {
        background: linear-gradient(135deg, #1e2631 0%, #0e1117 100%);
        padding: 15px; border-radius: 12px; text-align: center;
        border: 1px solid #3e4452; 
        margin-bottom: 30px !important; /* 자산 카드 아래 여백 확대 */
    }
    .equity-label { color: #8b949e !important; font-size: 11px !important; }
    .equity-value { color: #ffffff !important; font-size: 28px !important; font-weight: bold !important; }
    
    .clean-log {
        background-color: #000000 !important; 
        color: #00ffc8 !important;
        padding: 10px !important; 
        border: none !important; 
        white-space: pre !important; 
        overflow: auto !important; 
        -webkit-overflow-scrolling: touch !important;
        font-size: 13px !important; 
        line-height: 1.4 !important;
        height: 380px !important; 
        margin-bottom: 35px;
    }
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
        r = ss.get_kline(category="linear", symbol=symbol, interval="15", limit=100)["result"]["list"]
        df = pd.DataFrame(r, columns=["ts","o","h","l","c","v","t"]).apply(pd.to_numeric).iloc[::-1]
        delta = df['c'].diff()
        gain = delta.where(delta > 0, 0); loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        rsi = 100 - (100 / (1 + (avg_gain / (avg_loss + 1e-9))))
        return round(rsi.iloc[-1], 1)
    except: return "--"

def get_margin_balance():
    try:
        res = ss.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        return f"${float(res['result']['list'][0]['totalMarginBalance']):,.2f}"
    except: return "$0.00"

def get_live_status():
    try:
        now = datetime.now().strftime('%m-%d %H:%M:%S')
        
        # ★ 로그 형식과 동일하게 RSI 실시간 한 줄씩 생성 ★
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XAUUSDT", "XAGUSDT", "DOGEUSDT"]
        rsi_lines = []
        for s in symbols:
            val = get_rsi_live(s)
            # 로그와 똑같은 [월-일 시:분:초] 🔎 [종목] 형식
            rsi_lines.append(f"[{now}] 🔎 {s:<10} 감시 중... RSI: {val}")
        
        # RSI 라인들을 합침
        rsi_head = "<div>" + "</div><div>".join(rsi_lines) + "</div>"

        pos_res = ss.get_positions(category="linear", settleCoin="USDT")['result']['list']
        pos_lines = []
        for p in pos_res:
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
        
        # 포지션 정보가 없으면 알림 표시
        if not pos_lines:
            pos_lines.append(f"<div>[{now}] 🛰️ 현재 오픈된 포지션이 없습니다.</div>")

        # 결과: RSI 감시 로그(위) + 현재 포지션 상황(아래)
        return rsi_head + "<div style='margin-top:10px; border-top:1px dashed #444; padding-top:10px;'>" + "".join(pos_lines) + "</div>"
    except: return "<div class='status-text'>🛰️ API 연결 대기 중...</div>"

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

                # ★ 정밀 시간 추출 (MM-DD 포함) ★
                time_match = re.search(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})', line)
                if time_match:
                    # YYYY-MM-DD HH:MM:SS 형태일 때
                    display_time = f"{time_match.group(2)}-{time_match.group(3)} {time_match.group(4)}"
                else:
                    # [HH:MM:SS] 형태일 때 (오늘 날짜 강제 주입)
                    only_time = re.search(r'(\d{2}:\d{2}:\d{2})', line)
                    if only_time:
                        display_time = f"{today_mm_dd} {only_time.group(1)}"
                    else:
                        display_time = "Unknown"

                msg = line.split(" - ")[-1] if " - " in line else line
                # 메시지 내부의 대괄호 시간 제거
                msg = re.sub(r'\[\d{2}:\d{2}:\d{2}\]\s*', '', msg).strip()
                msg = re.sub(r'\[\d{4}-\d{2}-\d{2}.*?\]\s*', '', msg).strip()
                
                display_line = f"[{display_time}] {msg}"

                content = re.sub(r'\[.*?\]', '', display_line).strip()
                if content in seen: continue
                seen.add(content)
                processed.append(display_line)
                if len(processed) >= limit: break
            
            return "\n\n" + "\n".join(processed)
    except: return "Log Error"

# --- [4] 화면 렌더링 ---

# 1. 메인 제목 (흰색, 크게)
st.markdown('<div class="main-title">🤖 Sniper Bot Monitor v11.9</div>', unsafe_allow_html=True)

# 2. 자산 카드 (아래 여백 추가)
current_equity = get_margin_balance()
st.markdown(f"""
    <div class="total-balance-card">
        <div class="equity-label">Total Equity (Margin Balance)</div>
        <div class="equity-value">{current_equity}</div>
    </div>
    """, unsafe_allow_html=True)

# 3. 메인 봇 로그 (제목 흰색)
st.markdown('<div class="section-header">🤖 Main Bot Trading History</div>', unsafe_allow_html=True)
live_header = get_live_status()
main_history = get_final_logs(LOG_FILES['BYBIT5'], "BYBIT5", limit=60)
st.markdown(f"<div class='clean-log'>{live_header}\n{'-'*95}{main_history}</div>", unsafe_allow_html=True)

# 4. 페어봇 로그 (제목 흰색, 월-일 표시 수정 완료)
st.markdown('<div class="section-header">⚖️ Pair Bot Trading History</div>', unsafe_allow_html=True)
pair_history = get_final_logs(LOG_FILES['PAIR'], "PAIR_RAW", limit=100)
st.markdown(f"<div class='clean-log pair-log'>{pair_history}</div>", unsafe_allow_html=True)

st.markdown("<hr>", unsafe_allow_html=True)

# 5. 분석 차트 (제목 흰색, 수직 배치)
st.markdown('<div class="section-header">📊 Market Analysis Charts</div>', unsafe_allow_html=True)
tf_choice = st.radio("Chart Timeframe", ["15M", "1H"], horizontal=True, key="v11_9_tf")
itv = "60" if tf_choice == "1H" else "15"

img_crypto = draw_pair_chart("BTCUSDT", "ETHUSDT", "#007bff", "#dc3545", "CRYPTO Correl", itv)
if img_crypto: st.image(img_crypto, use_container_width=True)
st.markdown("<br>", unsafe_allow_html=True)
img_metal = draw_pair_chart("XAUUSDT", "XAGUSDT", "#ffc107", "#6f42c1", "METALS Correl", itv)
if img_metal: st.image(img_metal, use_container_width=True)

st.caption(f"Last Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | v11.9 Full White Theme")