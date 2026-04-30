import io, os, re, datetime, streamlit as st
from pybit.unified_trading import HTTP
from streamlit_autorefresh import st_autorefresh

# [1. 설정]
LOG_FILE = "user2_bybit7.log"
K, S = "MZxVDs6SlVHsDndKXE", "ba3OlKPxWgdMjEVddU844DrAREqWY3MgRVVv"
ss = HTTP(testnet=False, demo=True, api_key=K, api_secret=S)

st.set_page_config(page_title="Sniper Pro Monitor v8.7.8", layout="wide")
st_autorefresh(interval=10000, key="refresh_v8_7_8")

# [2. CSS 스타일]
st.markdown("""
    <style>
    .stApp {background-color: #000000 !important;}
    [data-testid='stHeader'], [data-testid='stToolbar'] {display: none !important;}
    .block-container { max-width: 1000px !important; padding-top: 1rem !important; margin: 0 auto !important; }
    
    .main-title { color: #00ffc8 !important; font-family: 'Courier New', Courier, monospace !important; font-size: 18px; font-weight: bold; text-align: center; margin-bottom: 10px; }

    .equity-card { background-color: #0e1117; border: 1px solid #3e4452; border-radius: 12px; padding: 15px 20px; text-align: center; margin: 0 auto 20px auto !important; max-width: 400px; }
    .equity-label { color: #8b949e !important; font-size: 11px; margin-bottom: 5px; }
    .equity-value { color: #ffffff !important; font-size: 28px; font-weight: bold; }

    pre, code, span, div, p { font-family: 'Courier New', Courier, monospace !important; font-size: 14px; line-height: 1.5; }

    .terminal-container { background-color: #000000 !important; border: none; padding: 0px 5px; height: calc(100vh - 220px); white-space: pre !important; overflow: auto; -webkit-overflow-scrolling: touch; }
    
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
    """상단 실시간 상태 (수익금 색상 강조 적용)"""
    try:
        now = datetime.datetime.now().strftime('%m-%d %H:%M:%S')
        pos_res = ss.get_positions(category="linear", settleCoin="USDT")['result']['list']
        lines = []
        for p in pos_res:
            sz = float(p.get("size", 0))
            if sz > 0:
                sym = p['symbol']; side = f"({p['side']})"
                
                # 수익금 계산 및 색상 결정 (요청하신 부분)
                pnl_val = float(p.get('unrealisedPnl', 0))
                pnl_color = "#00ff00" if pnl_val >= 0 else "#ff4b4b" # 양수 초록 / 음수 빨강
                pnl_html = f"<span style='color:{pnl_color}; font-weight:bold;'>{pnl_val:>7.1f}$</span>"
                
                avg = f"{float(p.get('avgPrice', 0)):.1f}"
                base = 0.01 if "BTC" in sym else 0.2
                step = 1; ratio = sz / base
                for s in range(1, 8):
                    if ratio >= (2**(s-1)) * 0.9: step = s
                
                line = f"[{now}] 🛰️ {sym:<10} {side:<6} | Net: {pnl_html} | {step}Step | Qty: {sz:<6} | Avg: {avg}"
                lines.append(f"<div class='status-text'>{line}</div>")
        return "".join(lines) if lines else f"<div class='status-text'>[{now}] 🛰️ 현재 오픈된 포지션이 없습니다.</div>"
    except: return "<div class='status-text'>🛰️ API 연결 대기 중...</div>"

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

st.markdown('<div class="main-title">🤖 Trading History & Logs</div>', unsafe_allow_html=True)

current_equity = get_margin_balance()
st.markdown(f'<div class="equity-card"><div class="equity-label">My Total Equity</div><div class="equity-value">{current_equity}</div></div>', unsafe_allow_html=True)

live_status_html = get_live_status()
log_html = get_all_logs()
divider_line = "-" * 115 

st.markdown(f"""
<div class="terminal-container">
{live_status_html}
<div class="divider">{divider_line}</div>
{log_html}
</div>
""", unsafe_allow_html=True)

st.caption(f"Last Updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Sniper Pro v8.7.8 (Color Fixed)")