import streamlit as st
import pandas as pd
import requests
import datetime
import urllib3
import re
import os
import plotly.graph_objects as go
from plotly.subplots import make_subplots

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="我的投資儀表板", layout="wide", page_icon="🏠")

# ==========================================
# 迷你資料庫：存取持股清單
# ==========================================
HOLDINGS_FILE = "my_holdings.txt"
DEFAULT_HOLDINGS = "2317 鴻海, 3481 群創, 1815 富喬, 1802 台玻, 0050, 009816"

def load_holdings():
    if os.path.exists(HOLDINGS_FILE):
        with open(HOLDINGS_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return DEFAULT_HOLDINGS

def save_holdings(holdings_str):
    with open(HOLDINGS_FILE, "w", encoding="utf-8") as f:
        f.write(holdings_str)

# ==========================================
# 工具與抓取函式
# ==========================================
def convert_to_float(val):
    try:
        val_str = re.sub(r'<[^>]+>', '', str(val)).strip()
        if val_str in ['-', '', 'nan', 'None', '---', '除息', '除權', 'X']: return 0.0
        return float(val_str.replace(',', ''))
    except: return 0.0

@st.cache_data(ttl=3600)
def fetch_kline_data(ticker):
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
    headers = {'User-Agent': 'Mozilla/5.0'}
    df_list = []
    
    # 上市 (TWSE)
    try:
        url_twse = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALL&response=json"
        res = requests.get(url_twse, headers=headers, verify=False, timeout=10).json()
        if res.get('stat') == 'OK':
            valid_tables = [t for t in res.get('tables', []) if '收盤價' in t.get('fields', []) and '證券代號' in t.get('fields', [])]
            for target in valid_tables:
                title = target.get('title', '')
                if '公債' in title or '債券' in title: continue
                
                df = pd.DataFrame(target['data'], columns=target['fields'])
                sign_col = next((c for c in target['fields'] if '漲跌' in c and '價差' not in c), '漲跌(+/-)')
                
                if not set(['證券代號', '證券名稱', '收盤價', '成交股數']).issubset(df.columns): continue
                
                df_clean = pd.DataFrame()
                df_clean['代碼'] = df['證券代號'].str.strip()
                df_clean['商品'] = df['證券名稱'].str.strip()
                df_clean['收盤'] = df['收盤價'].apply(convert_to_float)
                df_clean['成交量_股'] = df['成交股數'].apply(convert_to_float)
                df_clean['開盤'] = df['開盤價'].apply(convert_to_float) if '開盤價' in df.columns else df_clean['收盤']
                df_clean['最高'] = df['最高價'].apply(convert_to_float) if '最高價' in df.columns else df_clean['收盤']
                df_clean['最低'] = df['最低價'].apply(convert_to_float) if '最低價' in df.columns else df_clean['收盤']
                
                if sign_col in df.columns and '漲跌價差' in df.columns:
                    def get_change(r):
                        sign = str(r[sign_col]).lower()
                        val = convert_to_float(r['漲跌價差'])
                        if 'green' in sign or '-' in sign: return -val
                        return val
                    df_clean['漲跌'] = df.apply(get_change, axis=1)
                else: df_clean['漲跌'] = 0.0
                
                df_list.append(df_clean)
    except: pass
    
    # 上櫃 (TPEx)
    try:
        url_tpex = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json&d={roc_date_str}"
        res = requests.get(url_tpex, headers=headers, verify=False, timeout=10).json()
        raw = res.get('aaData') or (res.get('tables', [{}])[0].get('data', []) if res.get('tables') else [])
        if raw:
            df = pd.DataFrame(raw)
            if len(df.columns) >= 9:
                df_clean = pd.DataFrame()
                df_clean['代碼'] = df.iloc[:, 0].str.strip()
                df_clean['商品'] = df.iloc[:, 1].str.strip()
                df_clean['收盤'] = df.iloc[:, 2].apply(convert_to_float)
                df_clean['漲跌'] = df.iloc[:, 3].apply(convert_to_float)
                df_clean['開盤'] = df.iloc[:, 4].apply(convert_to_float)
                df_clean['最高'] = df.iloc[:, 5].apply(convert_to_float)
                df_clean['最低'] = df.iloc[:, 6].apply(convert_to_float)
                df_clean['成交量_股'] = df.iloc[:, 8].apply(convert_to_float)
                df_list.append(df_clean)
    except: pass
    
    return pd.concat(df_list, ignore_index=True).drop_duplicates(subset=['代碼'], keep='last') if df_list else None

# ==========================================
# 介面與核心邏輯
# ==========================================
st.title("🏠 我的投資儀表板")
st.divider()

current_saved_holdings = load_holdings()

col1, col2, col3 = st.columns([4, 1, 1])
with col1:
    user_stocks_input = st.text_input("📝 持股清單：", value=current_saved_holdings)
with col2:
    selected_date = st.date_input("選擇日期", datetime.date.today())
with col3:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    save_btn = st.button("💾 儲存為預設", use_container_width=True)

# 🌟 新增：週末假日智慧警示
if selected_date.weekday() >= 5:
    st.warning(f"⚠️ 您選擇的日期 ({selected_date}) 是週末假日，台股未開盤喔！請選擇其他交易日。")

if save_btn:
    save_holdings(user_stocks_input)
    st.success("✅ 持股清單已成功存檔！下次開啟將自動讀取。")

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
    df_all = fetch_market_data(selected_date.strftime('%Y%m%d'), f"{selected_date.year-1911}/{selected_date.strftime('%m/%d')}")

    final_rows = []
    for code in my_codes:
        if df_all is not None and code in df_all['代碼'].values:
            row = df_all[df_all['代碼'] == code].iloc[0]
            name = row['商品'] if row['商品'] else user_name_map.get(code, f"({code})")
            open_p, high_p, low_p, close_p = row['開盤'], row['最高'], row['最低'], row['收盤']
            change_p = row['漲跌']
            vol = int(row['成交量_股'] // 1000)
            
            prev_close = close_p - change_p
            pct = round((change_p / prev_close) * 100, 2) if prev_close > 0 else 0.0
            
            final_rows.append({
                '代碼': code, '商品': name,
                '開盤': open_p, '最高': high_p, '最低': low_p, '收盤': close_p, 
                '漲跌': change_p, '漲幅%': pct, '成交量(張)': vol
            })
        else:
            name = user_name_map.get(code, f"({code})")
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
                        '代碼': code, '商品': name,
                        '開盤': round(float(k_today['Open']), 2), '最高': round(float(k_today['High']), 2),
                        '最低': round(float(k_today['Low']), 2), '收盤': round(price, 2), 
                        '漲跌': round(change, 2), '漲幅%': round(pct, 2),
                        '成交量(張)': int(k_today['Volume'] / 1000)
                    })

