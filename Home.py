import streamlit as st
import pandas as pd
import requests
import datetime
import urllib3
import re
import os
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="我的投資儀表板", layout="wide", page_icon="🏠")

# ==========================================
# 迷你資料庫：存取持股與「商品名稱記憶庫」
# ==========================================
HOLDINGS_FILE = "my_holdings.txt"
NAME_CACHE_FILE = "name_cache.json"  # 🌟 新增：專屬的記憶大腦
DEFAULT_HOLDINGS = "2317 鴻海, 3481 群創, 1815 富喬, 1802 台玻, 0050, 009816"

COMMON_ETF_MAP = {
    "0050": "元大台灣50", "0056": "元大高股息", "00878": "國泰永續高股息", 
    "00919": "群益台灣精選高息", "00929": "復華台灣科技優息", "00940": "元大台灣價值高息",
    "006208": "富邦台50", "00713": "元大台灣高息低波", "00679B": "元大美債20年"
}

def load_holdings():
    if os.path.exists(HOLDINGS_FILE):
        with open(HOLDINGS_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return DEFAULT_HOLDINGS

def save_holdings(holdings_str):
    with open(HOLDINGS_FILE, "w", encoding="utf-8") as f:
        f.write(holdings_str)

# 🌟 記憶大腦的讀取與存檔功能
def load_name_cache():
    if os.path.exists(NAME_CACHE_FILE):
        try:
            with open(NAME_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

def save_name_cache(cache_dict):
    with open(NAME_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_dict, f, ensure_ascii=False, indent=2)

# ==========================================
# 工具與抓取函式
# ==========================================
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
                meta = result[0].get('meta', {})
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
                df = df.dropna()
                
                # Yahoo 修正機制：挖出真實成交量 (修復 0050 成交量為 0 的 Bug)
                if not df.empty and df['Volume'].iloc[-1] == 0:
                    reg_vol = meta.get('regularMarketVolume', 0)
                    if reg_vol > 0:
                        df.iloc[-1, df.columns.get_loc('Volume')] = reg_vol
                        
                return df
        except: continue
    return pd.DataFrame()

@st.cache_data(ttl=3600)

def fetch_openapi_names_and_volumes():
    name_map = {}
    vol_map = {}
    headers = {'User-Agent': 'Mozilla/5.0'}

    try:
        # 🌟 關鍵破解：加上 verify=False 繞過雲端主機對台灣政府 SSL 憑證的檢查
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=15, verify=False).json()
        for row in res:
            code = str(row.get('Code', '')).strip()
            name_map[code] = str(row.get('Name', '')).strip()
            try: vol_map[code] = int(row.get('TradeVolume', 0)) // 1000
            except: pass
    except: pass

    try:
        # 🌟 同樣加上 verify=False
        res = requests.get("https://www.tpex.org.tw/openapi/v1/dlyquote", headers=headers, timeout=15, verify=False).json()
        for row in res:
            code = str(row.get('SecuritiesCompanyCode', '')).strip()
            name_map[code] = str(row.get('CompanyName', '')).strip()
            try: vol_map[code] = int(row.get('TradingVolume', 0)) // 1000
            except: pass
    except: pass

    return name_map, vol_map

# ==========================================
# 介面與核心邏輯
# ==========================================
st.title("🏠 我的投資儀表板 (終極記憶版)")
st.divider()

current_saved_holdings = load_holdings()

col1, col2, col3 = st.columns([4, 1, 1])
with col1:
    user_stocks_input = st.text_input("📝 持股清單 (輸入過一次名稱就會永久記憶)：", value=current_saved_holdings)
with col2:
    selected_date = st.date_input("選擇日期", datetime.date.today())
with col3:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    save_btn = st.button("💾 儲存為預設", use_container_width=True)

if selected_date.weekday() >= 5:
    st.warning(f"⚠️ 您選擇的日期 ({selected_date}) 是週末假日，將自動顯示最近一個交易日的資料。")

if save_btn:
    save_holdings(user_stocks_input)
    st.success("✅ 持股清單已成功存檔！")

# 🌟 載入記憶大腦
name_cache = load_name_cache()
cache_updated = False

# 解析輸入
pairs = [s.strip() for s in user_stocks_input.split(',')]
my_codes = []

for p in pairs:
    c_match = re.search(r'\d{4,6}[A-Za-z]?', p)
    if c_match:
        code = c_match.group()
        my_codes.append(code)
        
        # 看看代碼後面有沒有跟著文字
        name_part = p.replace(code, '').strip()
        if name_part:
            # 如果有輸入名字，就寫入記憶大腦
            name_cache[code] = name_part
            cache_updated = True

# 如果大腦有學到新單字，就存檔下來
if cache_updated:
    save_name_cache(name_cache)

target_ts = pd.Timestamp(selected_date).normalize()

with st.spinner('同步官方資料庫與精準歷史行情中...'):
    api_name_map, api_vol_map = fetch_openapi_names_and_volumes()

    final_rows = []
    for code in my_codes:
        # 🌟 找名字的最強順序：記憶大腦 > 內建 ETF 字典 > 政府官方 API > 殘酷的只顯示代碼
        name = name_cache.get(code) or COMMON_ETF_MAP.get(code) or api_name_map.get(code, f"({code})")
        
        df_k = fetch_kline_data(code)
        if not df_k.empty:
            if target_ts in df_k.index:
                k_target = df_k.loc[target_ts]
                past_data = df_k[df_k.index < target_ts]
            else:
                k_target = df_k.iloc[-1]
                past_data = df_k.iloc[:-1]
            
            if not past_data.empty:
                yest_close = past_data.iloc[-1]['Close']
                price = float(k_target['Close'])
                change = price - yest_close
                pct = (change / yest_close) * 100
                
                vol = int(k_target['Volume'] / 1000)
                if vol == 0 and code in api_vol_map:
                    vol = api_vol_map[code]
                
                final_rows.append({
                    '代碼': code, '商品': name,
                    '開盤': round(float(k_target['Open']), 2), '最高': round(float(k_target['High']), 2),
                    '最低': round(float(k_target['Low']), 2), '收盤': round(price, 2), 
                    '漲跌': round(change, 2), '漲幅%': round(pct, 2),
                    '成交量(張)': vol
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

