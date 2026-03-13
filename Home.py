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

# 🌟 預設持股清單
DEFAULT_HOLDINGS = "^TWII 加權指數, ^TWOII 櫃買指數, 2317 鴻海, 1802 台玻, 1717 長興, 4952 凌通, 2344 華邦電, 009816 凱基台灣Top50"

# ==========================================
# 從 Supabase 抓取全台股清單與後綴 (突破千筆限制版)
# ==========================================
@st.cache_data(ttl=86400)
def load_stock_info_from_db():
    """
    從 stock_info 資料表抓取代號、名稱與後綴(suffix)
    使用分頁機制 (Pagination)，確保 2000+ 檔台股都能完整載入
    """
    stock_dict = {}
    try:
        all_data = []
        step = 1000
        # 迴圈分批抓取，每次抓 1000 筆，最多抓到 5000 筆為止 (涵蓋台股綽綽有餘)
        for i in range(0, 5000, step):
            response = supabase.table("stock_info").select("stock_id, stock_name, suffix").range(i, i + step - 1).execute()
            all_data.extend(response.data)
            
            # 如果這次抓到的資料少於 1000 筆，代表已經抓到底了，提早結束迴圈
            if len(response.data) < step:
                break
                
        # 將完整資料組裝成字典
        for row in all_data:
            sid = str(row['stock_id']).strip()
            stock_dict[sid] = {
                'name': str(row['stock_name']).strip(),
                'suffix': str(row.get('suffix', '')).strip()
            }
    except Exception as e:
        st.error(f"無法載入股票清單: {e}")
    return stock_dict

# 常見 ETF 或指數的備用對應
COMMON_ETF_MAP = {
    "^TWII": "加權指數", "^TWOII": "櫃買指數",
    "0050": "元大台灣50", "0056": "元大高股息", "00878": "國泰永續高股息", 
    "00919": "群益台灣精選高息", "00929": "復華台灣科技優息", "00940": "元大台灣價值高息",
    "006208": "富邦台50", "00713": "元大台灣高息低波", "00679B": "元大美債20年"
}

# ==========================================
# 持股設定存取 (Supabase user_settings 表)
# ==========================================
def load_holdings():
    try:
        response = supabase.table("user_settings").select("value").eq("key", "holdings").execute()
        if response.data:
            return response.data[0]["value"]
    except Exception as e:
        st.warning(f"無法讀取雲端持股，將使用預設值。({e})")
    return DEFAULT_HOLDINGS

def save_holdings(holdings_str):
    try:
        supabase.table("user_settings").upsert({"key": "holdings", "value": holdings_str}).execute()
    except Exception as e:
        st.error(f"儲存持股至 Supabase 失敗: {e}")

# ==========================================
# 工具與抓取函式 (精準 suffix 優化版)
# ==========================================
@st.cache_data(ttl=3600)
def fetch_kline_data(ticker, specific_suffix=None):
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # 如果有明確的 suffix，就只抓一次；否則維持盲猜邏輯
    if ticker.startswith('^'):
        suffixes_to_try = ['']
    elif specific_suffix is not None:
        suffixes_to_try = [specific_suffix]
    else:
        suffixes_to_try = ['.TW', '.TWO']
    
    for suffix in suffixes_to_try:
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
        except: 
            continue
    return pd.DataFrame()

# ==========================================
# 介面與核心邏輯
# ==========================================
st.title("🏠 我的投資儀表板")
st.divider()

# 1. 載入全台股字典建立選單
stock_db_dict = load_stock_info_from_db()
all_stock_options = [f"{k} {v['name']}" for k, v in stock_db_dict.items()]

# ==========================================
# 2. 狀態管理：改用 List 儲存，方便操作與顯示
# ==========================================
if "holdings_list" not in st.session_state:
    raw_str = load_holdings()
    # 將雲端的字串拆解為乾淨的 List
    st.session_state.holdings_list = [s.strip() for s in raw_str.replace('、', ',').replace('，', ',').split(',') if s.strip()]

# 第一排：搜尋與新增
col_search, col_add = st.columns([4, 1])
with col_search:
    selected_stock = st.selectbox(
        "🔍 搜尋並新增持股 (請輸入代號或名稱)：", 
        options=[""] + all_stock_options,
        key="stock_selector"  # 👈 關鍵 1：綁定一個專屬 Key 給這個選單
    )
