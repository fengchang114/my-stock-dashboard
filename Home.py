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
    """抓取 Yahoo Finance 歷史資料作為校準基準"""
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
    """抓取證交所與櫃買中心大盤資料"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    df_list = []
    
    # 上市
    try:
        url_twse = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALL&response=json"
        res = requests.get(url_twse, headers=headers, verify=False, timeout=10).json()
        if res.get('stat') == 'OK':
            # 選取筆數最多的表 (通常是股票大表)
            valid_tables = [t for t in res.get('tables', []) if '收盤價' in t.get('fields', [])]
            if valid_tables:
                target = max(valid_tables, key=lambda x: len(x.get('data', [])))
                df = pd.DataFrame(target['data'], columns=target['fields'])
                # 重新映射欄位，以防證交所欄位名稱變動
                df_clean = pd.DataFrame()
                df_clean['代碼'] = df.iloc[:, 0].str.strip()
                df_clean['商品'] = df.iloc[:, 1].str.strip()
                df_clean['成交'] = df['收盤價'].apply(convert_to_float)
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
            df_clean['成交'] = df.iloc[:, 2].apply(convert_to_float)
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
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_width=[0.2, 0.7])
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K線'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA5'], mode='lines', line=dict(color='purple'), name='MA5'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA20'], mode='lines', line=dict(color='orange'), name='MA20'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA60'], mode='lines', line=dict(color='blue'), name='MA60'), row=1, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='成交量'), row=2, col=1)
    fig.update_layout(xaxis_rangeslider_visible=False, height=600, dragmode='drawline', newshape=dict(line_color='black'))
    st.plotly_chart(fig, use_container_width=True, config={'modeBarButtonsToAdd': ['drawline', 'eraseshape']})

# ==========================================
# 首頁介面
# ==========================================
st.title("🏠 我的投資儀表板")
st.divider()

user_stocks_input = st.text_input("📝 持股清單 (代碼或名稱)：", value="2317 鴻海, 3481 群創, 1815 富喬, 1802 台玻, 009816")
selected_date = st.date_input("選擇日期", datetime.date.today())

# 解析代碼與名稱
my_codes = re.findall(r'\d{4,6}', user_stocks_input)
my_names = [n for n in re.sub(r'[A-Za-z0-9,\s]', ' ', user_stocks_input).split() if len(n) > 0]

with st.spinner('同步最新報價與校準漲跌幅...'):
    df_all = fetch_market_data(selected_date.strftime('%Y%m%d'), f"{selected_date.year-1911}/{selected_date.strftime('%m/%d')}")

if df_all is not None:
    # 初步篩選
    df_my = df_all[df_all['代碼'].isin(my_codes) | df_all['商品'].isin(my_names)].copy()
    
    # 🌟 核心修正：利用 Yahoo 歷史資料重新精算 價格、漲跌、漲幅
    final_rows = []
    target_ts = pd.Timestamp(selected_date).normalize()
    
    for _, row in df_my.iterrows():
        code, name = row['代碼'], row['商品']
        df_k = fetch_kline_data(code)
        
        if not df_k.empty and target_ts in df_k.index:
            k_today = df_k.loc[target_ts]
            past_data = df_k[df_k.index < target_ts]
            
            if not past_data.empty:
                yest_close = past_data.iloc[-1]['Close']
                price = round(float(k_today['Close']), 2)
                change = round(price - yest_close, 2)
                pct = round((change / yest_close) * 100, 2)
                vol = int(k_today['Volume'] / 1000)
                
                final_rows.append({'代碼': code, '商品': name, '成交': price, '漲跌': change, '漲幅%': pct, '成交量(張)': vol})
        else:
            # 如果 Yahoo 沒資料 (例如權證)，保留原始資料但補齊欄位
            final_rows.append({'代碼': code, '商品': name, '成交': row['成交'], '漲跌': 0.0, '漲幅%': 0.0, '成交量(張)': 0})

    df_final = pd.DataFrame(final_rows).sort_values(by='漲幅%', ascending=False)

    st.subheader("💡 今日持股表現")
    st.dataframe(
        df_final, 
        hide_index=True, 
        use_container_width=True,
        column_config={
            "漲幅%": st.column_config.NumberColumn(format="%.2f %%"),
            "漲跌": st.column_config.NumberColumn(format="%.2f")
        }
    )

    st.divider()
    if not df_final.empty:
        selected_stock = st.selectbox("圖表分析：", [f"{r['代碼']} {r['商品']}" for _, r in df_final.iterrows()])
        plot_kline(selected_stock.split()[0], selected_stock.split()[1])
