import io, os, re, datetime, streamlit as st
from pybit.unified_trading import HTTP
from streamlit_autorefresh import st_autorefresh

# [1. 설정]
LOG_FILE = "user2_bybit7.log"
K, S = "MZxVDs6SlVHsDndKXE", "ba3OlKPxWgdMjEVddU844DrAREqWY3MgRVVv"
ss = HTTP(testnet=False, demo=True, api_key=K, api_secret=S)

st.set_page_config(page_title="Sniper Pro Monitor v8.7.7", layout="wide")
st_autorefresh(interval=10000, key="refresh_v8_7_7")

# [2. CSS 스타일: 중앙 고정 너비 및 정렬 최적화]
st.markdown("""
    <style>
    .stApp {background-color: #000000 !important;}
    [data-testid='stHeader'], [data-testid='stToolbar'] {display: none !important;}
    
    /* 전체 콘텐츠를 중앙에 고정 (너비 1000px 제한) */
    .block-container {
        max-width: 1000px !important;
        padding-top: 1rem !important;
        margin: 0 auto !important;
        float: none !important;
    }
    
    /* 제목 중앙 정렬 */
    .main-title {
        color: #00ffc8 !important;
        font-family: 'Courier New', Courier, monospace !important;
        font-size: 18px !important;
        font-weight: bold !important;
        text-align: center; /* 중앙 정렬 */
        margin-bottom: 10px !important;
    }

    /* 자산 카드 중앙 정렬 및 사이즈 고정 */
    .equity-card {
        background-color: #0e1117; 
        border: 1px solid #3e4452; 
        border-radius: 12px;
        padding: 15px 20px;
        text-align: center;
        margin: 0 auto 20px auto !important; /* 중앙 배치 핵심 */
        max-width: 400px;
    }
    
    .equity-label {
        color: #8b949e !important; 
        font-size: 11px !important;
        margin-bottom: 5px;
    }
    
    .equity-value {
        color: #ffffff !important; 
        font-size: 28px !important;
        font-weight: bold !important;
    }

    /* 터미널 폰트 및 스크롤 설정 */
    pre, code, span, div, p {
        font-family: 'Courier New', Courier, monospace !important;
        font-size: 14px !important;
        line-height: 1.5 !important;
    }

    .terminal-container {
        background-color: #000000 !important;
        border: none !important;
        padding: 0px 5px !important;
        height: calc(100vh - 220px);
        white-space: pre !important; 
        overflow: auto !important; 
        -webkit-overflow-scrolling: touch !important;
    }

    .status-text { color: #00ffc8 !important; font-weight: bold; } 
    .log-entry   { color: #ff9800 !important; } 
    .log-tp      { color: #00ff00 !important; font-weight: bold; } 
    .log-reduce  { color: #ffffff !important; } 
    .log-info    { color: #00ffc8 !important; } 
    .divider     { color: #333 !important; display: block; margin: 8px 0; }
    
    ::-webkit-scrollbar { width: 0px; height: 0px; } 
    </style>
    """, unsafe_allow_html=True)

# --- [3] 데이터 처리 함수 ---

def get_margin_balance():
    try:
        res = ss.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        balance = float(res['result']['list'][0]['totalMarginBalance'])
        return f"${balance:,.2f}"
    except: return "$0.00"

def get_live_status():
    try:
        now = datetime.datetime.now().strftime('%m-%d %H:%M:%S')
        pos_res = ss.get_positions(category="linear", settleCoin="USDT")['result']['list']
        lines = []
        for p in pos_res:
            sz = float(p.get("size", 0))
            if sz > 0:
                sym = p['symbol']; side = f"({p['side']})"
                pnl = f"{float(p.get('unrealisedPnl', 0)):.1f}$"
                avg = f"{float(p.get('avgPrice', 0)):.1f}"
                base = 0.01 if "BTC" in sym else 0.2
                step = 1; ratio = sz / base
                for s in range(1, 8):
                    if ratio >= (2**(s-1)) * 0.9: step = s
                # f-string 정렬 (전체 너비에 맞춰 고정)
                line = f"[{now}] 🛰️ {sym:<10} {side:<6} | Net: {pnl:>7} | {step}Step | Qty: {sz:<6} | Avg: {avg}"
                lines.append(line)
        return "\n".join(lines) if lines else f"[{now}] 🛰️ 현재 오픈된 포지션이 없습니다."
    except: return "🛰️ API 연결 대기 중..."

def format_log_line(line):
    if any(k in line for k in ["매물대", "갱신", "가동", "감시"]): return None
    time_match = re.search(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})', line)
    if not time_match: return None
    timestamp = f"[{time_match.group(2)}-{time_match.group(3)} {time_match.group(4)}]"
    symbol_match = re.search(r'\[(.*?)\]', line)
    symbol_raw = symbol_match.group(1) if symbol_match else "SYSTEM"
    symbol = f"[{symbol_raw:<8}]"

    if "🎯" in line:
        rsi = re.search(r'RSI:([\d.]+)', line); price = re.search(r'종가:([\d.]+)', line)
        qty = "0.01" if "BTC" in line else "0.2"
        rsi_val = f"RSI:{float(rsi.group(1)):.1f}" if rsi else "----"
        p_val = price.group(1) if price else "----"
        return f"<div class='log-entry'>{timestamp} 🎯 {symbol} 진입 성공 | {qty:<5} | {rsi_val} | 가격:{p_val}</div>"
    if any(k in line for k in ["💰", "익절 완료", "전략 종료"]):
        return f"<div class='log-tp'>{timestamp} 💰 {symbol} 전략 종료 - 익절 완료</div>"
    if any(k in line for k in ["📉", "본전", "덜어내기"]):
        reduce_qty = "0.005" if "BTC" in line else "0.1"
        return f"<div class='log-reduce'>{timestamp} 📉 {symbol} 리스크 관리: 50% 덜어내기 (-{reduce_qty})</div>"
    if any(k in line for k in ["🚀", "Step", "체결!"]):
        msg = line.split(" - ")[-1] if " - " in line else line
        icon = "🚀" if "🚀" in line else "⚙️"
        return f"<div class='log-info'>{timestamp} {icon} {symbol} {msg[:60]}</div>"
    return None

def get_all_logs():
    if not os.path.exists(LOG_FILE): return "로그 파일을 찾는 중..."
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        formatted = []; seen_contents = set()
        for line in reversed(lines):
            f_line = format_log_line(line)
            if not f_line: continue
            content_only = re.sub(r'\[\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]', '', f_line).strip()
            if content_only in seen_contents: continue
            formatted.append(f_line); seen_contents.add(content_only)
            if len(formatted) >= 120: break 
        return "".join(formatted)
    except: return "로그 읽기 오류"

# --- [4] 메인 화면 렌더링 ---

# 1. 제목 (중앙 정렬됨)
st.markdown('<div class="main-title">🤖 Trading History & Logs</div>', unsafe_allow_html=True)

# 2. 자산 카드 (중앙 정렬됨)
current_equity = get_margin_balance()
st.markdown(f"""
    <div class="equity-card">
        <div class="equity-label">My Total Equity</div>
        <div class="equity-value">{current_equity}</div>
    </div>
    """, unsafe_allow_html=True)

# 3. 터미널 영역
live_status = get_live_status()
log_html = get_all_logs()
divider_line = "-" * 115 

st.markdown(f"""
<div class="terminal-container">
<span class="status-text">{live_status}</span>
<div class="divider">{divider_line}</div>
{log_html}
</div>
""", unsafe_allow_html=True)

st.caption(f"Last Updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Sniper Pro v8.7.7")