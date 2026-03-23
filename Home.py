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
# 雲端資料庫：Supabase 初始化
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

DEFAULT_HOLDINGS = "^TWII 加權指數, ^TWOII 櫃買指數, 2317 鴻海, 1802 台玻, 1717 長興, 4952 凌通, 2344 華邦電, 009816 凱基台灣Top50"

@st.cache_data(ttl=86400)
def load_stock_info_from_db():
    stock_dict = {}
    try:
        all_data = []
        step = 1000
        for i in range(0, 5000, step):
            response = supabase.table("stock_info").select("stock_id, stock_name, suffix").range(i, i + step - 1).execute()
            all_data.extend(response.data)
            if len(response.data) < step: break
        for row in all_data:
            sid = str(row['stock_id']).strip()
            stock_dict[sid] = {'name': str(row['stock_name']).strip(), 'suffix': str(row.get('suffix', '')).strip()}
    except Exception as e:
        st.error(f"無法載入股票清單: {e}")
    return stock_dict

COMMON_ETF_MAP = {
    "^TWII": "加權指數", "^TWOII": "櫃買指數",
    "0050": "元大台灣50", "0056": "元大高股息", "00878": "國泰永續高股息", 
    "00919": "群益台灣精選高息", "00929": "復華台灣科技優息", "00940": "元大台灣價值高息",
    "006208": "富邦台50", "00713": "元大台灣高息低波", "00679B": "元大美債20年"
}

def load_holdings():
    try:
        response = supabase.table("user_settings").select("value").eq("key", "holdings").execute()
        if response.data: return response.data[0]["value"]
    except Exception as e:
        st.warning(f"無法讀取雲端持股，將使用預設值。({e})")
    return DEFAULT_HOLDINGS

def save_holdings(holdings_str):
    try:
        supabase.table("user_settings").upsert({"key": "holdings", "value": holdings_str}).execute()
    except Exception as e:
        st.error(f"儲存持股至 Supabase 失敗: {e}")

# ==========================================
# 工具與抓取函式 (純淨 K 線版)
# ==========================================
@st.cache_data(ttl=3600)
def fetch_kline_data(ticker, specific_suffix=None):
    headers = {'User-Agent': 'Mozilla/5.0'}
    suffixes_to_try = [''] if ticker.startswith('^') else ([specific_suffix] if specific_suffix else ['.TW', '.TWO'])
    
    for suffix in suffixes_to_try:
        try:
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}{suffix}?range=6mo&interval=1d"
            res = requests.get(url, headers=headers, timeout=5).json()
            result = res.get('chart', {}).get('result')
            if result:
                quote = result[0]['indicators']['quote'][0]
                df = pd.DataFrame({
                    'Close': quote['close'], 'Open': quote['open'], 
                    'High': quote['high'], 'Low': quote['low'], 'Volume': quote['volume']
                })
                df.index = pd.to_datetime(result[0]['timestamp'], unit='s', utc=True)
                df.index = df.index.tz_convert('Asia/Taipei').tz_localize(None).normalize()
                
                df = df[~df.index.duplicated(keep='last')]
                df = df.dropna(subset=['Close']).ffill()
                
                meta = result[0].get('meta', {})
                if not df.empty and df['Volume'].iloc[-1] == 0:
                    reg_vol = meta.get('regularMarketVolume', 0)
                    if reg_vol > 0: df.iloc[-1, df.columns.get_loc('Volume')] = reg_vol
                
                return df, {} 
        except: 
            continue
    return pd.DataFrame(), {}

# ==========================================
# 介面與核心邏輯
# ==========================================
st.title("🏠 我的投資儀表板")
st.divider()

stock_db_dict = load_stock_info_from_db()
all_stock_options = [f"{k} {v['name']}" for k, v in stock_db_dict.items()]

if "holdings_list" not in st.session_state:
    raw_str = load_holdings()
    st.session_state.holdings_list = [s.strip() for s in raw_str.replace('、', ',').replace('，', ',').split(',') if s.strip()]

def add_selected_stock():
    selected = st.session_state.stock_selector
    if selected:
        if selected not in st.session_state.holdings_list:
            st.session_state.holdings_list.append(selected)
            st.toast(f"✅ 已將 {selected} 加入清單！")
        st.session_state.stock_selector = ""

col_search, col_add = st.columns([4, 1])
with col_search:
    st.selectbox("🔍 搜尋並新增持股 (請輸入代號或名稱)：", options=[""] + all_stock_options, key="stock_selector")
with col_add:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    st.button("➕ 新增至清單", use_container_width=True, on_click=add_selected_stock)

col_list, col_date, col_save = st.columns([5, 2, 2])
with col_list:
    safe_options = list(set(all_stock_options + st.session_state.holdings_list))
    st.session_state.holdings_list = st.multiselect("🏷️ 目前持股清單 (點選 'x' 可移除)：", options=safe_options, default=st.session_state.holdings_list)
with col_date:
    selected_date = st.date_input("選擇日期", datetime.date.today())
