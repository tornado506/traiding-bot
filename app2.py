import io, os, re, datetime, streamlit as st
import pandas as pd
import numpy as np
import streamlit.components.v1 as components
from pybit.unified_trading import HTTP
from streamlit_autorefresh import st_autorefresh

# [1. 설정 및 API 연결 - User2 전용]
LOG_FILE = "user2_bybit7.log"
K, S = "MZxVDs6SlVHsDndKXE", "ba3OlKPxWgdMjEVddU844DrAREqWY3MgRVVv"
ss = HTTP(testnet=False, demo=True, api_key=K, api_secret=S)
ss.endpoint = "https://api-demo.bybit.com"

# User2 원금 설정
START_CASH = 50000.0 

st.set_page_config(page_title="User2 Sniper Monitor v17.6", layout="wide")
st_autorefresh(interval=5000, key="refresh_user2_v17_6")

# --- [2. 데이터 사이언스 로직] ---

def get_more_closed_pnl(ss_client, pages=3):
    """최대 200개씩 여러 페이지를 긁어와서 과거 데이터를 확보하는 함수"""
    all_results = []
    cursor = ""
    for _ in range(pages):
        try:
            res = ss_client.get_closed_pnl(category="linear", limit=200, cursor=cursor)['result']
            data_list = res.get('list', [])
            all_results.extend(data_list)
            cursor = res.get('nextPageCursor', "")
            if not cursor: break
        except: break
    return all_results

def get_dashboard_data():
    try:
        # A. 자산 정보
        bal = ss.get_wallet_balance(accountType="UNIFIED", coin="USDT")['result']['list'][0]
        cash = float(bal['coin'][0]['walletBalance'])
        
        # B. 현재 포지션 실시간 데이터
        pos = ss.get_positions(category="linear", settleCoin="USDT", limit=50)['result']['list']
        active_positions = []
        unrealized_sum = 0.0
        for p in pos:
            sz = float(p.get("size", 0))
            if sz > 0:
                pnl = float(p.get('unrealisedPnl', 0))
                unrealized_sum += pnl
                active_positions.append({
                    "sym": p['symbol'], "side": p['side'], "pnl": pnl,
                    "qty": sz, "avg": float(p['avgPrice']),
                    "color": "#00FF94" if pnl >= 0 else "#ff4b4b",
                    "icon": "trending_up" if p['side'] == "Buy" else "trending_down"
                })
        
        # C. 그래프 데이터 (★페이지네이션 적용: 600개 데이터 수집★)
        daily_pnl = {}
        all_closed_data = get_more_closed_pnl(ss, pages=3) 
        for e in all_closed_data:
            dt = datetime.datetime.fromtimestamp(int(e['updatedTime'])/1000).strftime('%m.%d')
            daily_pnl[dt] = daily_pnl.get(dt, 0) + float(e['closedPnl'])
        
        chart_data = []
        for i in range(13, -1, -1):
            d_str = (datetime.datetime.now() - datetime.timedelta(days=i)).strftime('%m.%d')
            chart_data.append({"date": d_str, "pnl": daily_pnl.get(d_str, 0.0)})
        
        current_wallet_total = cash + unrealized_sum
        return {
            "net": current_wallet_total - START_CASH,
            "wallet": current_wallet_total,
            "active_pos": active_positions, "chart": chart_data
        }
    except: return None

# --- [3. UI 조립] ---
d = get_dashboard_data()

