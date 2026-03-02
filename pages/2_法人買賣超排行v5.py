import streamlit as st
import requests
import pandas as pd
import datetime
import urllib3
import io

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="法人買賣超排行", layout="wide")

INDUSTRY_CODE_MAP = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "07": "化學生技醫療", "08": "玻璃陶瓷",
    "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業",
    "13": "電子工業", "14": "建材營造", "15": "航運業", "16": "觀光餐旅",
    "17": "金融保險", "18": "貿易百貨", "19": "綜合", "20": "其他",
    "21": "化學工業", "22": "生技醫療", "23": "油電燃氣", "24": "半導體業",
    "25": "電腦及週邊", "26": "光電業", "27": "通信網路", "28": "電子零組件",
    "29": "電子通路", "30": "資訊服務", "31": "其他電子", "32": "文化創意",
    "33": "農業科技", "34": "電子商務", "35": "綠能環保", "36": "數位雲端",
    "37": "運動休閒", "38": "居家生活", "80": "管理股票", "91": "存託憑證",
}

def convert_to_float(val):
    try:
        val_str = str(val).strip()
        if val_str in ['-', '', 'nan', 'None']: return 0.0
        return float(val_str.replace(',', ''))
    except: return 0.0

@st.cache_data(ttl=86400)
def get_industry_map():
    industry_map = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", headers=headers, verify=False, timeout=5)
        for row in res.json(): industry_map[row.get('公司代號', '').strip()] = INDUSTRY_CODE_MAP.get(row.get('產業別', ''), '其他')
        res = requests.get("https://www.tpex.org.tw/openapi/v1/t187ap03_O", headers=headers, verify=False, timeout=5)
        for row in res.json(): industry_map[row.get('公司代號', '').strip()] = INDUSTRY_CODE_MAP.get(row.get('產業別', ''), '其他')
    except: pass
    return industry_map

@st.cache_data(ttl=3600)
def fetch_twse_data(date_obj):
    date_str = date_obj.strftime('%Y%m%d')
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        url_chips = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL&response=json"
        res = requests.get(url_chips, headers=headers, verify=False, timeout=10).json()
        if res.get('stat') != 'OK': return None
        df_chips = pd.DataFrame(res['data'], columns=res['fields']).iloc[:, [0, 1, 4, 10, 11]]
        df_chips.columns = ['代號', '名稱', '外資', '投信', '自營商']

        url_price = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALL&response=json"
        res_price = requests.get(url_price, headers=headers, verify=False, timeout=10).json()
        target_table = next((t for t in res_price.get('tables', []) if '收盤價' in t['fields']), None)
        df_price = pd.DataFrame(target_table['data'], columns=target_table['fields'])[['證券代號', '成交股數', '收盤價', '漲跌(+/-)', '漲跌價差']]
        df_price.columns = ['代號', '成交量_股', '收盤價', '漲跌符號', '漲跌價差']

        url_pe = f"https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={date_str}&selectType=ALL&response=json"
        res_pe = requests.get(url_pe, headers=headers, verify=False, timeout=10).json()
        if res_pe.get('stat') == 'OK':
            df_pe = pd.DataFrame(res_pe['data']).iloc[:, [0, 5, 6]]
            df_pe.columns = ['代號', '本益比', '股價淨值比']
        else: df_pe = pd.DataFrame(columns=['代號', '本益比', '股價淨值比'])

        def calc_change(row):
            sign, val = str(row['漲跌符號']).lower(), str(row['漲跌價差'])
            try:
                v = float(val.replace(',', ''))
                return v * -1 if 'green' in sign or '-' in sign else v
            except: return 0.0
        
        df_price['漲跌'] = df_price.apply(calc_change, axis=1)
        merged = pd.merge(df_chips, df_price[['代號', '收盤價', '漲跌', '成交量_股']], on='代號', how='left')
        return pd.merge(merged, df_pe, on='代號', how='left')
    except: return None

