import io, os, re, datetime, streamlit as st
import pandas as pd
import numpy as np
import streamlit.components.v1 as components
from pybit.unified_trading import HTTP
from streamlit_autorefresh import st_autorefresh

# [1. 설정 및 연결]
K1, S1 = "O1QpHJH4wdsjPiCe1x", "XdHD6eGaNz1iSnLF0j6nVEETZMDiSvGnai75"
ss1 = HTTP(testnet=False, demo=True, api_key=K1, api_secret=S1)
ss1.endpoint = "https://api-demo.bybit.com"

K2, S2 = "PdCEy7g1TSfU5SY5aQ", "lYE2y2y7c79wG3K0kntZiwgwDwhdHY6mNUHo"
ss2 = HTTP(testnet=False, demo=True, api_key=K2, api_secret=S2)
ss2.endpoint = "https://api-demo.bybit.com"

START_CASH_MAIN = 50000.0
START_CASH_SUB  = 5000.0
LOG_FILES = {"MAIN_LOG": "bybit7.log"}
FEE_RATE = 0.0006 

PAIR_GROUPS = [
    ("BTCUSDT", "ETHUSDT", "CRYPTO PAIR"),
    ("XAUUSDT", "XAGUSDT", "METALS PAIR"),
    ("SOLUSDT", "AVAXUSDT", "ALTCOINS PAIR")
]

st.set_page_config(page_title="Sniper Bot Monitor v17.8", layout="wide")
st_autorefresh(interval=5000, key="refresh_final_v17_8")

# --- [2. 데이터 사이언스 로직] ---

def get_more_closed_pnl(ss_client, pages=3):
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

def get_full_dashboard_data():
    try:
        bal1 = ss1.get_wallet_balance(accountType="UNIFIED", coin="USDT")['result']['list'][0]
        cash1 = float(bal1['coin'][0]['walletBalance'])
        pos1 = ss1.get_positions(category="linear", settleCoin="USDT", limit=50)['result']['list']
        pnl1_sum = sum(float(p.get('unrealisedPnl', 0)) for p in pos1 if float(p.get("size", 0)) > 0)

        bal2 = ss2.get_wallet_balance(accountType="UNIFIED", coin="USDT")['result']['list'][0]
        cash2 = float(bal2['coin'][0]['walletBalance'])
        pos2 = ss2.get_positions(category="linear", settleCoin="USDT", limit=50)['result']['list']
        pnl2_sum = sum(float(p.get('unrealisedPnl', 0)) for p in pos2 if float(p.get("size", 0)) > 0)

        total_wallet = (cash1 + pnl1_sum) + (cash2 + pnl2_sum)
        total_net = total_wallet - (START_CASH_MAIN + START_CASH_SUB)

        display_items = []
        for p in pos1:
            if float(p.get("size", 0)) > 0:
                display_items.append({"is_pair": False, "sym": p['symbol'], "side": p['side'], "pnl": float(p['unrealisedPnl']), "qty": p['size'], "avg": p['avgPrice']})
        
        sub_pos_map = {p['symbol']: p for p in pos2 if float(p.get("size", 0)) > 0}
        processed_sub_syms = set()
        for sym_a, sym_b, label in PAIR_GROUPS:
            if sym_a in sub_pos_map or sym_b in sub_pos_map:
                pa, pb = sub_pos_map.get(sym_a), sub_pos_map.get(sym_b)
                display_items.append({
                    "is_pair": True, "label": label,
                    "sym_a": sym_a, "side_a": pa['side'] if pa else "--", "pnl_a": float(pa['unrealisedPnl']) if pa else 0.0,
                    "sym_b": sym_b, "side_b": pb['side'] if pb else "--", "pnl_b": float(pb['unrealisedPnl']) if pb else 0.0
                })
                processed_sub_syms.update([sym_a, sym_b])
        for sym, p in sub_pos_map.items():
            if sym not in processed_sub_syms:
                display_items.append({"is_pair": False, "sym": sym, "side": p['side'], "pnl": float(p['unrealisedPnl']), "qty": p['size'], "avg": p['avgPrice']})

        daily_pnl = {}
        for s in [ss1, ss2]:
            all_closed_data = get_more_closed_pnl(s, pages=3) 
            for e in all_closed_data:
                dt = datetime.datetime.fromtimestamp(int(e['updatedTime'])/1000).strftime('%m.%d')
                daily_pnl[dt] = daily_pnl.get(dt, 0) + float(e['closedPnl'])
        
        chart_data = []
        for i in range(13, -1, -1):
            d_str = (datetime.datetime.now() - datetime.timedelta(days=i)).strftime('%m.%d')
            chart_data.append({"date": d_str, "pnl": daily_pnl.get(d_str, 0.0)})

        return {"net": total_net, "wallet": total_wallet, "items": display_items, "chart": chart_data}
    except: return None

# --- [3] UI 조립 ---
d = get_full_dashboard_data()

