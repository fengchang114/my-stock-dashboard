import streamlit as st
import requests
import pandas as pd
import datetime
import urllib3
import io

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="盤後強弱勢股篩選", layout="wide")

def convert_to_int(val):
    try:
        if isinstance(val, (int, float)): return int(val)
        return int(str(val).replace(',', ''))
    except: return 0

def convert_to_float(val):
    try:
        val_str = str(val).strip()
        if val_str in ['-', '', 'nan', 'None', '---']: return 0.0
        return float(val_str.replace(',', ''))
    except: return 0.0

@st.cache_data(ttl=3600)
def fetch_twse_data(date_str):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        url_price = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALL&response=json"
        res = requests.get(url_price, headers=headers, verify=False, timeout=10)
        data = res.json()
        if data.get('stat') != 'OK': return None
            
        target_table = next((t for t in data.get('tables', []) if '收盤價' in t['fields']), None)
        df = pd.DataFrame(target_table['data'], columns=target_table['fields'])
        df = df[['證券代號', '證券名稱', '收盤價', '漲跌(+/-)', '漲跌價差', '成交股數']]
        df.columns = ['代碼', '商品', '成交', '漲跌符號', '漲跌價差', '成交量_股']
        
        def calc_change(row):
            sign = str(row['漲跌符號']).lower()
            val = str(row['漲跌價差'])
            try:
                v = float(val.replace(',', ''))
                if 'green' in sign or '-' in sign: return v * -1
                return v
            except: return 0.0
            
        df['漲跌'] = df.apply(calc_change, axis=1)
        return df[['代碼', '商品', '成交', '漲跌', '成交量_股']]
    except: return None

@st.cache_data(ttl=3600)
def fetch_tpex_data(date_obj):
    roc_year = date_obj.year - 1911
    date_str = f"{roc_year}/{date_obj.strftime('%m/%d')}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        url_price = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json&d={date_str}"
        res = requests.get(url_price, headers=headers, verify=False, timeout=10)
        data = res.json()
        if 'aaData' not in data or not data['aaData']: return None
            
        df = pd.DataFrame(data['aaData'])
        df = df.iloc[:, [0, 1, 2, 3, 8]]
        df.columns = ['代碼', '商品', '成交', '漲跌', '成交量_股']
        return df
    except: return None

st.title("📈 盤後強弱勢股篩選器")
st.markdown("抓取上市櫃全市場資料，篩選條件：**排除ETF與權證、量大於千張、排除-KY、取前100大**")

col1, col2 = st.columns([1, 3])
with col1:
    selected_date = st.date_input("請選擇查詢日期", datetime.date.today())
    run_button = st.button("🚀 開始抓取與篩選", use_container_width=True)

if run_button:
    query_date_str = selected_date.strftime('%Y%m%d')
    
    with st.spinner(f'正在向證交所與櫃買中心獲取 {selected_date} 的資料...'):
        df_twse = fetch_twse_data(query_date_str)
        df_tpex = fetch_tpex_data(selected_date)

    if df_twse is None and df_tpex is None:
        st.error(f"⚠️ {selected_date} 查無資料，可能為假日或盤後資料尚未更新。")
    else:
        df_all = pd.concat([d for d in [df_twse, df_tpex] if d is not None], ignore_index=True)
        df_all['商品'] = df_all['商品'].str.strip()
        df_all['代碼'] = df_all['代碼'].str.strip()
        df_all['成交量_股'] = df_all['成交量_股'].apply(convert_to_int)
        df_all['成交量_張'] = df_all['成交量_股'] // 1000
        df_all['成交'] = df_all['成交'].apply(convert_to_float)
        df_all['漲跌'] = df_all['漲跌'].apply(convert_to_float)

        def calc_pct(row):
            close = row['成交']
            change = row['漲跌']
            prev_close = close - change
            if prev_close > 0: return round((change / prev_close) * 100, 2)
            return 0.0
        df_all['漲幅%'] = df_all.apply(calc_pct, axis=1)

        # 條件篩選 (大盤強弱勢)
        cond_not_etf = ~df_all['代碼'].str.startswith('00')
        cond_not_warrant = df_all['代碼'].str.len() < 6
        df_filtered = df_all[cond_not_etf & cond_not_warrant].copy()
        df_filtered = df_filtered[df_filtered['成交量_張'] >= 1000]
        df_filtered = df_filtered[~df_filtered['商品'].str.contains('KY', na=False)]

        target_cols = ['商品', '代碼', '成交', '漲幅%']
        df_strong = df_filtered.sort_values(by='漲幅%', ascending=False).head(100)[target_cols]
        df_weak = df_filtered.sort_values(by='漲幅%', ascending=True).head(100)[target_cols]

        st.divider()
        
        col_s, col_w = st.columns(2)
        with col_s:
            st.subheader("🔥 強勢股前 100 名")
            st.dataframe(df_strong, height=500, hide_index=True, use_container_width=True)
        with col_w:
            st.subheader("🧊 弱勢股前 100 名")
            st.dataframe(df_weak, height=500, hide_index=True, use_container_width=True)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_strong.to_excel(writer, sheet_name='強勢股前100', index=False)
            df_weak.to_excel(writer, sheet_name='弱勢股前100', index=False)
        output.seek(0)
        
        st.success("✅ 資料運算完成！")
        st.download_button(
            label="📥 下載 Excel 報表",
            data=output,
            file_name=f"強弱勢股篩選_{query_date_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )