import streamlit as st
import requests
import pandas as pd
import datetime
import urllib3
import io
import re
from supabase import create_client, Client

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="法人買賣超排行", layout="wide")

# --- 1. 初始化 Supabase ---
@st.cache_resource
def init_supabase() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error("❌ 找不到 Supabase Secrets 設定，請檢查 Streamlit Cloud 設定。")
        st.stop()

supabase = init_supabase()

# ==========================================
# 2. 產業地圖與工具函式 (源自您的 v5 版本)
# ==========================================
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
                adj_close = result[0]['indicators']['adjclose'][0]['adjclose']
                df = pd.DataFrame({'Close': adj_close})
                df.index = pd.to_datetime(timestamps, unit='s') + pd.Timedelta(hours=8)
                df.index = df.index.normalize()
                return df.dropna()
        except: continue
    return pd.DataFrame()

# ==========================================
# 3. 官方大盤資料抓取 (源自您的 v5 版本)
# ==========================================
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
        valid_tables = [t for t in res_price.get('tables', []) if '收盤價' in t.get('fields', [])]
        if valid_tables:
            target_table = max(valid_tables, key=lambda x: len(x.get('data', [])))
            df_price = pd.DataFrame(target_table['data'], columns=target_table['fields'])
            sign_col = next((c for c in target_table['fields'] if '漲跌' in c and '價差' not in c), '漲跌(+/-)')
            df_price = df_price[['證券代號', '成交股數', '收盤價', sign_col, '漲跌價差']]
            df_price.columns = ['代號', '成交量_股', '收盤價', '漲跌符號', '漲跌價差']
            df_price['漲跌'] = df_price.apply(lambda r: float(r['漲跌價差'].replace(',','')) * (-1 if '-' in str(r['漲跌符號']) else 1), axis=1)
        else: df_price = pd.DataFrame(columns=['代號', '成交量_股', '收盤價', '漲跌'])

        url_pe = f"https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={date_str}&selectType=ALL&response=json"
        res_pe = requests.get(url_pe, headers=headers, verify=False, timeout=10).json()
        df_pe = pd.DataFrame(res_pe['data']).iloc[:, [0, 5, 6]] if res_pe.get('stat') == 'OK' else pd.DataFrame(columns=['代號','本益比','股價淨值比'])
        df_pe.columns = ['代號', '本益比', '股價淨值比']

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
        raw = res.get('aaData') or []
        if not raw: return None
        df_chips = pd.DataFrame(raw).iloc[:, [0, 1, 10, 13, 22]]
        df_chips.columns = ['代號', '名稱', '外資', '投信', '自營商']

        url_price = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json&d={date_str}"
        res_price = requests.get(url_price, headers=headers, verify=False, timeout=10).json()
        df_price = pd.DataFrame(res_price['aaData']).iloc[:, [0, 2, 3, 8]]
        df_price.columns = ['代號', '收盤價', '漲跌', '成交量_股']

        url_pe = f"https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php?l=zh-tw&o=json&d={date_str}"
        res_pe = requests.get(url_pe, headers=headers, verify=False, timeout=10).json()
        raw_pe = res_pe.get('aaData') or []
        df_pe = pd.DataFrame(raw_pe).iloc[:, [0, 2, 6]] if raw_pe else pd.DataFrame(columns=['代號','本益比','股價淨值比'])
        df_pe.columns = ['代號', '本益比', '股價淨值比']

        merged = pd.merge(df_chips, df_price, on='代號', how='left')
        return pd.merge(merged, df_pe, on='代號', how='left')
    except: return None

# ==========================================
# 4. Supabase 快取邏輯 (全新加入)
# ==========================================
def get_chips_rank_from_cache(date_str):
    try:
        res = supabase.table("chips_ranking_cache").select("*").eq("date", date_str).execute()
        if res.data:
            df = pd.DataFrame(res.data)
            df_buy = df[df['rank_type'] == 'buy'].sort_values('rank_no')
            df_sell = df[df['rank_type'] == 'sell'].sort_values('rank_no')
            # 重新映射欄位名稱以符合 UI 顯示
            mapping = {
                'rank_no': '排名', 'stock_id': '代號', 'stock_name': '名稱',
                'industry': '產業類別', 'close_price': '收盤價', 'change_val': '漲跌',
                'change_pct': '漲幅%', 'volume': '成交量', 'foreign_buy': '外資',
                'it_buy': '投信', 'dealer_buy': '自營商', 'total_buy': '法人買超',
                'pe_ratio': '本益比', 'pb_ratio': '股價淨值比', 'nav': '每股淨值'
            }
            return df_buy.rename(columns=mapping), df_sell.rename(columns=mapping).rename(columns={'法人買超': '法人賣超'})
    except: pass
    return None, None

