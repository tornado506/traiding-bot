import io, os, re, datetime, streamlit as st
import pandas as pd
import numpy as np
import streamlit.components.v1 as components
from pybit.unified_trading import HTTP
from streamlit_autorefresh import st_autorefresh
import time

# [1. 설정 및 API 연결]
LOG_FILE = "user2_bybit7.log"
K, S = "MZxVDs6SlVHsDndKXE", "ba3OlKPxWgdMjEVddU844DrAREqWY3MgRVVv"
ss = HTTP(testnet=False, demo=True, api_key=K, api_secret=S)
ss.endpoint = "https://api-demo.bybit.com"

# User2 원금 설정
START_CASH = 50000.0 

st.set_page_config(page_title="User2 Sniper Monitor", layout="wide")
st_autorefresh(interval=5000, key="refresh_user2_v19_9")

# --- [2. 데이터 사이언스 로직] ---

def get_price_live(symbol):
    try:
        res = ss.get_tickers(category="linear", symbol=symbol)
        price = float(res['result']['list'][0]['lastPrice'])
        if "BTC" in symbol: return f"{int(price):,}"
        return f"{price:,.1f}"
    except: return "--"

def get_recent_closed_logs():
    """실제 익절 내역 필터링 및 고정된 체결 시간 표시"""
    try:
        res = ss.get_closed_pnl(category="linear", limit=100)['result']['list']
        html_lines = []
        profit_only = [e for e in res if float(e['closedPnl']) > 0]
        
        for e in profit_only[:20]:
            # ★ API에서 제공하는 실제 체결 시간(updatedTime) 사용 (고정됨) ★
            ts = datetime.datetime.fromtimestamp(int(e['updatedTime'])/1000).strftime('%m.%d %H:%M:%S')
            sym = e['symbol'].replace("USDT", "")
            net_pnl = float(e['closedPnl'])
            exit_price = float(e['avgExitPrice'])
            qty = float(e['qty'])
            p_fmt = f"{exit_price:,.0f}" if "BTC" in sym else f"{exit_price:,.1f}"
            
            line = f"""
            <div style="margin-bottom:10px; color:#ffffff; font-size:10px; font-family:'Inter', sans-serif; font-weight:300; border-bottom:1px solid #222; padding-bottom:6px; white-space:nowrap;">
                <span style="color:#8b949e;">[{ts}]</span> 
                <span style="color:#00FF94; font-weight:300; margin-left:3px;">{sym}</span> 
                <span style="color:#8b949e; font-size:9px;">({p_fmt}/{qty})</span>
                <span style="margin-left:4px;">익절</span>
                <span style="color:#00FF94; font-weight:300; float:right;">+{net_pnl:.2f} USD</span>
            </div>
            """
            html_lines.append(line)
        return "".join(html_lines)
    except: return "<div style='color:#444; font-size:10px;'>데이터 수신 오류</div>"

def get_dashboard_data():
    try:
        bal = ss.get_wallet_balance(accountType="UNIFIED", coin="USDT")['result']['list'][0]
        cash = float(bal['coin'][0]['walletBalance'])
        
        pos = ss.get_positions(category="linear", settleCoin="USDT", limit=50)['result']['list']
        active_positions = []
        unr_sum = 0.0
        for p in pos:
            sz = float(p.get("size", 0))
            if sz > 0:
                pnl = float(p.get('unrealisedPnl', 0))
                unr_sum += pnl
                # ★ 포지션 진입 시간 추출 (없으면 현재 시간) ★
                entry_ts = datetime.datetime.fromtimestamp(int(p['updatedTime'])/1000).strftime('%m.%d %H:%M') if 'updatedTime' in p else "--:--"
                active_positions.append({
                    "sym": p['symbol'], "side": p['side'], "pnl": pnl, 
                    "qty": sz, "avg": float(p['avgPrice']), "time": entry_ts
                })
        
        if 'price_history' not in st.session_state: st.session_state.price_history = []
        if 'last_price_time' not in st.session_state: st.session_state.last_price_time = 0
        if time.time() - st.session_state.last_price_time >= 30:
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            p_btc, p_eth, p_xau = get_price_live("BTCUSDT"), get_price_live("ETHUSDT"), get_price_live("XAUUSDT")
            st.session_state.price_history.insert(0, f"[{ts}] BTC:{p_btc} | ETH:{p_eth} | XAU:{p_xau}")
            st.session_state.price_history = st.session_state.price_history[:3]
            st.session_state.last_price_time = time.time()

        daily_pnl = {}
        all_closed_data = ss.get_closed_pnl(category="linear", limit=200)['result']['list']
        for e in all_closed_data:
            dt = datetime.datetime.fromtimestamp(int(e['updatedTime'])/1000).strftime('%m.%d')
            daily_pnl[dt] = daily_pnl.get(dt, 0) + float(e['closedPnl'])
        
        chart_data = [{"date": (datetime.datetime.now() - datetime.timedelta(days=i)).strftime('%m.%d'), "pnl": daily_pnl.get((datetime.datetime.now() - datetime.timedelta(days=i)).strftime('%m.%d'), 0.0)} for i in range(13, -1, -1)]
        wallet_total = cash + unr_sum
        return {"net": wallet_total - START_CASH, "wallet": wallet_total, "rel": (wallet_total - START_CASH) - unr_sum, "unr": unr_sum, "active_pos": active_positions, "chart": chart_data}
    except: return None

