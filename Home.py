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
# 迷你資料庫：存取持股與動態記憶
# ==========================================
HOLDINGS_FILE = "my_holdings.txt"
NAME_CACHE_FILE = "name_cache.json" 

# 🌟 預設持股清單
DEFAULT_HOLDINGS = "^TWII 加權指數, ^TWOII 櫃買指數, 1717 長興, 1802 台玻, 2317 鴻海, 4952 凌通"

COMMON_ETF_MAP = {
    "^TWII": "加權指數", "^TWOII": "櫃買指數",
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
# 讀取本地官方名冊
# ==========================================
@st.cache_data(ttl=86400) 
def load_local_official_dictionary():
    name_map = {}
    if os.path.exists("STOCK_DAY_ALL.json"):
        try:
            with open("STOCK_DAY_ALL.json", "r", encoding="utf-8") as f:
                for row in json.load(f):
                    c, n = str(row.get('Code', '')).strip(), str(row.get('Name', '')).strip()
                    if c and n: name_map[c] = n
        except: pass

    if os.path.exists("dlyquote.json"):
        try:
            with open("dlyquote.json", "r", encoding="utf-8") as f:
                for row in json.load(f):
                    c, n = str(row.get('SecuritiesCompanyCode', '')).strip(), str(row.get('CompanyName', '')).strip()
                    if c and n: name_map[c] = n
        except: pass
    return name_map

# ==========================================
# 工具與抓取函式
# ==========================================
@st.cache_data(ttl=3600)
def fetch_kline_data(ticker):
    headers = {'User-Agent': 'Mozilla/5.0'}
    suffixes = ['.TW', '.TWO'] if not ticker.startswith('^') else ['']
    
    for suffix in suffixes:
        try:
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}{suffix}?range=6mo&interval=1d"
            res = requests.get(url, headers=headers, timeout=5).json()
            result = res.get('chart', {}).get('result')
            if result:
                meta = result[0].get('meta', {})
                quote = result[0]['indicators']['quote'][0]
                df = pd.DataFrame({
                    'Close': result[0]['indicators']['adjclose'][0]['adjclose'],
                    'Open': quote['open'], 'High': quote['high'], 'Low': quote['low'],
                    'Volume': quote['volume']
                })
                df.index = pd.to_datetime(result[0]['timestamp'], unit='s') + pd.Timedelta(hours=8)
                df.index = df.index.normalize()
                df = df.dropna()
                
                if not df.empty and df['Volume'].iloc[-1] == 0:
                    reg_vol = meta.get('regularMarketVolume', 0)
                    if reg_vol > 0: df.iloc[-1, df.columns.get_loc('Volume')] = reg_vol
                return df
        except: continue
    return pd.DataFrame()

# ==========================================
# 介面與核心邏輯
# ==========================================
st.title("🏠 我的投資儀表板")
st.divider()

current_saved_holdings = load_holdings()

col1, col2, col3 = st.columns([4, 1, 1])
with col1:
    user_stocks_input = st.text_input("📝 持股清單 (支援防呆輸入，空格/逗號皆可)：", value=current_saved_holdings)
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

official_name_map = load_local_official_dictionary()
name_cache = load_name_cache()
cache_updated = False

# 🌟 智慧防呆解析引擎
raw_str = user_stocks_input.replace('、', ',').replace('，', ',')
pairs = [s.strip() for s in raw_str.split(',')]
my_codes = []

for p in pairs:
    tokens = p.split()
    current_codes = []
    name_tokens = []
    
    for t in tokens:
        if re.match(r'^\^?[A-Za-z]?\d{4,6}[A-Za-z]?$', t) or t in COMMON_ETF_MAP:
            current_codes.append(t)
            if t not in my_codes:
                my_codes.append(t)
        else:
            name_tokens.append(t)
            
    if current_codes and name_tokens:
        target_code = current_codes[-1]
        name_part = " ".join(name_tokens)
        name_cache[target_code] = name_part
        cache_updated = True

if cache_updated: save_name_cache(name_cache)

target_ts = pd.Timestamp(selected_date).normalize()

with st.spinner('從本地字典庫調閱資料與精算行情中...'):
    final_rows = []
    for code in my_codes:
        name = name_cache.get(code) or COMMON_ETF_MAP.get(code) or official_name_map.get(code) or f"({code})"
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
                
                final_rows.append({
                    '代碼': code, '商品': name,
                    '開盤': round(float(k_target['Open']), 2), '最高': round(float(k_target['High']), 2),
                    '最低': round(float(k_target['Low']), 2), '收盤': round(price, 2), 
                    '漲跌': round(change, 2), '漲幅%': round(pct, 2), '成交量(張)': vol
                })

# --- 顯示持股表格 ---
if final_rows:
    df_final = pd.DataFrame(final_rows)
    
    def custom_style(row):
        styles = []
        for col in row.index:
            css = ""
            if col == '收盤':
                css += "font-weight: bold; "
            
            if col in ['漲跌', '漲幅%']:
                if row[col] > 0:
                    css += "color: #ff4b4b; " 
                elif row[col] < 0:
                    css += "color: #1e7b1e; " 
            
            if row['漲幅%'] >= 9.85:
                css += "background-color: rgba(255, 75, 75, 0.2); "
            elif row['漲幅%'] <= -9.85:
                css += "background-color: rgba(0, 136, 0, 0.15); " 
                
            styles.append(css)
        return styles

    # 🌟 透過 Pandas 的 set_table_styles 與 set_table_attributes 直接從底層灌入樣式
    styled_df = df_final.style.apply(custom_style, axis=1)\
                      .format({"開盤": "{:.2f}", "最高": "{:.2f}", "最低": "{:.2f}", 
                               "收盤": "{:.2f}", "漲跌": "{:.2f}", "漲幅%": "{:.2f} %", "成交量(張)": "{:.0f}"})\
                      .hide(axis="index")\
                      .set_table_attributes('style="width: 100%; border-collapse: collapse; text-align: center;"')\
                      .set_table_styles([
                          # 這裡可以盡情設定您要的字體大小，22px 或 30px 都絕對會生效！
                          {'selector': 'th', 'props': [('font-size', '20px'), ('text-align', 'center'), ('padding', '12px'), ('border-bottom', '2px solid #555')]},
                          {'selector': 'td', 'props': [('font-size', '18px'), ('text-align', 'center'), ('padding', '12px'), ('border-bottom', '1px solid #ddd')]}
                      ])
    
    st.subheader(f"💡 {selected_date} 盤勢與持股表現")
    
    # 🌟 捨棄 st.table()！直接把格式化好的 HTML 畫布丟給瀏覽器強制渲染！
    html_table = styled_df.to_html()
    st.markdown(html_table, unsafe_allow_html=True)

    # --- 下半部 K 線圖 ---

    # --- 下半部 K 線圖 ---
    # ... (下方 K 線圖區塊維持原樣即可)
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
    if selected_date.weekday() < 5:
        st.info("💡 查無資料。可能原因：\n1. 今日為國定假日未開盤\n2. 目前尚在盤中，資料尚未產出。")
    else:
        st.info("💡 週末查無資料，請點選上方日期切換至最近的交易日。")