# --- 顯示持股表格 ---
if final_rows:
    df_final = pd.DataFrame(final_rows)
    
    st.subheader(f"💡 {selected_date} 持股表現")
    st.dataframe(
        df_final, hide_index=True, use_container_width=True,
        column_config={
            "開盤": st.column_config.NumberColumn(format="%.2f"),
            "最高": st.column_config.NumberColumn(format="%.2f"),
            "最低": st.column_config.NumberColumn(format="%.2f"),
            "收盤": st.column_config.NumberColumn(format="%.2f"),
            "漲跌": st.column_config.NumberColumn(format="%.2f"),
            "漲幅%": st.column_config.NumberColumn(format="%.2f %%"),
            "成交量(張)": st.column_config.NumberColumn(format="%d")
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
            
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_width=[0.2, 0.8], subplot_titles=(f'{t_name} ({t_code}) 日K與均線', '成交量'))
            fig.add_trace(go.Candlestick(x=df_k.index, open=df_k['Open'], high=df_k['High'], low=df_k['Low'], close=df_k['Close'], name='K線'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA5'], mode='lines', line=dict(color='purple'), name='MA5'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA20'], mode='lines', line=dict(color='orange'), name='MA20'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA60'], mode='lines', line=dict(color='blue'), name='MA60'), row=1, col=1)
            v_colors = ['red' if c >= o else 'green' for c, o in zip(df_k['Close'], df_k['Open'])]
            fig.add_trace(go.Bar(x=df_k.index, y=df_k['Volume'], marker_color=v_colors, name='成交量'), row=2, col=1)
            fig.update_layout(xaxis_rangeslider_visible=False, height=650, dragmode='drawline', newshape=dict(line_color='black', line_width=2))
            fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
            st.plotly_chart(fig, use_container_width=True, config={'modeBarButtonsToAdd': ['drawline', 'eraseshape']})
else:
    # 🌟 新增：針對平日無資料的溫馨提示
    if selected_date.weekday() < 5:
        st.info("💡 查無資料。可能原因：\n1. 今日為國定假日未開盤\n2. 目前尚在盤中，官方盤後資料（下午 2:00 後）尚未產出。")
    else:
        st.info("💡 週末查無官方盤後資料，請點選上方日期切換至最近的交易日（例如星期五）。")