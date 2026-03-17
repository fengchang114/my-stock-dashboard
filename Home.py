import streamlit as st
import pandas as pd
import requests
import datetime
import urllib3
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 忽略不安全的請求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(page_title="我的投資儀表板 2026", layout="wide", page_icon="🏠")

# ==========================================
# 核心邏輯：抓取 K 線與 Meta 資料
# ==========================================
@st.cache_data(ttl=300)
def fetch_kline_data(stock_code):
    """
    從 Yahoo Finance 抓取資料，並將 Meta 中的昨收價偷渡到 attrs
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    # 判斷是否為指數
    ticker = stock_code if stock_code.startswith('^') else f"{stock_code}.TW"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d"
    
    try:
        response = requests.get(url, headers=headers, timeout=10, verify=False)
        data = response.json()
        result = data['chart']['result'][0]
        
        # 提取時間戳與報價
        timestamps = result.get('timestamp', [])
        indicators = result['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'Open': indicators['open'],
            'High': indicators['high'],
            'Low': indicators['low'],
            'Close': indicators['close'],
            'Volume': indicators['volume']
        }, index=pd.to_datetime([datetime.datetime.fromtimestamp(t) for t in timestamps]))
        
        # 移除空值
        df.dropna(inplace=True)
        
        # --- 核心修復：偷渡 Meta 資訊 ---
        meta = result.get('meta', {})
        df.attrs['prev_close'] = meta.get('chartPreviousClose')
        df.attrs['current_price'] = meta.get('regularMarketPrice')
        
        return df
    except Exception as e:
        st.error(f"抓取 {stock_code} 失敗: {e}")
        return pd.DataFrame()

# ==========================================
# UI 介面
# ==========================================
st.title("📈 投資監測儀表板 (2026 版)")

# 持股設定 (你可以自行修改或讀取 Supabase)
holdings_input = st.text_input("輸入持股清單 (代號 名稱)", "2317 鴻海, 1802 台玻, 1717 長興, 4952 凌通, 2344 華邦電, ^TWII 加權指數")
holdings = [h.strip().split(' ') for h in holdings_input.split(',')]

# 選取顯示個股
selected_stock = st.selectbox("選擇查看個股細節", [f"{h[0]} {h[1]}" for h in holdings])
t_code, t_name = selected_stock.split(' ')

# 抓取資料
df_k = fetch_kline_data(t_code)

if not df_k.empty:
    # 1. 準備指標資料 (MA & MACD)
    df_k['MA5'] = df_k['Close'].rolling(5).mean()
    df_k['MA20'] = df_k['Close'].rolling(20).mean()
    df_k['MA60'] = df_k['Close'].rolling(60).mean()
    
    # MACD 計算
    exp1 = df_k['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df_k['Close'].ewm(span=26, adjust=False).mean()
    df_k['DIF'] = exp1 - exp2
    df_k['DEA'] = df_k['DIF'].ewm(span=9, adjust=False).mean()
    df_k['MACD_hist'] = df_k['DIF'] - df_k['DEA']

    # ==========================================
    # 2. 精算區塊：修復 320% 漲幅 Bug
    # ==========================================
    # 優先從 Meta 讀取「真實昨收」，若無才用 K 線最後一根
    meta_prev = df_k.attrs.get('prev_close')
    current_price = df_k.attrs.get('current_price') or df_k['Close'].iloc[-1]
    
    # 判定昨收價：
    # 如果 K 線最後一根日期就是今天，則昨收應取「前一根」或「Meta 的 prev_close」
    yest_close = float(meta_prev) if meta_prev else df_k['Close'].iloc[-2]
    
    change = current_price - yest_close
    pct = (change / yest_close) * 100
    
    # 顯示頂部卡片
    col1, col2, col3 = st.columns(3)
    col1.metric("當前股價", f"{current_price:.2f}", f"{change:+.2f} ({pct:+.2f}%)")
    col2.metric("真實昨收 (Meta)", f"{yest_close:.2f}")
    col3.metric("今日高/低", f"{df_k['High'].iloc[-1]:.2f} / {df_k['Low'].iloc[-1]:.2f}")

    # ==========================================
    # 3. Plotly 三子圖繪製
    # ==========================================
    fig = make_subplots(
        rows=3, cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.05, 
        row_heights=[0.5, 0.2, 0.3],
        subplot_titles=(f'{t_name} ({t_code}) K線與均線', '成交量', 'MACD')
    )

    # Row 1: K線與均線
    fig.add_trace(go.Candlestick(
        x=df_k.index, open=df_k['Open'], high=df_k['High'], low=df_k['Low'], close=df_k['Close'], name='K線'
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA5'], name='MA5', line=dict(color='purple', width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA20'], name='MA20', line=dict(color='orange', width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA60'], name='MA60', line=dict(color='blue', width=1)), row=1, col=1)

    # Row 2: 成交量
    v_colors = ['red' if c >= o else 'green' for c, o in zip(df_k['Close'], df_k['Open'])]
    fig.add_trace(go.Bar(x=df_k.index, y=df_k['Volume'], name='成交量', marker_color=v_colors), row=2, col=1)

    # Row 3: MACD
    macd_colors = ['red' if val >= 0 else 'green' for val in df_k['MACD_hist']]
    fig.add_trace(go.Scatter(x=df_k.index, y=df_k['DIF'], name='DIF', line=dict(color='black', width=1.5)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df_k.index, y=df_k['DEA'], name='DEA', line=dict(color='blue', width=1)), row=3, col=1)
    fig.add_trace(go.Bar(x=df_k.index, y=df_k['MACD_hist'], name='MACD柱', marker_color=macd_colors), row=3, col=1)

    # 圖表設定
    fig.update_layout(height=800, xaxis_rangeslider_visible=False, showlegend=True)
    st.plotly_chart(fig, use_container_width=True)

else:
    st.warning("查無資料，請確認代號是否正確。")

# 強制清除快取按鈕
if st.button("清除快取並重新整理"):
    st.cache_data.clear()
    st.rerun()
