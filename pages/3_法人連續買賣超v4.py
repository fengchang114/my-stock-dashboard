import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import urllib3
import io
import time
import datetime as dt
from supabase import create_client, Client

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="法人連續買賣超", layout="wide")

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

# --- 2. 產業代碼與工具函式 ---
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

# --- 3. Supabase 單日籌碼快取邏輯 ---
def get_daily_chips_from_cache(date_str):
    try:
        res = supabase.table("daily_chips_cache").select("stock_id, stock_name, foreign_buy, it_buy").eq("date", date_str).execute()
        if res.data and len(res.data) > 500: # 確保資料量完整
            df = pd.DataFrame(res.data)
            df.columns = ['代號', '名稱', '外資', '投信']
            return df
    except: pass
    return None

def save_daily_chips_to_cache(date_str, df_daily):
    data_to_insert = []
    for _, row in df_daily.iterrows():
        data_to_insert.append({
            "date": date_str, "stock_id": str(row['代號']), "stock_name": str(row['名稱']),
            "foreign_buy": int(row['外資']), "it_buy": int(row['投信'])
        })
    # 分批寫入 (每 500 筆一次)
    if data_to_insert:
        for i in range(0, len(data_to_insert), 500):
            try:
                supabase.table("daily_chips_cache").insert(data_to_insert[i:i+500]).execute()
            except: pass

