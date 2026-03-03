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
def convert_to_float(val):
    try:
        val_str = str(val).strip()
        if val_str in ['-', '', 'nan', 'None', '---', '除息', '除權']: return 0.0
        return float(val_str.replace(',', ''))
    except: return 0.0

@st.cache_data(ttl=3600)
def fetch_kline_data(ticker):
    """直接呼叫 Yahoo Public API 獲取最新的 K 線資料 (校準用)"""
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
                df = pd.DataFrame({
                    'Open': quote['open'], 'High': quote['high'], 'Low': quote['low'],
                    'Close': quote['close'], 'Volume': quote['volume']
                })
                df.index = pd.to_datetime(timestamps, unit='s') + pd.Timedelta(hours=8)
                df.index = df.index.normalize()
                df = df.dropna()
                if not df.empty: return df
        except: continue
    return pd.DataFrame()

@st.cache_data(ttl=3600)
def fetch_market_data(date_str, roc_date_str):
    headers = {'User-Agent': 'Mozilla/5.0'}
    df_list = []
    
    # 上市
    try:
        url_twse = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALL&response=json"
        res = requests.get(url_twse, headers=headers, verify=False, timeout=10).json()
        if res.get('stat') == 'OK':
            valid_tables = [t for t in res.get('tables', []) if '收盤價' in t.get('fields', [])]
            if valid_tables:
                target = max(valid_tables, key=lambda x: len(x.get('data', [])))
                df = pd.DataFrame(target['data'], columns=target['fields'])
                df_clean = pd.DataFrame()
                df_clean['代碼'] = df.iloc[:, 0].str.strip()
                df_clean['商品'] = df.iloc[:, 1].str.strip()
                df_list.append(df_clean)
    except: pass

    # 上櫃
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

def plot_kline(ticker, name):
    df = fetch_kline_data(ticker)
    if df.empty:
        st.warning(f"⚠️ 無法取得 {name} ({ticker}) 的歷史股價資料。")
        return
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_width=[0.2, 0.8])
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K線'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA5'], mode='lines', line=dict(color='purple'), name='MA5'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA20'], mode='lines', line=dict(color='orange'), name='MA20'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA60'], mode='lines', line=dict(color='blue'), name='MA60'), row=1, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='成交量'), row=2, col=1)
    fig.update_layout(xaxis_rangeslider_visible=False, height=650, dragmode='drawline', newshape=dict(line_color='black', line_width=2))
    st.plotly_chart(fig, use_container_width=True, config={'modeBarButtonsToAdd': ['drawline', 'eraseshape']})

# ==========================================
# 介面設計
# ==========================================
st.title("🏠 我的投資儀表板 (即時精準版)")
st.divider()

user_stocks_input = st.text_input("📝 持股清單：", value="2317 鴻海, 3481 群創, 1815 富喬, 1802 台玻, 009816")
selected_date = st.date_input("選擇日期", datetime.date.today())

my_codes = re.findall(r'\d{4,6}', user_stocks_input)
my_names = [n for n in re.sub(r'[A-Za-z0-9,\s]', ' ', user_stocks_input).split() if len(n) > 0]

with st.spinner('同步最新市場數據與飆股行情...'):
    df_all = fetch_market_data(selected_date.strftime('%Y%m%d'), f"{selected_date.year-1911}/{selected_date.strftime('%m/%d')}")

if df_all is not None:
    # 篩選持股
    df_my = df_all[df_all['代碼'].isin(my_codes) | df_all['商品'].isin(my_names)].copy()
    
    final_rows = []
    target_ts = pd.Timestamp(selected_date).normalize()
    
    for _, row in df_my.iterrows():
        code, name = row['代碼'], row['商品']
        df_k = fetch_kline_data(code)
        
        if not df_k.empty:
            # 優先找使用者選的那天，如果那天沒開盤就找最後一天
            if target_ts in df_k.index:
                k_today = df_k.loc[target_ts]
                past_data = df_k[df_k.index < target_ts]
            else:
                k_today = df_k.iloc[-1]
                past_data = df_k.iloc[:-1]
                
            if not past_data.empty:
                yest_close = past_data.iloc[-1]['Close']
                price = round(float(k_today['Close']), 2)
                change = round(price - yest_close, 2)
                pct = round((change / yest_close) * 100, 2)
                vol = int(k_today['Volume'] / 1000)
                
                final_rows.append({'代碼': code, '商品': name, '成交': price, '漲跌': change, '漲幅%': pct, '成交量(張)': vol})
        else:
            final_rows.append({'代碼': code, '商品': name, '成交': 0.0, '漲跌': 0.0, '漲幅%': 0.0, '成交量(張)': 0})

    df_final = pd.DataFrame(final_rows).sort_values(by='漲幅%', ascending=False)

    st.subheader(f"💡 {selected_date} 持股表現")
    st.dataframe(
        df_final, 
        hide_index=True, 
        use_container_width=True,
        column_config={
            "漲幅%": st.column_config.NumberColumn(format="%.2f %%"),
            "漲跌": st.column_config.NumberColumn(format="%.2f"),
            "成交": st.column_config.NumberColumn(format="%.2f")
        }
    )

    st.divider()
    if not df_final.empty:
        selected_stock = st.selectbox("圖表分析：", [f"{r['代碼']} {r['商品']}" for _, r in df_final.iterrows()])
        if selected_stock:
            plot_kline(selected_stock.split()[0], selected_stock.split()[1])
