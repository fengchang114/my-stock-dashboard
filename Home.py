import streamlit as st
import pandas as pd
import requests
import datetime
import urllib3
import re
from supabase import create_client, Client
import plotly.graph_objects as go
from plotly.subplots import make_subplots

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="我的投資儀表板", layout="wide", page_icon="🏠")

# ==========================================
# 雲端資料庫：Supabase 初始化 (保留原架構)
# ==========================================
@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase: Client = init_connection()
except Exception as e:
    st.error(f"⚠️ Supabase 連線失敗，請檢查 .streamlit/secrets.toml 設定。錯誤訊息: {e}")
    st.stop()

# 🌟 預設持股清單
DEFAULT_HOLDINGS = "^TWII 加權指數, ^TWOII 櫃買指數, 2317 鴻海, 1802 台玻, 1717 長興, 4952 凌通, 2344 華邦電, 009816 凱基台灣Top50"

# ==========================================
# 工具函式
# ==========================================
@st.cache_data(ttl=3600)
def fetch_all_tw_stocks():
    """保留你原本的分頁抓取全台股邏輯"""
    all_data = []
    page = 0
    limit = 1000
    while True:
        res = supabase.table("tw_stocks").select("code, name").range(page*limit, (page+1)*limit-1).execute()
        if not res.data: break
        all_data.extend(res.data)
        if len(res.data) < limit: break
        page += 1
    return pd.DataFrame(all_data)

@st.cache_data(ttl=300)
def fetch_kline_data(stock_code):
    """【終極修復版】偷渡 Meta 屬性以解決 320% 漲幅 Bug"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    ticker = stock_code if stock_code.startswith('^') else f"{stock_code}.TW"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d"
    
    try:
        resp = requests.get(url, headers=headers, timeout=10, verify=False)
        json_data = resp.json()
        res = json_data['chart']['result'][0]
        meta = res.get('meta', {})
        
        df = pd.DataFrame({
            'Open': res['indicators']['quote'][0]['open'],
            'High': res['indicators']['quote'][0]['high'],
            'Low': res['indicators']['quote'][0]['low'],
            'Close': res['indicators']['quote'][0]['close'],
            'Volume': res['indicators']['quote'][0]['volume']
        }, index=pd.to_datetime([datetime.datetime.fromtimestamp(t) for t in res['timestamp']]))
        
        df.dropna(inplace=True)
        # 將關鍵 Meta 資訊存入 attrs
        df.attrs['prev_close'] = meta.get('chartPreviousClose')
        df.attrs['current_price'] = meta.get('regularMarketPrice')
        return df
    except:
        return pd.DataFrame()

# ==========================================
# 側邊欄與資料準備
# ==========================================
with st.sidebar:
    st.header("⚙️ 設定")
    user_input = st.text_area("自訂持股 (格式: 代號 名稱, ...)", DEFAULT_HOLDINGS, height=150)
    stocks = [s.strip().split(' ') for s in user_input.split(',') if s.strip()]
    if st.button("清除快取"):
        st.cache_data.clear()
        st.rerun()

# ==========================================
# 主介面：上方看板 (修正漲跌幅邏輯)
# ==========================================
cols = st.columns(len(stocks))
for i, (t_code, t_name) in enumerate(stocks):
    df_k = fetch_kline_data(t_code)
    if not df_k.empty:
        # 核心 Bug 修正：優先使用 Meta 的昨收價
        meta_prev = df_k.attrs.get('prev_close')
        price = df_k.attrs.get('current_price') or df_k['Close'].iloc[-1]
        
        # 如果 Meta 有給數值，則無視 K 線陣列最後一根，強制以此計算
        yest_close = float(meta_prev) if meta_prev else df_k['Close'].iloc[-2]
        
        change = price - yest_close
        pct = (change / yest_close) * 100
        cols[i].metric(t_name, f"{price:.2f}", f"{change:+.2f} ({pct:+.2f}%)")

# ==========================================
# 主介面：下方細節圖表 (MACD 3子圖)
# ==========================================
st.divider()
selected_stock = st.selectbox("選擇個股查看技術圖表", [f"{s[0]} {s[1]}" for s in stocks])
t_code, t_name = selected_stock.split(' ')
df_plot = fetch_kline_data(t_code)

if not df_plot.empty:
    # 計算指標
    df_plot['MA5'] = df_plot['Close'].rolling(5).mean()
    df_plot['MA20'] = df_plot['Close'].rolling(20).mean()
    df_plot['MA60'] = df_plot['Close'].rolling(60).mean()
    
    # MACD 計算
    exp1 = df_plot['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df_plot['Close'].ewm(span=26, adjust=False).mean()
    df_plot['DIF'] = exp1 - exp2
    df_plot['DEA'] = df_plot['DIF'].ewm(span=9, adjust=False).mean()
    df_plot['MACD_hist'] = df_plot['DIF'] - df_plot['DEA']

    # 繪製三子圖
    fig = make_subplots(
        rows=3, cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.03, 
        row_heights=[0.5, 0.15, 0.35],
        subplot_titles=(f'{t_name} K線/均線', '成交量', 'MACD')
    )

    # 1. K線
    fig.add_trace(go.Candlestick(x=df_plot.index, open=df_plot['Open'], high=df_plot['High'], low=df_plot['Low'], close=df_plot['Close'], name='K線'), row=1, col=1)
    for ma, color in zip(['MA5', 'MA20', 'MA60'], ['purple', 'orange', 'blue']):
        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot[ma], name=ma, line=dict(width=1, color=color)), row=1, col=1)

    # 2. 成交量
    v_colors = ['red' if c >= o else 'green' for c, o in zip(df_plot['Close'], df_plot['Open'])]
    fig.add_trace(go.Bar(x=df_plot.index, y=df_plot['Volume'], name='成交量', marker_color=v_colors), row=2, col=1)

    # 3. MACD
    m_colors = ['red' if v >= 0 else 'green' for v in df_plot['MACD_hist']]
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['DIF'], name='DIF', line=dict(color='black')), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['DEA'], name='DEA', line=dict(color='blue')), row=3, col=1)
    fig.add_trace(go.Bar(x=df_plot.index, y=df_plot['MACD_hist'], name='MACD柱', marker_color=m_colors), row=3, col=1)

    fig.update_layout(height=850, xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)
