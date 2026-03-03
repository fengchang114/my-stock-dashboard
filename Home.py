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
def convert_to_int(val):
    try:
        if isinstance(val, (int, float)): return int(val)
        return int(str(val).replace(',', ''))
    except: return 0

def convert_to_float(val):
    try:
        val_str = str(val).strip()
        if val_str in ['-', '', 'nan', 'None', '---', '除息', '除權']: return 0.0
        return float(val_str.replace(',', ''))
    except: return 0.0

@st.cache_data(ttl=3600)
def fetch_market_data(date_str, roc_date_str):
    headers = {'User-Agent': 'Mozilla/5.0'}
    df_list = []
    
    # 上市
    try:
        url_twse = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALL&response=json"
        res = requests.get(url_twse, headers=headers, verify=False, timeout=10).json()
        if res.get('stat') == 'OK':
            req_cols = {'證券代號', '證券名稱', '收盤價', '漲跌(+/-)', '漲跌價差', '成交股數'}
            target_table = next((t for t in res.get('tables', []) if req_cols.issubset(set(t.get('fields', [])))), None)
            
            if target_table:
                df = pd.DataFrame(target_table['data'], columns=target_table['fields'])
                df = df[['證券代號', '證券名稱', '收盤價', '漲跌(+/-)', '漲跌價差', '成交股數']]
                df.columns = ['代碼', '商品', '成交', '漲跌符號', '漲跌價差', '成交量_股']
                def calc_change(row):
                    sign, val = str(row['漲跌符號']).lower(), str(row['漲跌價差'])
                    try:
                        v = float(val.replace(',', ''))
                        return v * -1 if 'green' in sign or '-' in sign else v
                    except: return 0.0
                df['漲跌'] = df.apply(calc_change, axis=1)
                df_list.append(df[['代碼', '商品', '成交', '漲跌', '成交量_股']])
    except: pass

    # 上櫃
    try:
        url_tpex = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json&d={roc_date_str}"
        res = requests.get(url_tpex, headers=headers, verify=False, timeout=10).json()
        raw_data = res.get('aaData', []) or (res.get('tables', [{}])[0].get('data', []) if res.get('tables') else [])
        if raw_data:
            df = pd.DataFrame(raw_data).iloc[:, [0, 1, 2, 3, 8]]
            df.columns = ['代碼', '商品', '成交', '漲跌', '成交量_股']
            df_list.append(df)
    except: pass
    
    if df_list: return pd.concat(df_list, ignore_index=True)
    return None

