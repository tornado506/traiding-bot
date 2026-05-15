import io, os, re, datetime, streamlit as st
import pandas as pd
import numpy as np
import streamlit.components.v1 as components
from pybit.unified_trading import HTTP
from streamlit_autorefresh import st_autorefresh
import time

# [1. 설정 및 API 연결 - User2 전용]
LOG_FILE = "user2_bybit7.log"
K, S = "MZxVDs6SlVHsDndKXE", "ba3OlKPxWgdMjEVddU844DrAREqWY3MgRVVv"
ss = HTTP(testnet=False, demo=True, api_key=K, api_secret=S)
ss.endpoint = "https://api-demo.bybit.com"

# User2 원금 설정
START_CASH = 50000.0 

st.set_page_config(page_title="User2 Sniper Monitor", layout="wide")
st_autorefresh(interval=5000, key="refresh_user2_final_full")

# --- [2. 데이터 사이언스 로직] ---

def get_price_live(symbol):
    """실시간 가격 조회 및 포맷팅 (BTC:정수, ETH/XAU:1자리)"""
    try:
        res = ss.get_tickers(category="linear", symbol=symbol)
        price = float(res['result']['list'][0]['lastPrice'])
        if "BTC" in symbol:
            return f"{int(price):,}" # 콤마 포함 정수
        return f"{price:,.1f}" # 콤마 포함 소수점 1자리
    except:
        return "--"

def get_more_closed_pnl(ss_client, pages=3):
    """최대 600개의 종료 거래를 긁어와 과거 데이터 확보"""
    all_results = []
    cursor = ""
    for _ in range(pages):
        try:
            res = ss_client.get_closed_pnl(category="linear", limit=200, cursor=cursor)['result']
            all_results.extend(res.get('list', []))
            cursor = res.get('nextPageCursor', "")
            if not cursor: break
        except: break
    return all_results

def get_dashboard_data():
    try:
        # A. 자산 정보
        bal = ss.get_wallet_balance(accountType="UNIFIED", coin="USDT")['result']['list'][0]
        cash = float(bal['coin'][0]['walletBalance'])
        
        # B. 현재 포지션 데이터
        pos = ss.get_positions(category="linear", settleCoin="USDT", limit=50)['result']['list']
        active_positions = []
        unr_sum = 0.0
        for p in pos:
            sz = float(p.get("size", 0))
            if sz > 0:
                pnl = float(p.get('unrealisedPnl', 0))
                unr_sum += pnl
                active_positions.append({
                    "sym": p['symbol'], "side": p['side'], "pnl": pnl,
                    "qty": sz, "avg": float(p['avgPrice']),
                })
        
        # C. 실시간 가격 감시 로그 (30초마다 갱신 / 슬림 버전)
        if 'price_history' not in st.session_state: st.session_state.price_history = []
        if 'last_price_time' not in st.session_state: st.session_state.last_price_time = 0
        
        if time.time() - st.session_state.last_price_time >= 30:
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            p_btc = get_price_live("BTCUSDT")
            p_eth = get_price_live("ETHUSDT")
            p_xau = get_price_live("XAUUSDT")
            
            # [시간] BTC:가격 | ETH:가격 | XAU:가격 형식 (날짜/아이콘 제거)
            new_log = f"[{ts}] BTC:{p_btc} | ETH:{p_eth} | XAU:{p_xau}"
            st.session_state.price_history.insert(0, new_log)
            st.session_state.price_history = st.session_state.price_history[:3]
            st.session_state.last_price_time = time.time()

        # D. 그래프 데이터
        daily_pnl = {}
        all_closed_data = get_more_closed_pnl(ss, pages=3) 
        for e in all_closed_data:
            dt = datetime.datetime.fromtimestamp(int(e['updatedTime'])/1000).strftime('%m.%d')
            daily_pnl[dt] = daily_pnl.get(dt, 0) + float(e['closedPnl'])
        
        chart_data = [{"date": (datetime.datetime.now() - datetime.timedelta(days=i)).strftime('%m.%d'), "pnl": daily_pnl.get((datetime.datetime.now() - datetime.timedelta(days=i)).strftime('%m.%d'), 0.0)} for i in range(13, -1, -1)]
        
        wallet_total = cash + unr_sum
        net_income = wallet_total - START_CASH
        rel_income = net_income - unr_sum

        return {
            "net": net_income, "wallet": wallet_total, "rel": rel_income, "unr": unr_sum,
            "active_pos": active_positions, "chart": chart_data
        }
    except: return None