# --- [3] UI 조립 ---
d = get_dashboard_data()

if d:
    def get_color(val): return "#00FF94" if val >= 0 else "#ff4b4b"
    def get_grad_color(val): return "#00713f" if val >= 0 else "#93000a"

    pos_html = "".join([f"""
    <div style="background:rgba(255,255,255,0.03); border-radius:12px; padding:18px; margin-bottom:12px; border:1px solid {get_color(p['pnl'])}22; position:relative; overflow:hidden;">
        <div style="position:absolute; left:0; top:0; width:5px; height:100%; background:linear-gradient(to bottom, {get_color(p['pnl'])}, {get_grad_color(p['pnl'])});"></div>
        <div style="display:flex; justify-content:space-between; align-items:center; padding-left:8px;">
            <div style="display:flex; align-items:center; gap:12px;">
                <div style="background:rgba(255,255,255,0.05); padding:10px; border-radius:10px; display:flex;"><span class="material-symbols-outlined" style="color:{get_color(p['pnl'])}; font-size:22px; font-variation-settings:'wght' 300;">{"trending_up" if p['pnl']>=0 else "trending_down"}</span></div>
                <div>
                    <div style="font-size:14px; font-weight:400; color:#ffffff; margin-bottom:5px;">{p['sym']}</div>
                    <div style="font-size:10px; color:#849586; font-weight:300;">
                        <span style="color:#8b949e; margin-right:5px;">[{p['time']}]</span>
                        <span style="color:{'#00FF94' if p['side']=='Buy' else '#ff4b4b'}; font-weight:500;">({p['side']})</span> • Qty: {p['qty']}
                    </div>
                </div>
            </div>
            <div style="text-align:right;">
                <div style="color:{get_color(p['pnl'])}; font-weight:400; font-size:17px; margin-bottom:2px;">{p['pnl']:+.2f} $</div>
                <div style="font-size:10px; color:#dae2fd; opacity:0.7; font-weight:300;">Avg: {float(p['avg']):,.1f}</div>
            </div>
        </div>
    </div>""" for p in d['active_pos']])
    
    max_p = max([abs(x['pnl']) for x in d['chart']] + [15.0])
    bars_html = "".join([f'<div style="flex:1; display:flex; flex-direction:column; align-items:center; height:100%; justify-content:flex-end; min-width:0;"><div style="font-size:6px; color:{get_color(x["pnl"])}; margin-bottom:3px; font-weight:300;">{f"{x["pnl"]:+.1f}" if abs(x["pnl"])>0.1 else ""}</div><div style="width:12px; background:{get_color(x["pnl"])}; height:{max(min((abs(x["pnl"])/max_p)*60,60),1)}%; border-radius:1px; opacity:0.8;"></div><div style="font-size:7px; color:#555; margin-top:8px; font-weight:300;">{x["date"]}</div></div>' for x in d['chart']])
    
    price_log_html = "".join([f'<div style="margin-bottom:5px; opacity:{"1" if i==0 else "0.5" if i==1 else "0.2"}; font-size:11px; font-weight:300;">{log}</div>' for i, log in enumerate(st.session_state.price_history)])
    closed_trade_logs = get_recent_closed_logs()

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,300,0,0" rel="stylesheet">
        <style>
            body {{ background-color: #0A0C10; margin: 0; padding: 0; font-family: 'Inter', sans-serif; color: #dae2fd; overflow-x: hidden; font-weight: 300; }}
            .container {{ max-width: 440px; margin: 0 auto; padding: 30px 10px 50px 10px; }}
            .glass-card {{ background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 20px; padding: 15px; margin-bottom: 15px; }}
            .label {{ font-size: 8px; color: #849586; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; }}
            .val-num {{ font-size: 17px; font-weight: 400; letter-spacing: -0.5px; }}
            .unit-small {{ font-size: 9px; font-weight: 400; opacity: 0.5; margin-left: 1px; }}
            .price-log-box {{ background: rgba(255,255,255,0.02); border-radius: 12px; padding: 12px; margin-bottom: 15px; font-family: 'Courier New'; color: #00FF94; border: 1px solid #222; line-height: 1.4; min-height: 60px; }}
            .history-scroll-box {{ height: 160px; overflow-y: auto; padding-right: 5px; -webkit-overflow-scrolling: touch; }}
            .history-scroll-box::-webkit-scrollbar {{ width: 2px; }}
            .history-scroll-box::-webkit-scrollbar-thumb {{ background: #333; border-radius: 10px; }}
            .material-symbols-outlined {{ font-family: 'Material Symbols Outlined' !important; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div style="display:flex; justify-content:space-between; align-items:center; padding: 0 5px 20px;">
                <span class="material-symbols-outlined" style="color:#00FF94; font-size:24px;">chevron_left</span>
                <div style="color:#00FF94; font-weight:600; font-size:16px; letter-spacing:2px;">RESULTS</div>
                <span class="material-symbols-outlined" style="font-size:22px; color:#444;">notifications</span>
            </div>
            
            <div class="glass-card">
                <div style="display:flex; align-items:center;">
                    <div style="flex:1.8;">
                        <div class="label">Net / Wallet Income</div>
                        <div class="val-num">
                            <span style="color:#00FF94;">{d['net']:,.0f}<span class="unit-small"> USD</span></span> 
                            <span style="opacity:0.2; margin:0 3px;">/</span>
                            <span style="color:#ffffff;">{d['wallet']:,.0f}<span class="unit-small"> USD</span></span>
                        </div>
                    </div>
                    <div style="flex:1; border-left: 1px solid rgba(255,255,255,0.08); padding-left: 12px;">
                        <div class="label">Start Balance</div>
                        <div class="val-num" style="color:#ffffff;">50,000<span class="unit-small"> USD</span></div>
                    </div>
                </div>
            </div>

            <div style="display:flex; gap:8px; margin-bottom:15px;">
                <div class="glass-card" style="flex:1; margin:0; text-align:center; padding:12px;">
                    <div class="label">Session Profit</div>
                    <div style="font-size:16px; font-weight:400; color:#bdf4ff;">{d['rel']:,.1f}<span class="unit-small"> USD</span></div>
                </div>
                <div class="glass-card" style="flex:1; margin:0; text-align:center; padding:12px;">
                    <div class="label">Unrealized PnL</div>
                    <div style="font-size:16px; font-weight:400; color:{get_color(d['unr'])};">{d['unr']:,.1f}<span class="unit-small"> USD</span></div>
                </div>
            </div>

            <div style="font-size:10px; font-weight:500; color:#849586; letter-spacing:1px; margin-bottom:12px; padding-left:5px;">ACTIVE POSITIONS</div>
            {pos_html if pos_html else "<div style='color:#444; text-align:center; padding:30px; font-size:11px;'>진입 포지션 없음</div>"}

            <div style="font-size:9px; font-weight:600; color:#849586; letter-spacing:1px; margin: 15px 0 8px 5px;">LIVE PRICE MONITORING (30S)</div>
            <div class="price-log-box">{price_log_html if price_log_html else "시세 수신 중..."}</div>

            <div style="font-size:9px; font-weight:600; color:#849586; letter-spacing:1px; margin: 15px 0 8px 5px;">PROFIT/LOSS HISTORY</div>
            <div class="glass-card" style="margin-top:0; padding:12px;">
                <div class="history-scroll-box">{closed_trade_logs}</div>
            </div>

            <div class="glass-card" style="margin-top:10px; padding:20px 5px 15px 5px;">
                <div style="font-size:9px; color:#849586; text-transform:uppercase; margin-bottom:25px; padding-left:10px;">Revenue History (14 Days)</div>
                <div style="height:120px; display:flex; align-items:flex-end; gap:4px; width:100%; overflow:hidden; padding: 0 5px;">{bars_html}</div>
            </div>
        </div>
    </body>
    </html>
    """
    components.html(full_html, height=1450, scrolling=False)
else:
    st.error("데이터 수신 대기 중...")