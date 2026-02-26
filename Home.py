import streamlit as st
import pandas as pd
import requests
import datetime
import urllib3
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(page_title="我的投資儀表板", layout="wide", page_icon="🏠")

# ==========================================
# 工具與抓取函式 (獨立在首頁運作)
# ==========================================
def convert_to_int(val):
    try:
        if isinstance(val, (int, float)): return int(val)
        return int(str(val).replace(',', ''))
    except: return 0

def convert_to_float(val):
    try:
        val_str = str(val).strip()
        # 排除無法轉換的字眼
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
            target_table = next((t for t in res.get('tables', []) if '收盤價' in t['fields']), None)
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

    # 🌟 上櫃 (強化版：同時防呆 aaData 與 tables 兩種格式)
    try:
        url_tpex = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json&d={roc_date_str}"
        res = requests.get(url_tpex, headers=headers, verify=False, timeout=10).json()
        
        raw_data = res.get('aaData', []) or (res.get('tables', [{}])[0].get('data', []) if res.get('tables') else [])
        if raw_data:
            df = pd.DataFrame(raw_data).iloc[:, [0, 1, 2, 3, 8]]
            df.columns = ['代碼', '商品', '成交', '漲跌', '成交量_股']
            df_list.append(df)
    except: pass
    
    if df_list:
        return pd.concat(df_list, ignore_index=True)
    return None

# ==========================================
# 首頁介面設計
# ==========================================
st.title("🏠 我的專屬投資儀表板")
st.markdown("歡迎回來！請在下方管理您的持股，或透過左側選單使用進階盤後分析工具。")
st.divider()

col1, col2 = st.columns([2, 1])
with col1:
    # 讓使用者可以自由編輯持股
    user_stocks_input = st.text_input(
        "📝 編輯我的持股代碼 (可用代碼或加上名稱，例如：1815 富喬, 2317)：", 
        value="2317, 2344, 3297, 1815, 8112, 0050"
    )
with col2:
    selected_date = st.date_input("選擇看盤日期", datetime.date.today())
    run_button = st.button("🔄 更新持股報價", use_container_width=True)

if run_button or user_stocks_input:
    # 🌟 強化輸入處理：自動抓出字串中的「數字代碼」與「中文字名稱」
    # 1. 抓取所有 4~6 碼的連續數字作為代碼
    my_codes = re.findall(r'\d{4,6}', user_stocks_input)
    
    # 2. 抓取可能的中文名稱 (移除數字與雜訊)
    cleaned_names = re.sub(r'[A-Za-z0-9,\s]', ' ', user_stocks_input).split()
    my_names = [n for n in cleaned_names if len(n) > 0]
    
    query_date_str = selected_date.strftime('%Y%m%d')
    roc_year = selected_date.year - 1911
    roc_date_str = f"{roc_year}/{selected_date.strftime('%m/%d')}"

    with st.spinner('正在獲取最新報價...'):
        df_all = fetch_market_data(query_date_str, roc_date_str)

    if df_all is None:
        st.error(f"⚠️ {selected_date} 查無資料，可能為假日或盤後資料尚未更新。")
    else:
        # 資料清洗
        df_all['商品'] = df_all['商品'].str.strip()
        df_all['代碼'] = df_all['代碼'].str.strip()
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

        # 🌟 雙重包抄篩選：只要「代碼」符合，或是「商品名稱」包含輸入的字眼，就通通抓出來
        cond_code = df_all['代碼'].isin(my_codes)
        cond_name = df_all['商品'].apply(lambda x: any(n in x for n in my_names) if my_names else False)
        
        df_my_stocks = df_all[cond_code | cond_name].copy()
        
        st.subheader("💡 今日持股表現")
        if not df_my_stocks.empty:
            df_my_stocks = df_my_stocks[['代碼', '商品', '成交', '漲跌', '漲幅%', '成交量_張']].sort_values(by='漲幅%', ascending=False)
            st.dataframe(
                df_my_stocks, 
                hide_index=True, 
                use_container_width=True,
                column_config={
                    "漲幅%": st.column_config.NumberColumn(format="%.2f %%"),
                    "成交量_張": st.column_config.NumberColumn(format="%d 張")
                }
            )
        else:
            st.info("今日無您的持股資料，或輸入的代碼有誤。")