@st.cache_data(ttl=3600)
def fetch_kline_data(ticker):
    """🌟 繞過 yfinance 限流封鎖，直接呼叫 Yahoo Public API 獲取 K 線資料"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    # 輪流測試上市(.TW)與上櫃(.TWO)
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
                    'Open': quote['open'],
                    'High': quote['high'],
                    'Low': quote['low'],
                    'Close': quote['close'],
                    'Volume': quote['volume']
                })
                # 轉換為台灣時間並對齊日期
                df.index = pd.to_datetime(timestamps, unit='s') + pd.Timedelta(hours=8)
                df.index = df.index.normalize()
                df = df.dropna()
                
                if not df.empty:
                    return df
        except:
            continue
            
    return pd.DataFrame()

def plot_kline(ticker, name):
    df = fetch_kline_data(ticker)
        
    if df.empty:
        st.warning(f"⚠️ 無法取得 {name} ({ticker}) 的歷史股價資料，請稍後再試。")
        return

    # 🌟 新增 5 日均線 (MA5)，並保留原本的 MA20 與 MA60
    df['MA5'] = df['Close'].rolling(window=5).mean()
    df['MA20'] = df['Close'].rolling(window=20).mean()
    df['MA60'] = df['Close'].rolling(window=60).mean()

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                        vertical_spacing=0.03, subplot_titles=(f'{name} ({ticker}) 日K線與均線', '成交量'), 
                        row_width=[0.2, 0.7])

    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'],
                                 low=df['Low'], close=df['Close'], name='K線',
                                 increasing_line_color='red', decreasing_line_color='green'), row=1, col=1)
    
    # 🌟 畫出三條均線 (設定不同顏色以利辨識)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA5'], mode='lines', line=dict(color='purple', width=1.5), name='MA5 周線'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA20'], mode='lines', line=dict(color='orange', width=1.5), name='MA20 月線'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA60'], mode='lines', line=dict(color='blue', width=1.5), name='MA60 季線'), row=1, col=1)

    colors = ['red' if row['Close'] >= row['Open'] else 'green' for index, row in df.iterrows()]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name='成交量'), row=2, col=1)

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        height=650,
        margin=dict(l=20, r=20, t=40, b=20),
        dragmode='drawline',
        # 🌟 將畫筆顏色改為黑色 (black)，線條加粗，這樣會清楚很多
        newshape=dict(line_color='black', line_width=2, opacity=1)
    )
    
    fig.update_xaxes(rangeslider_visible=False, rangebreaks=[dict(bounds=["sat", "mon"])])

    st.plotly_chart(fig, use_container_width=True, config={
        'modeBarButtonsToAdd': ['drawline', 'eraseshape'],
        'displaylogo': False
    })

# ==========================================
# 首頁介面設計
# ==========================================
st.title("🏠 我的專屬投資儀表板")
st.markdown("歡迎回來！請在下方管理您的持股，點擊按鈕即可畫出專屬 K 線與趨勢線。")
st.divider()

col1, col2 = st.columns([2, 1])
with col1:
    user_stocks_input = st.text_input(
        "📝 編輯我的持股代碼 (可用代碼或加上名稱)：", 
        value="2317 鴻海, 3481 群創, 1815 富喬, 1802 台玻, 009816"
    )
with col2:
    selected_date = st.date_input("選擇看盤日期", datetime.date.today())
    run_button = st.button("🔄 更新今日報價", use_container_width=True)

my_codes = re.findall(r'\d{4,6}', user_stocks_input)
cleaned_names = re.sub(r'[A-Za-z0-9,\s]', ' ', user_stocks_input).split()
my_names = [n for n in cleaned_names if len(n) > 0]

query_date_str = selected_date.strftime('%Y%m%d')
roc_year = selected_date.year - 1911
roc_date_str = f"{roc_year}/{selected_date.strftime('%m/%d')}"

with st.spinner('正在獲取最新報價...'):
    df_all = fetch_market_data(query_date_str, roc_date_str)

df_my_stocks = pd.DataFrame()
if df_all is not None:
    df_all['商品'] = df_all['商品'].str.strip()
    df_all['代碼'] = df_all['代碼'].str.strip()
    
    # 排除長度 >= 6 碼的權證，但「豁免」使用者手動輸入的代碼 (保護 009816)
    cond_length = df_all['代碼'].str.len() < 6
    cond_user_explicit = df_all['代碼'].isin(my_codes)
    df_all = df_all[cond_length | cond_user_explicit].copy()
    
    df_all['成交量_股'] = df_all['成交量_股'].apply(convert_to_int)
    df_all['成交量_張'] = df_all['成交量_股'] // 1000
    df_all['成交'] = df_all['成交'].apply(convert_to_float)
    df_all['漲跌'] = df_all['漲跌'].apply(convert_to_float)

    def calc_pct(row):
        close, change = row['成交'], row['漲跌']
        prev_close = close - change
        if prev_close > 0: return round((change / prev_close) * 100, 2)
        return 0.0
    df_all['漲幅%'] = df_all.apply(calc_pct, axis=1)

    cond_code = df_all['代碼'].isin(my_codes)
    cond_name = df_all['商品'].apply(lambda x: any(n in x for n in my_names) if my_names else False)
    df_my_stocks = df_all[cond_code | cond_name].copy()

st.subheader("💡 今日持股表現")
if not df_my_stocks.empty:
    df_display = df_my_stocks[['代碼', '商品', '成交', '漲跌', '漲幅%', '成交量_張']].sort_values(by='漲幅%', ascending=False)
    st.dataframe(
        df_display, 
        hide_index=True, 
        use_container_width=True,
        column_config={
            "漲幅%": st.column_config.NumberColumn(format="%.2f %%"),
            "成交量_張": st.column_config.NumberColumn(format="%d 張")
        }
    )
else:
    st.info("今日無您的持股資料，請確認日期是否為假日或盤中未產生資料。")

st.divider()
st.subheader("📈 互動式 K 線與趨勢線分析")
st.markdown("💡 **操作秘訣**：\n"
            "1. **畫線**：點選右上角的「✏️ **Draw line**」，在圖表上拖曳。\n"
            "2. **刪除**：點選右上角的「🧹 **Erase active shape**」，然後點擊您畫的線即可清除！")

if not df_my_stocks.empty:
    stock_options = [f"{row['代碼']} {row['商品']}" for _, row in df_display.iterrows()]
    selected_stock = st.selectbox("選擇要查看圖表的股票：", stock_options)
    
    if selected_stock:
        target_ticker = selected_stock.split()[0]
        target_name = selected_stock.split()[1]
        
        with st.spinner(f"正在載入 {target_name} 的歷史K線..."):
            plot_kline(target_ticker, target_name)

