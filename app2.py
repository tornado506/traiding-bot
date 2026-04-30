import io, os, re, datetime, streamlit as st
from pybit.unified_trading import HTTP
from streamlit_autorefresh import st_autorefresh

# [1. 설정]
LOG_FILE = "user2_bybit7.log"
K, S = "MZxVDs6SlVHsDndKXE", "ba3OlKPxWgdMjEVddU844DrAREqWY3MgRVVv"
ss = HTTP(testnet=False, demo=True, api_key=K, api_secret=S)

st.set_page_config(page_title="Sniper Pro Monitor v8.7", layout="wide")
st_autorefresh(interval=10000, key="refresh_v8_7")

# [2. CSS 스타일: 제목 크기 강화 및 터미널 레이아웃]
st.markdown("""
    <style>
    .stApp {background-color: #000000 !important;}
    [data-testid='stHeader'], [data-testid='stToolbar'] {display: none !important;}
    .block-container {padding-top: 1.5rem !important; max-width: 98% !important;}
    
    /* 제목 스타일 강화 */
    .main-title {
        color: #00ffc8 !important;
        font-family: 'Courier New', Courier, monospace !important;
        font-size: 38px !important; /* 크기를 38px로 대폭 키움 */
        font-weight: bold !important;
        margin-bottom: 25px !important;
        letter-spacing: -1px;
    }

    pre, code, span, div, p {
        font-family: 'Courier New', Courier, monospace !important;
        font-size: 15px !important; /* 로그 글씨도 약간 키움 */
        line-height: 1.6 !important;
    }

    .terminal-container {
        background-color: #000000 !important;
        border: 1px solid #333;
        padding: 25px;
        min-height: 850px;
        white-space: pre;
        overflow-x: hidden;
    }

    .status-text { color: #00ffc8 !important; font-weight: bold; } 
    .log-entry   { color: #ff9800 !important; } /* 진입: 주황 */
    .log-tp      { color: #00ff00 !important; font-weight: bold; } /* 익절: 초록 */
    .log-reduce  { color: #ffffff !important; } /* 덜어내기: 흰색 */
    .log-info    { color: #00ffc8 !important; } 
    .divider     { color: #444 !important; display: block; margin: 15px 0; }
    </style>
    """, unsafe_allow_html=True)

# --- [3] 데이터 처리 함수 ---

def get_live_status():
    """상단 실시간 상태 정렬"""
    try:
        now = datetime.datetime.now().strftime('%m-%d %H:%M:%S')
        pos_res = ss.get_positions(category="linear", settleCoin="USDT")['result']['list']
        lines = []
        for p in pos_res:
            sz = float(p.get("size", 0))
            if sz > 0:
                sym = p['symbol']
                side = f"({p['side']})"
                pnl = f"{float(p.get('unrealisedPnl', 0)):.1f}$"
                avg = f"{float(p.get('avgPrice', 0)):.1f}"
                base = 0.01 if "BTC" in sym else 0.2
                step = 1
                ratio = sz / base
                for s in range(1, 8):
                    if ratio >= (2**(s-1)) * 0.9: step = s
                
                line = f"[{now}] 🛰️ {sym:<10} {side:<6} | Net: {pnl:>7} | {step}Step | Qty: {sz:<6} | Avg: {avg}"
                lines.append(line)
        return "\n".join(lines) if lines else f"[{now}] 🛰️ 현재 오픈된 포지션이 없습니다."
    except: return "🛰️ API 연결 대기 중..."

def format_log_line(line):
    """로그 줄별 필터링 및 양식 변환"""
    # 매물대 및 갱신 관련 로그 완전 차단
    if any(k in line for k in ["매물대", "갱신", "가동", "감시"]):
        return None

    time_match = re.search(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})', line)
    if not time_match: return None
    
    timestamp = f"[{time_match.group(2)}-{time_match.group(3)} {time_match.group(4)}]"
    symbol_match = re.search(r'\[(.*?)\]', line)
    symbol_raw = symbol_match.group(1) if symbol_match else "SYSTEM"
    symbol = f"[{symbol_raw:<8}]"

    # 1. 신규 진입 (주황색)
    if "🎯" in line:
        rsi = re.search(r'RSI:([\d.]+)', line)
        price = re.search(r'종가:([\d.]+)', line)
        qty = "0.01" if "BTC" in line else "0.2"
        rsi_val = f"RSI:{float(rsi.group(1)):.1f}" if rsi else "----"
        p_val = price.group(1) if price else "----"
        return f"<div class='log-entry'>{timestamp} 🎯 {symbol} 신규 진입 성공 | {qty:<5} {symbol_raw} | {rsi_val} | 가격:{p_val}</div>"

    # 2. 익절 (초록색)
    if any(k in line for k in ["💰", "익절 완료", "전략 종료"]):
        return f"<div class='log-tp'>{timestamp} 💰 {symbol} 전략 종료 - 모든 포지션 익절 완료</div>"

    # 3. 본전 덜어내기 (흰색)
    if any(k in line for k in ["📉", "본전", "덜어내기"]):
        reduce_qty = "0.005" if "BTC" in line else "0.1"
        return f"<div class='log-reduce'>{timestamp} 📉 {symbol} 리스크 관리: 본전 50% 덜어내기 완료 (-{reduce_qty})</div>"

    # 4. 기타 핵심 정보 (청록색)
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
        
        formatted = []
        seen_contents = set()
        
        for line in reversed(lines):
            f_line = format_log_line(line)
            if not f_line: continue
            
            # 내용 중복 제거
            content_only = re.sub(r'\[\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]', '', f_line).strip()
            if content_only in seen_contents: continue
                
            formatted.append(f_line)
            seen_contents.add(content_only)
            
            if len(formatted) >= 80: break
            
        return "".join(formatted)
    except: return "로그 읽기 오류"

# --- [4] 메인 화면 렌더링 ---

# 제목 크기 키움
st.markdown('<div class="main-title">🤖 Trading History & Logs</div>', unsafe_allow_html=True)

live_status = get_live_status()
log_html = get_all_logs()
divider_line = "-" * 105

st.markdown(f"""
<div class="terminal-container">
<span class="status-text">{live_status}</span>
<div class="divider">{divider_line}</div>
{log_html}
</div>
""", unsafe_allow_html=True)

st.caption(f"Last Updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Sniper Pro v8.7")