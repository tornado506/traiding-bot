import io
import os
from datetime import datetime, timedelta, timezone

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from pybit.unified_trading import HTTP

matplotlib.use("Agg")

K, S = "O1QpHJH4wdsjPiCe1x", "XdHD6eGaNz1iSnLF0j6nVEETZMDiSvGnai75"
ss = HTTP(testnet=False, demo=True, api_key=K, api_secret=S)
ss.endpoint = "https://api-demo.bybit.com"

PAIR_CHART_SIZE = (15, 5.25)
HUNTING_CHART_SIZE = (15, 5.25)
PRICE_STYLE = {"label": "0.8rem", "value": "1.0rem"}
KLINE_COLS = ["ts", "o", "h", "l", "c", "v", "t"]

ZONES = {
    "BTCUSDT": [
        {"l": 67500, "h": 68800, "c": "green"},
        {"l": 71200, "h": 71800, "c": "gray"},
        {"l": 73500, "h": 75000, "c": "red"},
    ],
    "XAUTUSDT": [
        {"l": 4600, "h": 4650, "c": "green"},
        {"l": 4740, "h": 4770, "c": "gray"},
        {"l": 4810, "h": 4840, "c": "red"},
    ],
}

st.set_page_config(page_title="Sniper V9 Logic-Fix", layout="wide")
st.markdown(
    """
    <style>
    .stApp {background-color: #0e1117 !important;}
    [data-testid="stHeader"], [data-testid="stToolbar"] {display: none !important;}
    [data-testid="stAppViewContainer"], .block-container {padding-top: 0 !important; margin-top: 0 !important;}
    * {color: #ffffff !important; font-family: 'Courier New', monospace; text-decoration: none !important;}
    h1, h3, h4 {color: #00ffc8 !important; font-weight: bold !important;}
    .stMetric {background: #1a1c24; border: 1px solid #3e4452; padding: 15px; border-radius: 12px;}
    [data-testid="stMetricValue"] {color: #ffffff !important;}
    .stTable {background: #1a1c24 !important; border-radius: 8px;}
    th {color: #00ffc8 !important;}
    .clean-log {
        background-color: #000000 !important; color: #00ff00 !important;
        padding: 15px !important; border: 1px solid #00ffc8 !important;
        border-radius: 5px !important; white-space: pre-wrap !important;
        font-size: 14px !important; line-height: 1.5 !important;
    }
    div[data-baseweb="select"] > div {
        background-color: #000000 !important;
        color: #ffffff !important;
        border: 1px solid #3e4452 !important;
    }
    div[data-baseweb="select"] * {
        color: #ffffff !important;
    }
    div[data-baseweb="popover"] {
        background-color: #000000 !important;
    }
    div[data-baseweb="popover"] ul,
    div[data-baseweb="popover"] li,
    div[role="listbox"],
    div[role="option"] {
        background-color: #000000 !important;
        color: #ffffff !important;
    }
    hr {border-top: 1px solid #444;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=20, show_spinner=False)
def kline_df(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    rows = ss.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)["result"]["list"]
    return pd.DataFrame(rows, columns=KLINE_COLS).apply(pd.to_numeric)


@st.cache_data(ttl=2, show_spinner=False)
def last_price(symbol: str) -> float:
    ticker = ss.get_tickers(category="linear", symbol=symbol)["result"]["list"][0]
    return float(ticker["lastPrice"])


@st.cache_data(ttl=3, show_spinner=False)
def open_orders(symbol: str):
    return ss.get_open_orders(category="linear", symbol=symbol)["result"]["list"]


def fig_to_img(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
    plt.close(fig)
    buf.seek(0)
    return buf


def render_price(label: str, price: float):
    st.markdown(
        f"""
        <div style="margin-bottom:8px;">
            <div style="font-size:{PRICE_STYLE['label']}; opacity:0.9;">{label}</div>
            <div style="font-size:{PRICE_STYLE['value']}; font-weight:700;">${price:,.1f}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def draw_pair_chart(sym1: str, sym2: str, color1: str, color2: str, title: str, interval: str):
    try:
        d1 = kline_df(sym1, interval, 100).iloc[::-1]
        d2 = kline_df(sym2, interval, 100).iloc[::-1]
        d1["time"] = pd.to_datetime(d1["ts"], unit="ms") + pd.Timedelta(hours=9)
        d1["n"] = (d1["c"] - d1["c"].mean()) / d1["c"].std()
        d2["n"] = (d2["c"] - d2["c"].mean()) / d2["c"].std()

        fig, ax = plt.subplots(figsize=PAIR_CHART_SIZE)
        ax.grid(True, linestyle="--", alpha=0.3)
        if interval == "60":
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        else:
            ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=15))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.tick_params(axis="x", labelrotation=90, labelsize=7)
        ax.plot(d1["time"], d1["n"], label=sym1, color=color1, lw=1.2)
        ax.plot(d1["time"], d2["n"], label=sym2, color=color2, lw=1.2)
        for y, color in [(0, "black"), (2.0, "red"), (-2.0, "green")]:
            ax.axhline(y=y, color=color, ls=":" if y else "-", alpha=0.4 if y else 0.3, lw=1)
        ax.fill_between(d1["time"], d1["n"], d2["n"], color="gray", alpha=0.05)
        tf_label = "1H" if interval == "60" else "15M"
        ax.set_title(f"📈 {title} Z-Score Flow ({tf_label})", fontsize=11, fontweight="bold")
        ax.legend(loc="upper left", fontsize=8)
        return fig_to_img(fig)
    except Exception:
        return None


