import streamlit as st
import pandas as pd
import requests
import datetime
import urllib3
import re
import plotly.graph_objects as go
from plotly.subplots import make_subplots

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(page_title="我的投資儀表板", layout="wide", page_icon="🏠")

# ==========================================
# 工具與抓取函式
# ==========================================
@st.cache_data(ttl=3600)
def fetch_kline_data(ticker):
    """抓取 Yahoo Finance 歷史資料 (自動調整除權息)"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    for suffix in ['.TW', '.TWO']:
        try:
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}{suffix}?range=6mo&interval=1d"
            res = requests.get(url, headers=headers, timeout=5)
            data = res.json()
            result = data.get('chart', {}).get('result')
            if result:
                timestamps = result[0]['timestamp']
                quote = result[0]['indicators']['quote'][0]
                # 使用 adjusted close (除權息修正後的收盤價)
                adj_close = result[0]['indicators']['adjclose'][0]['adjclose']
                df = pd.DataFrame({
                    'Close': adj_close,
                    'Open': quote['open'], 'High': quote['high'], 'Low': quote['low'],
                    'Volume': quote['volume']
                })
                df.index = pd.to_datetime(timestamps, unit='s') + pd.Timedelta(hours=8)
                df.index = df.index.normalize()
                return df.dropna()
        except: continue
    return pd.DataFrame()

@st.cache_data(ttl=3600)
def fetch_market_data(date_str, roc_date_str):
    """抓取證交所與櫃買中心大盤資料 (用來自動配對官方名稱)"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    df_list = []
    
    try:
        url_twse = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALL&response=json"
        res = requests.get(url_twse, headers=headers, verify=False, timeout=10).json()
        if res.get('stat') == 'OK':
            valid_tables = [t for t in res.get('tables', []) if '收盤價' in t.get('fields', []) and '證券代號' in t.get('fields', [])]
            if valid_tables:
                target = max(valid_tables, key=lambda x: len(x.get('data', [])))
                df = pd.DataFrame(target['data'], columns=target['fields'])
                df_clean = pd.DataFrame()
                df_clean['代碼'] = df.iloc[:, 0].str.strip()
                df_clean['商品'] = df.iloc[:, 1].str.strip()
                df_list.append(df_clean)
    except: pass

    try:
        url_tpex = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json&d={roc_date_str}"
        res = requests.get(url_tpex, headers=headers, verify=False, timeout=10).json()
        raw = res.get('aaData') or (res.get('tables', [{}])[0].get('data', []) if res.get('tables') else [])
        if raw:
            df = pd.DataFrame(raw)
            df_clean = pd.DataFrame()
            df_clean['代碼'] = df.iloc[:, 0].str.strip()
            df_clean['商品'] = df.iloc[:, 1].str.strip()
            df_list.append(df_clean)
    except: pass
    
    return pd.concat(df_list, ignore_index=True) if df_list else None

# ==========================================
# 介面與核心邏輯
# ==========================================
st.title("🏠 我的投資儀表板")
st.divider()

user_stocks_input = st.text_input("📝 持股清單 (格式如: 2317 鴻海, 1815 富喬)：", 
                                  value="2317 鴻海, 3481 群創, 1815 富喬, 1802 台玻, 009816")
selected_date = st.date_input("選擇日期", datetime.date.today())

# 1. 解析輸入，提取代碼與自訂名稱
pairs = [s.strip() for s in user_stocks_input.split(',')]
my_codes = []
user_name_map = {}
for p in pairs:
    c_match = re.search(r'\d{4,6}', p)
    if c_match:
        code = c_match.group()
        my_codes.append(code)
        name_part = p.replace(code, '').strip()
        if name_part:
            user_name_map[code] = name_part

target_ts = pd.Timestamp(selected_date).normalize()

with st.spinner('同步官方名稱與精準行情中...'):
    # 2. 抓取官方資料庫，用來自動補齊商品名稱
    df_all = fetch_market_data(selected_date.strftime('%Y%m%d'), f"{selected_date.year-1911}/{selected_date.strftime('%m/%d')}")
    api_name_map = {}
    if df_all is not None and not df_all.empty:
        api_name_map = dict(zip(df_all['代碼'], df_all['商品']))

    final_rows = []
    for code in my_codes:
        # 🌟 命名邏輯：官方 API 名稱 > 使用者輸入名稱 > 直接顯示代碼
        name = api_name_map.get(code, user_name_map.get(code, f"({code})"))
        
        df_k = fetch_kline_data(code)
        if not df_k.empty:
            if target_ts in df_k.index:
                k_today = df_k.loc[target_ts]
                past_data = df_k[df_k.index < target_ts]
            else:
                k_today = df_k.iloc[-1]
                past_data = df_k.iloc[:-1]
            
            if not past_data.empty:
                yest_close = past_data.iloc[-1]['Close']
                price = float(k_today['Close'])
                change = price - yest_close
                pct = (change / yest_close) * 100
                
                final_rows.append({
                    '代碼': code, 
                    '商品': name,
                    '成交': round(price, 2), 
                    '漲跌': round(change, 2), 
                    '漲幅%': round(pct, 2),
                    '成交量(張)': int(k_today['Volume'] / 1000)
                })

# --- 顯示持股表格 ---
if final_rows:
    df_final = pd.DataFrame(final_rows).sort_values(by='漲幅%', ascending=False)
    st.subheader(f"💡 {selected_date} 持股表現")
    st.dataframe(
        df_final, 
        hide_index=True, 
        use_container_width=True,
        column_config={
            "漲幅%": st.column_config.NumberColumn(format="%.2f %%"),
            "成交": st.column_config.NumberColumn(format="%.2f"),
            "漲跌": st.column_config.NumberColumn(format="%.2f")
        }
    )

    # --- 下半部 K 線圖 ---
    st.divider()
    selected_stock_str = st.selectbox("圖表分析：", [f"{r['代碼']} {r['商品']}" for _, r in df_final.iterrows()])
    if selected_stock_str:
        t_code = selected_stock_str.split()[0]
        t_name = selected_stock_str.split()[1]
        
        df_k = fetch_kline_data(t_code)
        if not df_k.empty:
            df_k['MA5'] = df_k['Close'].rolling(5).mean()
            df_k['MA20'] = df_k['Close'].rolling(20).mean()
            df_k['MA60'] = df_k['Close'].rolling(60).mean()
            
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_width=[0.2, 0.8],
                                subplot_titles=(f'{t_name} ({t_code}) 日K與均線', '成交量'))
            
            fig.add_trace(go.Candlestick(x=df_k.index, open=df_k['Open'], high=df_k['High'], 
                                         low=df_k['Low'], close=df_k['Close'], name='K線'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA5'], mode='lines', line=dict(color='purple'), name='MA5'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA20'], mode='lines', line=dict(color='orange'), name='MA20'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA60'], mode='lines', line=dict(color='blue'), name='MA60'), row=1, col=1)
            
            v_colors = ['red' if c >= o else 'green' for c, o in zip(df_k['Close'], df_k['Open'])]
            fig.add_trace(go.Bar(x=df_k.index, y=df_k['Volume'], marker_color=v_colors, name='成交量'), row=2, col=1)
            
            fig.update_layout(xaxis_rangeslider_visible=False, height=650, dragmode='drawline', 
                              newshape=dict(line_color='black', line_width=2))
            fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
            st.plotly_chart(fig, use_container_width=True, config={'modeBarButtonsToAdd': ['drawline', 'eraseshape']})
else:
    st.info("請輸入持股代碼並點選日期以顯示報價。")