with col_save:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    if st.button("💾 儲存為預設", use_container_width=True):
        save_holdings(", ".join(st.session_state.holdings_list))
        st.success("✅ 持股清單已成功存檔至雲端！")

if selected_date.weekday() >= 5: st.warning(f"⚠️ 您選擇的日期 ({selected_date}) 是週末假日，將自動顯示最近一個交易日的資料。")

my_codes = []
final_parsed_names = {} 

for p in st.session_state.holdings_list:
    tokens = p.split()
    current_codes, name_tokens = [], []
    for t in tokens:
        if re.match(r'^\^?[A-Za-z]?\d{4,6}[A-Za-z]?$', t) or t in COMMON_ETF_MAP:
            current_codes.append(t)
            if t not in my_codes: my_codes.append(t)
        else: name_tokens.append(t)
    if current_codes and name_tokens: final_parsed_names[current_codes[-1]] = " ".join(name_tokens)
    elif current_codes: final_parsed_names[current_codes[-1]] = ""

target_ts = pd.Timestamp(selected_date).normalize()

with st.spinner('從雲端資料庫調閱資料與精算行情中...'):
    final_rows = []
    for code in my_codes:
        db_info = stock_db_dict.get(code, {})
        name = final_parsed_names.get(code) or db_info.get('name') or COMMON_ETF_MAP.get(code) or f"({code})"
        df_k, _ = fetch_kline_data(code, specific_suffix=db_info.get('suffix'))
        
        if not df_k.empty:
            df_k = df_k.sort_index()
            if target_ts in df_k.index:
                k_target = df_k.loc[target_ts]
                past_data = df_k[df_k.index < target_ts]
            else:
                k_target = df_k.iloc[-1]
                past_data = df_k.iloc[:-1]
            
            price = float(k_target['Close'])
            if not past_data.empty:
                yest_close = float(past_data.iloc[-1]['Close'])
            else:
                yest_close = float(k_target['Open'])
            
            change = price - yest_close
            pct = (change / yest_close) * 100
            vol = int(k_target['Volume'] / 1000)
            
            final_rows.append({
                '代碼': code, '商品': name,
                '開盤': round(float(k_target['Open']), 2), '最高': round(float(k_target['High']), 2),
                '最低': round(float(k_target['Low']), 2), '收盤': round(price, 2), 
                '漲跌': round(change, 2), '漲幅%': round(pct, 2), '成交量(張)': vol
            })