def draw_hunting_chart(symbol: str, name: str, current_price: float):
    try:
        df = kline_df(symbol, "60", 500)
        bin_size = 100 if "BTC" in symbol else 10
        df["pb"] = (df["c"] / bin_size).round() * bin_size
        vp = df.groupby("pb")["v"].sum()

        orders = []
        for item in open_orders(symbol):
            side, reduce_only = item["side"], item.get("reduceOnly", False)
            if not reduce_only:
                color, label = ("#28a745", "LONG") if side == "Buy" else ("#dc3545", "SHORT")
            else:
                color, label = ("#007bff", "L-CLOSE") if side == "Sell" else ("#6f42c1", "S-CLOSE")
            orders.append({"p": float(item["price"]), "c": color, "l": label})

        refs = list(vp.index) + [o["p"] for o in orders] + [current_price]
        y_min, y_max = min(refs) - (bin_size * 5), max(refs) + (bin_size * 5)
        vp = vp[(vp.index >= y_min) & (vp.index <= y_max)]

        fig, ax = plt.subplots(figsize=HUNTING_CHART_SIZE)
        order_prices = [o["p"] for o in orders]
        bar_colors = ["#000" if any(abs(p - b) <= bin_size / 2 for b in order_prices) else "#fff" for p in vp.index]
        ax.barh(vp.index, vp.values, color=bar_colors, edgecolor="#000", linewidth=0.5, height=bin_size * 0.7)

        for zone in ZONES.get(symbol, []):
            if zone["l"] < y_max and zone["h"] > y_min:
                ax.axhspan(max(zone["l"], y_min), min(zone["h"], y_max), color=zone["c"], alpha=0.08)

        max_vol = vp.max() if not vp.empty else 100
        for order in orders:
            ax.axhline(y=order["p"], color=order["c"], ls="-", lw=2, alpha=0.6)
            ax.text(
                max_vol * 1.01,
                order["p"],
                f" {order['l']}:{order['p']:,.1f} ",
                color="white",
                fontweight="bold",
                va="center",
                ha="left",
                fontsize=9,
                bbox=dict(facecolor=order["c"], edgecolor="none", boxstyle="round,pad=0.2"),
            )

        ax.set_ylim(y_min, y_max)
        ax.set_xlim(0, max_vol * 1.3)
        ax.set_title(f"📍 {name} Hunting Map", fontsize=12, fontweight="bold")
        return fig_to_img(fig)
    except Exception:
        return None


def render_hunting_section(symbol: str, name: str, sync_text: str):
    try:
        price = last_price(symbol)
        render_price(name, price)
        image = draw_hunting_chart(symbol, name, price)
        if image:
            st.image(image, use_container_width=True)
    except Exception:
        st.write(sync_text)


def draw_log(title: str, path: str, icon: str):
    st.markdown(f"#### {icon} {title}")
    if not os.path.exists(path):
        st.error(f"{path}를 찾을 수 없습니다.")
        return
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        st.markdown(f"<div class='clean-log'>{''.join(f.readlines()[-15:])}</div>", unsafe_allow_html=True)


def kst_now_str() -> str:
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S")


def render_hunting_block():
    render_hunting_section("BTCUSDT", "BITCOIN", "Syncing BTC...")
    st.markdown("<br>", unsafe_allow_html=True)
    render_hunting_section("XAUTUSDT", "GOLD", "Syncing GOLD...")


def render_pair_block():
    st.markdown("<hr>", unsafe_allow_html=True)
    title_col, time_col = st.columns([0.7, 0.3])
    with title_col:
        st.subheader("⚖️ Pair Correlation Monitor")
    with time_col:
        st.markdown(
            f"<div style='text-align:right; font-size:0.9rem; opacity:0.9;'>현재시각 적용 (KST): {kst_now_str()}</div>",
            unsafe_allow_html=True,
        )
    tf_option = st.selectbox(
        "타임프레임",
        options=["15분봉", "1시간봉"],
        index=0,
        key="pair_tf",
    )
    interval = "60" if tf_option == "1시간봉" else "15"
    for pair in [
        ("BTCUSDT", "ETHUSDT", "#007bff", "#dc3545", "CRYPTO"),
        ("XAUUSDT", "XAGUSDT", "#ffc107", "#6f42c1", "METALS"),
    ]:
        img = draw_pair_chart(*pair, interval)
        if img:
            st.image(img, use_container_width=True)
        st.markdown("<br>", unsafe_allow_html=True)


def render_log_block():
    st.markdown("<hr>", unsafe_allow_html=True)
    draw_log("Pair Trading Logs", "pair_bot.log", "⚖️")
    st.markdown("<br>", unsafe_allow_html=True)
    draw_log("Bybit5 (USDT)", "bybit5.log", "🤖")


if hasattr(st, "fragment"):
    @st.fragment(run_every="30s")
    def hunting_fragment():
        render_hunting_block()

    @st.fragment(run_every="20s")
    def pair_fragment():
        render_pair_block()

    @st.fragment(run_every="3s")
    def log_fragment():
        render_log_block()

    hunting_fragment()
    pair_fragment()
    log_fragment()
else:
    render_hunting_block()
    render_pair_block()
    render_log_block()