if d:
    def get_color(val): return "#00FF94" if val >= 0 else "#ff4b4b"

    pos_html = ""
    for item in d['items']:
        if item['is_pair']:
            total_pnl = item['pnl_a'] + item['pnl_b']
            border_c = get_color(total_pnl)
            color_a = get_color(item['pnl_a'])
            color_b = get_color(item['pnl_b'])
            
            pos_html += f"""
            <div style="background:rgba(255,255,255,0.03); border-radius:16px; padding:16px; margin-bottom:12px; border:1px solid {border_c}44; border-left:4px solid {border_c};">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <div style="font-size:13px; font-weight:700; color:#ffffff;">{item['label']}</div>
                        <div style="font-size:10px; color:#849586; margin-top:2px;">
                            <span style="color:{color_a}">{item['sym_a']}</span><span style="color:#ffffff;">({item['side_a']})</span> 
                            <span style="color:#555; margin:0 4px;">/</span> 
                            <span style="color:{color_b}">{item['sym_b']}</span><span style="color:#ffffff;">({item['side_b']})</span>
                        </div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-weight:800; font-size:16px;">
                            <span style="color:{color_a};">{item['pnl_a']:+.1f}</span> 
                            <span style="color:#555; margin:0 4px;">/</span> 
                            <span style="color:{color_b};">{item['pnl_b']:+.1f}$</span>
                        </div>
                        <div style="font-size:9px; color:#849586;">Total: {total_pnl:+.2f}$</div>
                    </div>
                </div>
            </div>
            """
        else:
            color = get_color(item['pnl'])
            pos_html += f"""
            <div style="background:rgba(255,255,255,0.03); border-radius:16px; padding:16px; margin-bottom:12px; border:1px solid {color}44; border-left:4px solid {color};">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="display:flex; align-items:center; gap:12px;">
                        <div style="background:rgba(255,255,255,0.05); padding:8px; border-radius:8px; display:flex;">
                            <span class="material-symbols-outlined" style="color:{color}; font-size:20px;">{"trending_up" if item['pnl']>=0 else "trending_down"}</span>
                        </div>
                        <div>
                            <div style="font-size:13px; font-weight:700; color:#ffffff;">{item['sym']}</div>
                            <div style="font-size:10px; color:#849586;"><span style="color:#ffffff;">({item['side']})</span> • Qty: {item['qty']}</div>
                        </div>
                    </div>
                    <div style="text-align:right;">
                        <div style="color:{color}; font-weight:800; font-size:16px;">{item['pnl']:+.2f}$</div>
                        <div style="font-size:9px; color:#555;">Avg: {float(item['avg']):.1f}</div>
                    </div>
                </div>
            </div>
            """

    max_p = max([abs(x['pnl']) for x in d['chart']] + [15.0])
    bars_html = ""
    for x in d['chart']:
        h = max(min((abs(x['pnl']) / max_p) * 60, 60), 1)
        c = get_color(x['pnl'])
        bars_html += f'<div style="flex:1; display:flex; flex-direction:column; align-items:center; height:100%; justify-content:flex-end;"><div style="font-size:6px; color:{c}; margin-bottom:3px; font-weight:bold;">{f"{x["pnl"]:+.1f}" if abs(x["pnl"])>0.1 else ""}</div><div style="width:14px; background:{c}; height:{h}%; border-radius:1.5px; opacity:0.8;"></div><div style="font-size:7px; color:#555; margin-top:8px;">{x["date"]}</div></div>'

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
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
                        <div style="font-size:9px; color:#849586; text-transform:uppercase; margin-bottom:7px;">Net / Wallet Income (Unified)</div>
                        <div style="font-size:22px; font-weight:600;">
                            <span style="color:#00FF94;">{d['net']:,.0f}<span style="font-size:11px; opacity:0.6; margin-left:2px;">USD</span></span> 
                            <span style="opacity:0.2; margin:0 3px;">/</span>
                            <span>{d['wallet']:,.0f}<span style="font-size:11px; opacity:0.6; margin-left:2px;">USD</span></span>
                        </div>
                    </div>
                    <div style="flex:1; border-left:1px solid rgba(255,255,255,0.08); padding-left:15px;">
                        <div style="font-size:9px; color:#849586; text-transform:uppercase; margin-bottom:7px;">Start Balance</div>
                        <div style="font-size:22px; font-weight:600;">55,000<span style="font-size:11px; opacity:0.6; margin-left:2px;">USD</span></div>
                    </div>
                </div>
            </div>
            <div style="font-size:11px; font-weight:900; color:#849586; letter-spacing:1px; margin-bottom:15px;">ACTIVE POSITIONS (MAIN + SUB)</div>
            {pos_html if pos_html else "<div style='color:#444; text-align:center; padding:30px;'>진입 포지션 없음</div>"}
            <div class="glass-card" style="margin-top:20px; padding:30px 10px 20px 10px;">
                <div style="font-size:10px; color:#849586; text-transform:uppercase; margin-bottom:30px;">Combined Revenue (14 Days)</div>
                <div style="height:140px; display:flex; align-items:flex-end; gap:5px; padding:0 5px;">{bars_html}</div>
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
    st.error("API 연결 실패. 계정 상태를 확인하세요.")