with col_add:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    if st.button("➕ 新增至清單", use_container_width=True):
        if selected_stock:
            if selected_stock not in st.session_state.holdings_list:
                st.session_state.holdings_list.append(selected_stock)
                # 改用 toast 浮動提示，才不會因為下面的 rerun 而閃退消失
                st.toast(f"✅ 已將 {selected_stock} 加入清單！")
            else:
                st.toast(f"⚠️ {selected_stock} 已經在清單中囉！")
            
            # 👈 關鍵 2：強制將選單狀態設為空字串，達到「清空」的效果
            st.session_state.stock_selector = "" 
            
            # 👈 關鍵 3：立刻重整畫面，讓下方的標籤列與圖表同步更新
            st.rerun()

# 第二排：持股標籤顯示 (取代原本的 text_input) 與儲存
col_list, col_date, col_save = st.columns([5, 2, 2])
with col_list:
    # 確保原本手動輸入的自訂 ETF 也能正常顯示在選項中，避免報錯
    safe_options = list(set(all_stock_options + st.session_state.holdings_list))
    
    # 使用 multiselect 作為持股清單的「容器」，美觀且能點擊 'x' 輕易刪除
    updated_list = st.multiselect(
        "🏷️ 目前持股清單 (點選 'x' 可移除)：", 
        options=safe_options, 
        default=st.session_state.holdings_list
    )
    # 同步使用者的刪除動作回 session_state
    st.session_state.holdings_list = updated_list

with col_date:
    selected_date = st.date_input("選擇日期", datetime.date.today())

with col_save:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    if st.button("💾 儲存為預設", use_container_width=True):
        # 將 List 重新組合為逗號分隔的字串，寫入 Supabase
        save_str = ", ".join(st.session_state.holdings_list)
        save_holdings(save_str)
        st.success("✅ 持股清單已成功存檔至雲端！")

if selected_date.weekday() >= 5:
    st.warning(f"⚠️ 您選擇的日期 ({selected_date}) 是週末假日，將自動顯示最近一個交易日的資料。")

# ==========================================
# 🌟 智慧防呆解析引擎 (配合 List 架構更新)
# ==========================================
my_codes = []
final_parsed_names = {} 

# 直接迴圈處理 List，省去字串切割的麻煩
for p in st.session_state.holdings_list:
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
        final_parsed_names[target_code] = name_part
    elif current_codes:
        target_code = current_codes[-1]
        final_parsed_names[target_code] = ""

# ==========================================
# 🌟 智慧防呆解析引擎 (配合 List 架構更新)
# ==========================================
my_codes = []
final_parsed_names = {} 

# 直接迴圈處理 st.session_state.holdings_list，不再需要 replace 和 split 字串了！
for p in st.session_state.holdings_list:
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
        final_parsed_names[target_code] = name_part
    elif current_codes:
        target_code = current_codes[-1]
        final_parsed_names[target_code] = ""

target_ts = pd.Timestamp(selected_date).normalize()

with st.spinner('從雲端資料庫調閱資料與精算行情中...'):
    final_rows = []
    for code in my_codes:
        # 決定名稱與 suffix，優先從 Supabase 資料庫取用
        db_info = stock_db_dict.get(code, {})
        db_name = db_info.get('name')
        db_suffix = db_info.get('suffix')
        
        name = final_parsed_names.get(code) or db_name or COMMON_ETF_MAP.get(code) or f"({code})"
        
        # 傳入 db_suffix 加速抓取
        df_k = fetch_kline_data(code, specific_suffix=db_suffix)
        
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
    
    html_table = styled_df.to_html()
    st.markdown(html_table, unsafe_allow_html=True)

    st.divider()
    selected_stock_str = st.selectbox("圖表分析：", [f"{r['代碼']} {r['商品']}" for _, r in df_final.iterrows()])
    if selected_stock_str:
        t_code = selected_stock_str.split()[0]
        t_name = selected_stock_str.split()[1]
        
        # 繪圖時也使用資料庫抓到的 suffix 加速
        db_suffix = stock_db_dict.get(t_code, {}).get('suffix')
        df_k = fetch_kline_data(t_code, specific_suffix=db_suffix)
        
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