if d:
    # 그래프 막대 생성 (v17.2 디자인)
    max_p = max([abs(x['pnl']) for x in d['chart']] + [10.0])
    bars_html = ""
    for x in d['chart']:
        h = max(min((abs(x['pnl']) / max_p) * 65, 65), 1)
        c = "#00FF94" if x['pnl'] >= 0 else "#ff4b4b"
        bars_html += f"""
        <div style="flex:1; display:flex; flex-direction:column; align-items:center; height:100%; justify-content:flex-end; min-width:0;">
            <div style="font-size:6px; color:{c}; font-weight:600; margin-bottom:4px; height:10px; line-height:10px; white-space:nowrap;">{f'{x["pnl"]:+.1f}' if abs(x["pnl"])>0.1 else ''}</div>
            <div style="width:14px; background:{c}; height:{h}%; border-radius:1.5px; opacity:0.8;"></div>
            <div style="font-size:7px; color:#555; margin-top:8px; font-weight:500;">{x['date']}</div>
        </div>
        """

    # 포지션 리스트
    pos_list_html = ""
    for p in d['active_pos']:
        pos_list_html += f"""
        <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.03); border-radius:12px; padding:14px 16px; margin-bottom:10px; border:1px solid {p['color']}22; border-left:3px solid {p['color']};">
            <div style="display:flex; align-items:center; gap:12px;">
                <div style="background:{p['color']}11; padding:8px; border-radius:8px; display:flex;"><span class="material-symbols-outlined" style="color:{p['color']}; font-size:20px;">{p['icon']}</span></div>
                <div>
                    <div style="font-size:13px; font-weight:500; color:#ffffff;">{p['sym']}</div>
                    <div style="font-size:10px; color:#849586;">{p['side']} • Qty: {p['qty']}</div>
                </div>
            </div>
            <div style="text-align:right;">
                <div style="color:{p['color']}; font-weight:500; font-size:15px;">{p['pnl']:+.2f}$</div>
                <div style="font-size:9px; color:#555;">Avg: {p['avg']:,.2f}</div>
            </div>
        </div>
        """

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0" rel="stylesheet">
        <style>
            body {{ background-color: #0A0C10; margin: 0; padding: 0; font-family: 'Inter', sans-serif; color: #dae2fd; overflow-x: hidden; }}
            .container {{ max-width: 460px; margin: 0 auto; padding: 45px 15px 120px 15px; }}
            .glass-card {{ background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 20px; padding: 20px; margin-bottom: 20px; }}
            pre, code {{ background: transparent !important; color: inherit !important; padding: 0 !important; }}
            .nav-bar {{ position: fixed; bottom: 0; left: 50%; transform: translateX(-50%); width: 100%; max-width: 460px; background: rgba(10,12,16,0.98); backdrop-filter: blur(20px); display: flex; justify-content: space-around; align-items: center; padding: 15px 0 35px; border-top: 1px solid rgba(255,255,255,0.1); z-index: 1000; }}
            .nav-item {{ display: flex; flex-direction: column; align-items: center; color: #444; font-size: 9px; gap: 5px; }}
            .nav-item.active {{ color: #00FF94; }}
            .fab {{ background:#00FF94; width:60px; height:60px; border-radius:30px; display:flex; justify-content:center; align-items:center; box-shadow: 0 0 30px rgba(0,255,148,0.4); border: 5px solid #0A0C10; margin-top: -50px; }}
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
                        <div style="font-size:9px; color:#849586; text-transform:uppercase; margin-bottom:7px;">Net / Wallet Income</div>
                        <div style="font-size:22px; font-weight:600;">
                            <span style="color:#00FF94;">{d['net']:,.0f}<span style="font-size:11px; opacity:0.6; margin-left:2px;">USD</span></span> 
                            <span style="opacity:0.2; margin:0 3px;">/</span>
                            <span>{d['wallet']:,.0f}<span style="font-size:11px; opacity:0.6; margin-left:2px;">USD</span></span>
                        </div>
                    </div>
                    <div style="flex:1; border-left:1px solid rgba(255,255,255,0.08); padding-left:15px;">
                        <div style="font-size:9px; color:#849586; text-transform:uppercase; margin-bottom:7px;">Start Balance</div>
                        <div style="font-size:22px; font-weight:600;">50,000<span style="font-size:11px; opacity:0.6; margin-left:2px;">USD</span></div>
                    </div>
                </div>
            </div>
            <div style="font-size:11px; font-weight:900; color:#849586; letter-spacing:1px; margin-bottom:15px;">ACTIVE POSITIONS</div>
            {pos_list_html if pos_list_html else "<div style='color:#444; text-align:center; padding:30px;'>진입 포지션 없음</div>"}
            <div class="glass-card" style="margin-top:20px; padding:30px 10px 20px 10px;">
                <div style="font-size:10px; color:#849586; text-transform:uppercase; margin-bottom:30px;">Revenue History (14 Days)</div>
                <div style="height:130px; display:flex; align-items:flex-end; gap:5px; padding:0 5px;">{bars_html}</div>
            </div>
        </div>
        <nav class="nav-bar">
            <div class="nav-item"><span class="material-symbols-outlined">dashboard</span><span>Home</span></div>
            <div class="nav-item active"><span class="material-symbols-outlined">query_stats</span><span>Stats</span></div>
            <div class="fab"><span class="material-symbols-outlined" style="color:#0A0C10; font-weight:900; font-size:32px;">rebase_edit</span></div>
            <div class="nav-item"><span class="material-symbols-outlined">explore</span><span>Explore</span></div>
            <div class="nav-item"><span class="material-symbols-outlined">account_tree</span><span>Logic</span></div>
        </nav>
    </body>
    </html>
    """
    components.html(full_html, height=1100, scrolling=False)
else:
    st.error("데이터 수신 대기 중...")