# --- 4. 爬蟲抓取單日籌碼 ---
def fetch_one_day_chips(target_date):
    date_str_db = target_date.strftime('%Y-%m-%d')
    date_str_twse = target_date.strftime('%Y%m%d')
    roc_year = target_date.year - 1911
    date_str_tpex = f"{roc_year}/{target_date.strftime('%m/%d')}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # 優先查快取
    cached = get_daily_chips_from_cache(date_str_db)
    if cached is not None:
        return cached

    # 沒快取才爬
    try:
        # TWSE
        url_l = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str_twse}&selectType=ALL&response=json"
        res_l = requests.get(url_l, headers=headers, verify=False, timeout=10).json()
        df_l = pd.DataFrame(res_l['data'], columns=res_l['fields']).iloc[:, [0, 1, 4, 10]] if res_l.get('stat') == 'OK' else pd.DataFrame()
        
        # TPEX
        url_o = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=AL&t=D&d={date_str_tpex}"
        res_o = requests.get(url_o, headers=headers, verify=False, timeout=10).json()
        raw_o = res_o.get('aaData') or []
        df_o = pd.DataFrame(raw_o).iloc[:, [0, 1, 10, 13]] if raw_o else pd.DataFrame()
        
        if df_l.empty and df_o.empty: return None
        
        df_combined = pd.concat([df_l, df_o], ignore_index=True)
        df_combined.columns = ['代號', '名稱', '外資', '投信']
        for col in ['外資', '投信']:
            df_combined[col] = pd.to_numeric(df_combined[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int) // 1000
        
        # 存入快取
        save_daily_chips_to_cache(date_str_db, df_combined)
        return df_combined
    except: return None

# ==========================================
# 5. 主程式介面
# ==========================================
st.title("🔥 法人連續買賣超 (Supabase 快取版)")
st.markdown("自動比對多日籌碼，找出土洋同步連買/連賣的強勢與弱勢股。")
st.divider()

with st.sidebar:
    lookback = st.slider("分析最近交易日天數", 3, 10, 5)
    min_vol = st.number_input("最低成交量 (張)", value=500)
    start_analysis = st.button("🚀 開始分析", width='stretch')

if start_analysis:
    industry_map = get_industry_map()
    all_days_chips = []
    check_date = dt.date.today()
    found_days = 0
    
    # 蒐集多日資料 (優先從 DB 撈)
    with st.spinner(f"正在蒐集最近 {lookback} 個交易日的資料..."):
        while found_days < lookback:
            if check_date.weekday() < 5:
                df_day = fetch_one_day_chips(check_date)
                if df_day is not None:
                    all_days_chips.append(df_day)
                    found_days += 1
                elif found_days == 0 and dt.datetime.now().hour < 18:
                    pass # 若當日尚未收盤公告則跳過
            check_date -= dt.timedelta(days=1)
            if (dt.date.today() - check_date).days > 20: break # 安全閥

    if len(all_days_chips) < lookback:
        st.error(f"資料蒐集不足 (僅找到 {len(all_days_chips)} 天)，請確認日期或稍後再試。")
        st.stop()

    # --- 核心運算：計算連續天數 ---
    base_df = all_days_chips[0].copy()
    all_codes = base_df['代號'].unique()
    streak_results = []

    for code in all_codes:
        f_streak, t_streak = 0, 0
        f_stop, t_stop = False, False
        
        for i in range(lookback):
            df_i = all_days_chips[i]
            row = df_i[df_i['代號'] == code]
            if row.empty: break
            f_val, t_val = row.iloc[0]['外資'], row.iloc[0]['投信']
            
            # 外資連買/連賣邏輯
            if not f_stop:
                if i == 0: f_mode = 1 if f_val > 0 else (-1 if f_val < 0 else 0)
                if f_mode == 1 and f_val > 0: f_streak += 1
                elif f_mode == -1 and f_val < 0: f_streak -= 1
                else: f_stop = True
            
            # 投信連買/連賣邏輯
            if not t_stop:
                if i == 0: t_mode = 1 if t_val > 0 else (-1 if t_val < 0 else 0)
                if t_mode == 1 and t_val > 0: t_streak += 1
                elif t_mode == -1 and t_val < 0: t_streak -= 1
                else: t_stop = True
        
        if f_streak != 0 or t_streak != 0:
            streak_results.append({'代號': code, '外資連買': f_streak, '投信連買': t_streak})

    # --- 技術指標運算 (yfinance) ---
    final_list = []
    streak_df = pd.DataFrame(streak_results)
    merged_main = pd.merge(base_df, streak_df, on='代號')
    
    # 過濾：只留下有連續買或賣的股票
    filtered_main = merged_main[(merged_main['外資連買'].abs() >= 2) | (merged_main['投信連買'].abs() >= 2)]
    
    with st.spinner(f"正在分析 {len(filtered_main)} 檔標的之技術面..."):
        chunk_size = 40
        for j in range(0, len(filtered_main), chunk_size):
            chunk_df = filtered_main.iloc[j:j+chunk_size]
            tickers = [f"{c}.TW" if len(c)==4 else f"{c}.TWO" for c in chunk_df['代號']] # 簡化判斷
            try:
                yf_data = yf.download(tickers, period="1mo", group_by='ticker', progress=False, threads=True)
                for _, row in chunk_df.iterrows():
                    code = row['代號']
                    t_yf = f"{code}.TW" if f"{code}.TW" in yf_data else f"{code}.TWO"
                    try:
                        df_h = yf_data[t_yf].dropna()
                        if len(df_h) < 2: continue
                        last_c = df_h['Close'].iloc[-1]
                        prev_c = df_h['Close'].iloc[-2]
                        vol = df_h['Volume'].iloc[-1] // 1000
                        if vol < min_vol: continue
                        
                        change_p = ((last_c - prev_c) / prev_c) * 100
                        
                        # 判斷說明文字
                        desc = ""
                        f_s, t_s = row['外資連買'], row['投信連買']
                        if f_s > 0 and t_s > 0: desc = f"土洋同買 (外{f_s}/投{t_s})"
                        elif f_s < 0 and t_s < 0: desc = f"土洋同賣 (外{abs(f_s)}/投{abs(t_s)})"
                        elif f_s > 0: desc = f"外資連買 {f_s} 天"
                        elif t_s > 0: desc = f"投信連買 {t_s} 天"
                        
                        final_list.append({
                            "代號": code, "名稱": row['名稱'], "產業別": industry_map.get(code, "其他"),
                            "收盤": round(last_c, 2), "漲幅%": round(change_p, 2), "成交量": vol,
                            "外資": row['外資'], "投信": row['投信'],
                            "連續天數": max(abs(f_s), abs(t_s)), "詳細說明": desc
                        })
                    except: continue
            except: pass

    # --- 渲染結果 ---
    if final_list:
        df_res = pd.DataFrame(final_list)
        tab_buy, tab_sell = st.tabs(["🚀 法人連買排行", "🔻 法人連賣排行"])
        
        with tab_buy:
            df_b = df_res[df_res['詳細說明'].str.contains('買')].sort_values('連續天數', ascending=False)
            st.dataframe(df_b, width='stretch', hide_index=True)
        with tab_sell:
            df_s = df_res[df_res['詳細說明'].str.contains('賣')].sort_values('連續天數', ascending=False)
            st.dataframe(df_s, width='stretch', hide_index=True)
    else:
        st.warning("查無符合條件之標的。")