@st.cache_data(ttl=3600)
def fetch_tpex_data(date_obj):
    roc_year = date_obj.year - 1911
    date_str = f"{roc_year}/{date_obj.strftime('%m/%d')}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        url_chips = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=AL&t=D&d={date_str}"
        res = requests.get(url_chips, headers=headers, verify=False, timeout=10).json()
        raw = res.get('aaData') or (res.get('tables')[0]['data'] if res.get('tables') else [])
        if not raw: return None
        df_chips = pd.DataFrame(raw).iloc[:, [0, 1, 10, 13, 22]]
        df_chips.columns = ['代號', '名稱', '外資', '投信', '自營商']

        url_price = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json&d={date_str}"
        res_price = requests.get(url_price, headers=headers, verify=False, timeout=10).json()
        df_price = pd.DataFrame(res_price['aaData']).iloc[:, [0, 2, 3, 8]]
        df_price.columns = ['代號', '收盤價', '漲跌', '成交量_股']

        url_pe = f"https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php?l=zh-tw&o=json&d={date_str}"
        res_pe = requests.get(url_pe, headers=headers, verify=False, timeout=10).json()
        raw_pe = res_pe.get('tables', [{}])[0].get('data', []) or res_pe.get('aaData', [])
        if raw_pe:
            df_pe = pd.DataFrame(raw_pe).iloc[:, [0, 2, 6]]
            df_pe.columns = ['代號', '本益比', '股價淨值比']
        else: df_pe = pd.DataFrame(columns=['代號', '本益比', '股價淨值比'])

        merged = pd.merge(df_chips, df_price, on='代號', how='left')
        return pd.merge(merged, df_pe, on='代號', how='left')
    except: return None

# ==========================================
# 網頁主介面
# ==========================================
st.title("📊 法人買賣超排行 (含每股淨值)")
st.markdown("追蹤外資、投信、自營商動向，並自動計算每股淨值。")

# --- 將選項移至主畫面 ---
col1, col2 = st.columns([1, 3])
with col1:
    target_date = st.date_input("選擇查詢日期", datetime.date.today())
    run_btn = st.button("🚀 開始抓取與分析", use_container_width=True)

if run_btn:
    with st.spinner("正在下載全市場資料與產業地圖..."):
        industry_map = get_industry_map()
        df_twse = fetch_twse_data(target_date)
        df_tpex = fetch_tpex_data(target_date)

    if df_twse is None and df_tpex is None:
        st.error(f"⚠️ {target_date} 查無資料，可能為假日或盤後資料尚未更新。")
    else:
        df_all = pd.concat([d for d in [df_twse, df_tpex] if d is not None], ignore_index=True)
        
        # 數值轉換與處理
        for col in ['外資', '投信', '自營商', '成交量_股']:
            df_all[col] = pd.to_numeric(df_all[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
        
        df_all['外資'] //= 1000
        df_all['投信'] //= 1000
        df_all['自營商'] //= 1000
        df_all['成交量'] = df_all['成交量_股'] // 1000
        df_all['法人買賣超'] = df_all['外資'] + df_all['投信'] + df_all['自營商']
        
        for col in ['收盤價', '漲跌', '本益比', '股價淨值比']:
            df_all[col] = df_all[col].apply(convert_to_float)

        # 計算每股淨值
        df_all['每股淨值'] = df_all.apply(lambda r: round(r['收盤價'] / r['股價淨值比'], 2) if r['股價淨值比'] > 0 else 0, axis=1)

        # 篩選與整理
        df_all['代號'] = df_all['代號'].astype(str).str.strip()
        df_stock = df_all[~df_all['代號'].str.startswith('00') & (df_all['代號'].str.len() < 6)].copy()
        df_stock['產業類別'] = df_stock['代號'].map(industry_map).fillna('其他')

        output_cols = ['排名', '代號', '名稱', '產業類別', '收盤價', '漲跌', '成交量', '外資', '投信', '自營商', '法人買賣超', '本益比', '股價淨值比', '每股淨值']

        def make_rank_df(df, asc):
            res = df.sort_values(by='法人買賣超', ascending=asc).head(100).copy().reset_index(drop=True)
            res['排名'] = res.index + 1
            return res[output_cols]

        df_buy = make_rank_df(df_stock, False).rename(columns={'法人買賣超': '法人買超'})
        df_sell = make_rank_df(df_stock, True).rename(columns={'法人買賣超': '法人賣超'})

        st.divider()
        tab1, tab2 = st.tabs(["🚀 法人買超 Top 100", "🔻 法人賣超 Top 100"])
        with tab1: st.dataframe(df_buy, use_container_width=True, hide_index=True)
        with tab2: st.dataframe(df_sell, use_container_width=True, hide_index=True)

        # --- 下載按鈕也移回主畫面底部 ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_buy.to_excel(writer, sheet_name='法人買超Top100', index=False)
            df_sell.to_excel(writer, sheet_name='法人賣超Top100', index=False)
        output.seek(0)
        
        st.success("✅ 分析完成！")
        st.download_button("📥 下載 Excel 報表", data=output, file_name=f"{target_date}_法人買賣超排行.xlsx", type="primary")