if final_rows:
    df_final = pd.DataFrame(final_rows)
    def custom_style(row):
        styles = []
        for col in row.index:
            css = ""
            if col == '收盤': css += "font-weight: bold; "
            if col in ['漲跌', '漲幅%']:
                if row[col] > 0: css += "color: #ff4b4b; " 
                elif row[col] < 0: css += "color: #1e7b1e; " 
            if row['漲幅%'] >= 9.85: css += "background-color: rgba(255, 75, 75, 0.2); "
            elif row['漲幅%'] <= -9.85: css += "background-color: rgba(0, 136, 0, 0.15); " 
            styles.append(css)
        return styles

    styled_df = df_final.style.apply(custom_style, axis=1)\
                  .format({"開盤": "{:.2f}", "最高": "{:.2f}", "最低": "{:.2f}", 
                           "收盤": "{:.2f}", "漲跌": "{:.2f}", "漲幅%": "{:.2f} %", "成交量(張)": "{:.0f}"})\
                  .hide(axis="index")\
                  .set_table_attributes('style="width: 100%; border-collapse: collapse; text-align: center;"')\
                  .set_table_styles([
                      {'selector': 'th', 'props': [('font-size', '18px'), ('text-align', 'center'), ('padding', '12px'), ('border-bottom', '2px solid #555')]},
                      {'selector': 'td', 'props': [('font-size', '16px'), ('text-align', 'center'), ('padding', '12px'), ('border-bottom', '1px solid #ddd')]}
                  ])
    
    st.subheader(f"💡 {selected_date} 盤勢與持股表現")
    st.markdown(styled_df.to_html(), unsafe_allow_html=True)
    st.divider()
    
    selected_stock_str = st.selectbox("圖表分析：", [f"{r['代碼']} {r['商品']}" for _, r in df_final.iterrows()])
    if selected_stock_str:
        t_code = selected_stock_str.split()[0]
        t_name = selected_stock_str.split()[1]
        df_k, _ = fetch_kline_data(t_code, specific_suffix=stock_db_dict.get(t_code, {}).get('suffix'))
        
        if not df_k.empty:
            df_k['MA5'] = df_k['Close'].rolling(5).mean()
            df_k['MA20'] = df_k['Close'].rolling(20).mean()
            df_k['MA60'] = df_k['Close'].rolling(60).mean()
            
            # --- 新增 CDP 計算區塊 ---
            prev_H = df_k['High'].shift(1)
            prev_L = df_k['Low'].shift(1)
            prev_C = df_k['Close'].shift(1)
            
            df_k['CDP'] = (prev_H + prev_L + 2 * prev_C) / 4
            df_k['AH'] = df_k['CDP'] + (prev_H - prev_L)
            df_k['NH'] = 2 * df_k['CDP'] - prev_L
            df_k['NL'] = 2 * df_k['CDP'] - prev_H
            df_k['AL'] = df_k['CDP'] - (prev_H - prev_L)
            # -------------------------
            
            # 📈 繪製 MACD 三子圖
            df_k['EMA12'] = df_k['Close'].ewm(span=12, adjust=False).mean()
            df_k['EMA26'] = df_k['Close'].ewm(span=26, adjust=False).mean()
            df_k['DIF'] = df_k['EMA12'] - df_k['EMA26']
            df_k['DEA'] = df_k['DIF'].ewm(span=9, adjust=False).mean()
            df_k['MACD_hist'] = df_k['DIF'] - df_k['DEA']
            
            fig = make_subplots(
                rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04, 
                row_heights=[0.5, 0.2, 0.3], 
                subplot_titles=(f'{t_name} ({t_code}) 日K與均線 (含今日CDP支撐壓力)', '成交量', 'MACD')
            )
            
            # Row 1: K線
            fig.add_trace(go.Candlestick(x=df_k.index, open=df_k['Open'], high=df_k['High'], low=df_k['Low'], close=df_k['Close'], name='K線'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA5'], mode='lines', line=dict(color='purple', width=1.5), name='MA5'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA20'], mode='lines', line=dict(color='orange', width=1.5), name='MA20'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['MA60'], mode='lines', line=dict(color='blue', width=1.5), name='MA60'), row=1, col=1)
            
            # --- 新增 CDP 水平線繪製區塊 ---
            # 取得最後一筆資料（最新的一天）的 CDP 數值
            latest_cdp = df_k.iloc[-1]
            
           # --- 新增 CDP 水平線繪製區塊 ---
            # 取得最後一筆資料（最新的一天）的 CDP 數值
            latest_cdp = df_k.iloc[-1]
            
            # 設定共用的文字樣式：位置靠右上方、加上白底半透明背景避免與K線重疊
            anno_kwargs = dict(
                annotation_position="top right", 
                annotation_bgcolor="rgba(255, 255, 255, 0.85)", # 85% 不透明的白底
                annotation_font_size=12
            )

            fig.add_hline(y=latest_cdp['AH'], line_dash="dot", line_color="rgba(255, 0, 0, 0.5)", annotation_text=f"AH: {latest_cdp['AH']:.2f}", annotation_font_color="red", **anno_kwargs, row=1, col=1)
            fig.add_hline(y=latest_cdp['NH'], line_dash="dot", line_color="rgba(255, 165, 0, 0.8)", annotation_text=f"NH: {latest_cdp['NH']:.2f}", annotation_font_color="#d97700", **anno_kwargs, row=1, col=1)
            fig.add_hline(y=latest_cdp['CDP'], line_dash="dash", line_color="rgba(128, 128, 128, 0.5)", annotation_text=f"CDP: {latest_cdp['CDP']:.2f}", annotation_font_color="#555555", **anno_kwargs, row=1, col=1)
            fig.add_hline(y=latest_cdp['NL'], line_dash="dot", line_color="rgba(144, 238, 144, 0.8)", annotation_text=f"NL: {latest_cdp['NL']:.2f}", annotation_font_color="#2ca02c", **anno_kwargs, row=1, col=1)
            fig.add_hline(y=latest_cdp['AL'], line_dash="dot", line_color="rgba(0, 128, 0, 0.5)", annotation_text=f"AL: {latest_cdp['AL']:.2f}", annotation_font_color="darkgreen", **anno_kwargs, row=1, col=1)
            
            # -------------------------------
            
            # Row 2: 成交量
            v_colors = ['red' if c >= o else 'green' for c, o in zip(df_k['Close'], df_k['Open'])]
            fig.add_trace(go.Bar(x=df_k.index, y=df_k['Volume'], marker_color=v_colors, name='成交量'), row=2, col=1)
            
            # Row 3: MACD
            macd_colors = ['red' if val >= 0 else 'green' for val in df_k['MACD_hist']]
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['DIF'], mode='lines', line=dict(color='black', width=1.5), name='DIF (快)'), row=3, col=1)
            fig.add_trace(go.Scatter(x=df_k.index, y=df_k['DEA'], mode='lines', line=dict(color='blue', width=1.5), name='DEA (慢)'), row=3, col=1)
            fig.add_trace(go.Bar(x=df_k.index, y=df_k['MACD_hist'], marker_color=macd_colors, name='MACD柱'), row=3, col=1)

            fig.update_layout(xaxis_rangeslider_visible=False, height=850, dragmode='drawline', newshape=dict(line_color='black', line_width=2))
            fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
            st.plotly_chart(fig, use_container_width=True, config={'modeBarButtonsToAdd': ['drawline', 'eraseshape']})
else:
    st.info("💡 週末或盤中尚未開盤查無資料，請點選上方日期切換至最近的交易日。")