def save_chips_rank_to_cache(date_str, df_buy, df_sell):
    data_to_insert = []
    def prepare_data(df, r_type):
        for _, row in df.iterrows():
            data_to_insert.append({
                "date": date_str, "rank_type": r_type, "rank_no": int(row['排名']),
                "stock_id": str(row['代號']), "stock_name": str(row['名稱']),
                "industry": str(row['產業類別']), "close_price": float(row['收盤價']),
                "change_val": float(row['漲跌']), "change_pct": float(row['漲幅%']),
                "volume": int(row['成交量']), "foreign_buy": int(row['外資']),
                "it_buy": int(row['投信']), "dealer_buy": int(row['自營商']),
                "total_buy": int(row.get('法人買超', row.get('法人賣超', 0))),
                "pe_ratio": float(row['本益比']), "pb_ratio": float(row['股價淨值比']), "nav": float(row['每股淨值'])
            })
    prepare_data(df_buy, 'buy')
    prepare_data(df_sell, 'sell')
    if data_to_insert:
        try: supabase.table("chips_ranking_cache").insert(data_to_insert).execute()
        except: pass

# ==========================================
# 5. 網頁主介面
# ==========================================
st.title("📊 法人買賣超排行 (Supabase 快取版)")
st.markdown("追蹤三大法人動向，並自動校準前 200 檔熱門股之真實股價。")

col1, col2 = st.columns([1, 3])
with col1:
    target_date = st.date_input("選擇查詢日期", datetime.date.today())
    date_str = target_date.strftime('%Y-%m-%d')
    run_btn = st.button("🚀 開始抓取與精算", width='stretch')

if run_btn:
    df_buy, df_sell = get_chips_rank_from_cache(date_str)
    
    if df_buy is not None:
        st.success(f"✅ 已從 Supabase 載入 {date_str} 快取數據")
    else:
        with st.spinner("啟動全市場爬蟲與 Yahoo 精算..."):
            industry_map = get_industry_map()
            df_twse = fetch_twse_data(target_date)
            df_tpex = fetch_tpex_data(target_date)

            if df_twse is None and df_tpex is None:
                st.error("查無資料。")
                st.stop()
            
            df_all = pd.concat([d for d in [df_twse, df_tpex] if d is not None], ignore_index=True)
            for col in ['外資', '投信', '自營商', '成交量_股']:
                df_all[col] = pd.to_numeric(df_all[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
            
            df_all['外資'] //= 1000; df_all['投信'] //= 1000; df_all['自營商'] //= 1000
            df_all['成交量'] = df_all['成交量_股'] // 1000
            df_all['法人買賣超'] = df_all['外資'] + df_all['投信'] + df_all['自營商']
            for col in ['收盤價', '漲跌', '本益比', '股價淨值比']: df_all[col] = df_all[col].apply(convert_to_float)

            df_stock = df_all[~df_all['代號'].str.startswith('00') & (df_all['代號'].str.len() < 6)].copy()
            df_stock['產業類別'] = df_stock['代號'].map(industry_map).fillna('其他')

            df_buy_raw = df_stock.sort_values(by='法人買賣超', ascending=False).head(100).copy()
            df_sell_raw = df_stock.sort_values(by='法人買賣超', ascending=True).head(100).copy()
            
            unique_tickers = list(set(df_buy_raw['代號'].tolist() + df_sell_raw['代號'].tolist()))
            calibrated_data = {}
            target_ts = pd.Timestamp(target_date).normalize()
            
            # Yahoo 精算邏輯
            for code in unique_tickers:
                df_k = fetch_kline_data(code)
                if not df_k.empty and target_ts in df_k.index:
                    k_today = df_k.loc[target_ts]
                    past_data = df_k[df_k.index < target_ts]
                    if not past_data.empty:
                        yest_close = past_data.iloc[-1]['Close']
                        price = float(k_today['Close'])
                        calibrated_data[code] = {'精準收盤價': round(price, 2), '精準漲跌': round(price - yest_close, 2), '漲幅%': round(((price - yest_close) / yest_close) * 100, 2)}

            def apply_calibration(df_target):
                df_target['漲幅%'] = 0.0
                for idx, row in df_target.iterrows():
                    code = row['代號']
                    if code in calibrated_data:
                        df_target.at[idx, '收盤價'] = calibrated_data[code]['精準收盤價']
                        df_target.at[idx, '漲跌'] = calibrated_data[code]['精準漲跌']
                        df_target.at[idx, '漲幅%'] = calibrated_data[code]['漲幅%']
                df_target['每股淨值'] = df_target.apply(lambda r: round(r['收盤價'] / r['股價淨值比'], 2) if r['股價淨值比'] > 0 else 0, axis=1)
                df_target = df_target.reset_index(drop=True)
                df_target['排名'] = df_target.index + 1
                return df_target

            output_cols = ['排名', '代號', '名稱', '產業類別', '收盤價', '漲跌', '漲幅%', '成交量', '外資', '投信', '自營商', '法人買賣超', '本益比', '股價淨值比', '每股淨值']
            df_buy = apply_calibration(df_buy_raw)[output_cols].rename(columns={'法人買賣超': '法人買超'})
            df_sell = apply_calibration(df_sell_raw)[output_cols].rename(columns={'法人買賣超': '法人賣超'})
            
            save_chips_rank_to_cache(date_str, df_buy, df_sell)
            st.toast("🔥 精算完畢並已同步至雲端快取")

    # 顯示結果
    st.divider()
    tab1, tab2 = st.tabs(["🚀 法人買超 Top 100", "🔻 法人賣超 Top 100"])
    with tab1: st.dataframe(df_buy, width='stretch', hide_index=True)
    with tab2: st.dataframe(df_sell, width='stretch', hide_index=True)