# --- [3] UI 조립 ---
d = get_dashboard_data()

if d:
    def get_color(val): return "#00FF94" if val >= 0 else "#ff4b4b"
    def get_grad_color(val): return "#00713f" if val >= 0 else "#93000a"

    # 포지션 리스트 생성
    pos_html = ""
    for p in d['active_pos']:
        pnl_c = get_color(p['pnl'])
        dark_c = get_grad_color(p['pnl'])
        # 포지션 방향 색상 고정 (Buy=초록, Sell=빨강)
        side_c = "#00FF94" if p['side'] == "Buy" else "#ff4b4b"
        
        pos_html += f"""
        <div style="background:rgba(255,255,255,0.03); border-radius:12px; padding:18px; margin-bottom:12px; border:1px solid {pnl_c}22; position:relative; overflow:hidden;">
            <div style="position:absolute; left:0; top:0; width:5px; height:100%; background:linear-gradient(to bottom, {pnl_c}, {dark_c});"></div>
            <div style="display:flex; justify-content:space-between; align-items:center; padding-left:8px;">
                <div style="display:flex; align-items:center; gap:12px;">
                    <div style="background:rgba(255,255,255,0.05); padding:10px; border-radius:10px; display:flex;">
                        <span class="material-symbols-outlined" style="color:{pnl_c}; font-size:22px;">{"trending_up" if p['pnl']>=0 else "trending_down"}</span>
                    </div>
                    <div>
                        <div style="font-size:14px; font-weight:700; color:#ffffff; margin-bottom:6px;">{p['sym']}</div>
                        <div style="font-size:10px; color:#849586;">
                            <span style="color:{side_c}; font-weight:600;">({p['side']})</span> • Qty: {p['qty']}
                        </div>
                    </div>
                </div>
                <div style="text-align:right;">
                    <div style="color:{pnl_c}; font-weight:800; font-size:17px; margin-bottom:2px;">{p['pnl']:+.2f}$</div>
                    <div style="font-size:10px; color:#dae2fd; opacity:0.8;">Avg: {float(p['avg']):,.1f}</div>
                </div>
            </div>
        </div>
        """

    # 그래프 막대 생성
    max_p = max([abs(x['pnl']) for x in d['chart']] + [15.0])
    bars_html = "".join([f'<div style="flex:1; display:flex; flex-direction:column; align-items:center; height:100%; justify-content:flex-end;"><div style="font-size:6px; color:{get_color(x["pnl"])}; margin-bottom:3px; font-weight:bold;">{f"{x["pnl"]:+.1f}" if abs(x["pnl"])>0.1 else ""}</div><div style="width:14px; background:{get_color(x["pnl"])}; height:{max(min((abs(x["pnl"])/max_p)*60,60),1)}%; border-radius:1.5px; opacity:0.8;"></div><div style="font-size:7px; color:#555; margin-top:8px;">{x["date"]}</div></div>' for x in d['chart']])

    # 실시간 가격 로그 (슬림 & 13px)
    price_log_html = "".join([f'<div style="margin-bottom:5px; opacity:{"1" if i==0 else "0.5" if i==1 else "0.2"}; font-size:13px;">{log}</div>' for i, log in enumerate(st.session_state.price_history)])

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0" rel="stylesheet">
        <style>
            body {{ background-color: #0A0C10; margin: 0; padding: 0; font-family: 'Inter', sans-serif; color: #dae2fd; overflow-x: hidden; }}
            .container {{ max-width: 460px; margin: 0 auto; padding: 45px 15px 40px 15px; }}
            .glass-card {{ background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 20px; padding: 20px; margin-bottom: 20px; }}
            .label {{ font-size: 9px; color: #849586; text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 7px; font-weight: 500; }}
            .val-num {{ font-size: 20px; font-weight: 500; letter-spacing: -0.2px; }}
            .unit-small {{ font-size: 11px; font-weight: 400; opacity: 0.5; margin-left: 2px; }}
            .price-log-box {{ background: rgba(255,255,255,0.02); border-radius: 12px; padding: 15px; margin-bottom: 15px; font-family: 'Courier New'; color: #00FF94; border: 1px solid #222; line-height: 1.4; min-height: 70px; }}
            pre, code {{ background: transparent !important; color: inherit !important; padding: 0 !important; }}
            .material-symbols-outlined {{ font-family: 'Material Symbols Outlined' !important; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div style="display:flex; justify-content:space-between; align-items:center; padding: 0 5px 25px;">
                <span class="material-symbols-outlined" style="color:#00FF94; font-size:30px;">chevron_left</span>
                <div style="color:#00FF94; font-weight:900; font-size:22px; letter-spacing:3px;">RESULTS</div>
                <span class="material-symbols-outlined" style="font-size:26px;">notifications</span>
            </div>
            
            <div class="glass-card">
                <div style="display:flex; align-items:center;">
                    <div style="flex:1.7;">
                        <div class="label" style="font-size:9px; color:#849586; text-transform:uppercase; margin-bottom:7px;">Net / Wallet Income</div>
                        <div style="font-size:20px; font-weight:500;">
                            <span style="color:{get_color(d['net'])};">{d['net']:,.0f}<span class="unit-small"> USD</span></span> 
                            <span style="opacity:0.2; margin:0 3px;">/</span>
                            <span>{d['wallet']:,.0f}<span class="unit-small"> USD</span></span>
                        </div>
                    </div>
                    <div style="flex:1; border-left:1px solid rgba(255,255,255,0.08); padding-left:15px;">
                        <div class="label" style="font-size:9px; color:#849586; text-transform:uppercase; margin-bottom:7px;">Start Balance</div>
                        <div style="font-size:20px; font-weight:500;">50,000<span class="unit-small"> USD</span></div>
                    </div>
                </div>
            </div>

            <div style="display:flex; gap:10px; margin: 0 0 20px 0;">
                <div class="glass-card" style="flex:1; margin:0; text-align:center; padding:15px;">
                    <div class="label" style="font-size:9px; color:#849586; text-transform:uppercase; margin-bottom:7px;">Session Profit</div>
                    <div style="font-size:18px; font-weight:500; color:#bdf4ff;">{d['rel']:,.1f}<span style="font-size:10px; opacity:0.5;"> USD</span></div>
                </div>
                <div class="glass-card" style="flex:1; margin:0; text-align:center; padding:15px;">
                    <div class="label" style="font-size:9px; color:#849586; text-transform:uppercase; margin-bottom:7px;">Unrealized PnL</div>
                    <div style="font-size:18px; font-weight:500; color:{get_color(d['unr'])};">{d['unr']:,.1f}<span style="font-size:10px; opacity:0.5;"> USD</span></div>
                </div>
            </div>

            <div style="font-size:11px; font-weight:900; color:#849586; letter-spacing:1px; margin-bottom:15px;">ACTIVE POSITIONS</div>
            {pos_html if pos_html else "<div style='color:#444; text-align:center; padding:30px;'>진입 포지션 없음</div>"}

            <div style="font-size:10px; font-weight:900; color:#849586; letter-spacing:1px; margin: 15px 0 8px 5px;">LIVE PRICE MONITORING (30S)</div>
            <div class="price-log-box">{price_log_html if price_log_html else "시세 수신 중..."}</div>

            <div class="glass-card" style="margin-top:10px; padding:30px 10px 20px 10px;">
                <div style="font-size:10px; color:#849586; text-transform:uppercase; margin-bottom:30px;">Combined Revenue (14 Days)</div>
                <div style="height:140px; display:flex; align-items:flex-end; gap:5px; padding:0 5px;">{bars_html}</div>
            </div>
        </div>
    </body>
    </html>
    """
    components.html(full_html, height=1250, scrolling=False)
else:
    st.error("데이터 수신 대기 중...")