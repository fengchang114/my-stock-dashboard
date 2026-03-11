import streamlit as st
import pandas as pd
import yfinance as yf
import twstock
import requests
import datetime
import urllib3
import re
import time
from io import StringIO
from supabase import create_client, Client

# 基礎設定
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="異常注意警示股", layout="wide", page_icon="🚨")

# --- 1. 初始化 Supabase ---
@st.cache_resource
def init_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = init_supabase()

# --- 2. 智慧解析與快取邏輯 ---
def smart_extract_codes_to_set(data_list):
    code_set = set()
    if not data_list: return code_set
    for row in data_list:
        for item in row:
            val = str(item).strip()
            if re.match(r'^\d{4}$', val):
                code_set.add(val)
                break
    return code_set

def get_market_data_from_cache(date_str):
    try:
        res = supabase.table("warning_stocks_cache").select("*").eq("date", date_str).execute()
        if res.data:
            notice_set = {row['stock_id'] for row in res.data if row['status'] == '注意股'}
            punish_db = {row['stock_id']: {"期間": row['period'], "分盤": row['match_time']} 
                         for row in res.data if row['status'] == '處置股'}
            return notice_set, punish_db
    except: pass
    return None, None

def save_market_data_to_cache(date_str, notice_set, punish_db):
    data_to_insert = []
    for code, info in punish_db.items():
        data_to_insert.append({"date": date_str, "stock_id": code, "status": "處置股", "period": info['期間'], "match_time": info['分盤']})
    for code in notice_set:
        if code not in punish_db:
            data_to_insert.append({"date": date_str, "stock_id": code, "status": "注意股", "period": "", "match_time": "-"})
    if data_to_insert:
        try: supabase.table("warning_stocks_cache").insert(data_to_insert).execute()
        except: pass

# --- 3. 官方數據抓取 (台玻 20 分盤精準判定修正) ---
def get_official_market_data(target_date):
    date_str = target_date.strftime('%Y-%m-%d')
    today_str = target_date.strftime('%Y%m%d')
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    cached_notice, cached_punish = get_market_data_from_cache(date_str)
    if cached_notice is not None:
        st.toast("✅ 從雲端快取載入")
        return cached_notice, cached_punish

    notice_set, punish_db = set(), {}
    try:
        # 爬注意股
        url_n = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={today_str}&endDate={today_str}&response=json"
        res_n = requests.get(url_n, timeout=10, headers=headers, verify=False).json()
        if res_n.get('stat') == 'OK': notice_set = smart_extract_codes_to_set(res_n['data'])

        # 爬處置股
        url_p = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={today_str}&endDate={today_str}&response=json"
        res_p = requests.get(url_p, timeout=10, headers=headers, verify=False).json()
        if res_p.get('stat') == 'OK' and res_p.get('data'):
            for row in res_p['data']:
                row_str = "".join(str(item) for item in row)
                code_match = re.search(r'(\d{4})', row_str)
                if code_match:
                    code = code_match.group(1)
                    # 🌟 修正：精準判定分鐘數
                    if "20分" in row_str: m_time = "20分"
                    elif "45分" in row_str: m_time = "45分"
                    else: m_time = "5分" # 預設多為 5 分
                    
                    # 抓取期間 (通常包含 ~ 或 ～)
                    period = ""
                    for item in row:
                        if "~" in str(item) or "～" in str(item):
                            period = str(item)
                    punish_db[code] = {"期間": period, "分盤": m_time}
        
        save_market_data_to_cache(date_str, notice_set, punish_db)
    except: pass
    return notice_set, punish_db

# ==========================================
# 4. 主介面與核心邏輯
# ==========================================
st.title("🚨 異常注意警示股雷達")
target_date = st.date_input("📅 選擇查詢日期", datetime.date.today())
start_btn = st.button("🚀 開始連線查核", width='stretch', type="primary")

my_stocks = ['6548', '3297', '1815', '8112', '0050', '2492']

if start_btn:
    with st.spinner("正在執行全市場掃描與數據分析..."):
        notice_set, punish_db = get_official_market_data(target_date)
        
        # 獲取全市場代碼 (twstock)
        twstock.__update_codes()
        tickers = [f"{c}.TW" if info.market == '上市' else f"{c}.TWO" 
                   for c, info in twstock.codes.items() if info.type == '股票']
        
        all_results = []
        chunk_size = 30 # 🌟 調小批次量避免 YF 頻率限制
        yf_start = target_date - datetime.timedelta(days=45)
        yf_end = target_date + datetime.timedelta(days=1)

        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i+chunk_size]
            try:
                data = yf.download(chunk, start=yf_start, end=yf_end, group_by='ticker', progress=False, threads=True)
                for t in chunk:
                    try:
                        df = data[t].dropna()
                        if len(df) < 7: continue
                        code = t.split('.')[0]
                        last_c = df.iloc[-1]['Close']
                        prev_c = df.iloc[-2]['Close']
                        six_day_c = df.iloc[-7]['Close']
                        
                        status, m_time, p_period = "一般", "-", ""
                        if code in punish_db: status, m_time, p_period = "🚫處置股", punish_db[code]["分盤"], punish_db[code]["期間"]
                        elif code in notice_set: status = "📢注意股"
                        
                        six_pct = ((last_c - six_day_c) / six_day_c) * 100
                        warning = "🚨達注意標準" if status == "一般" and six_pct >= 25 else ("⚠️即將注意" if status == "一般" and six_pct >= 22 else "正常")
                        
                        if status != "一般" or warning != "正常":
                            all_results.append({
                                "代碼": code, "名稱": twstock.codes[code].name, "狀態": status, "分盤": m_time,
                                "預警": warning, "收盤": round(last_c, 2), "單日漲幅%": round(((last_c-prev_c)/prev_c)*100, 2),
                                "6日累計漲幅%": round(six_pct, 2), "處置期間": p_period
                            })
                    except: continue
                time.sleep(0.5) # 🌟 緩衝避免封鎖
            except: continue

        if all_results:
            df_final = pd.DataFrame(all_results)
            df_final['w'] = df_final['狀態'].map({'🚫處置股': 3, '📢注意股': 2, '一般': 1})
            df_final = df_final.sort_values(['w', '6日累計漲幅%'], ascending=False).drop(columns='w')

            # 🌟 最終樣式修正 (對齊與顏色)
            def custom_style(row):
                styles = []
                is_mine = row['代碼'] in my_stocks
                for col in row.index:
                    # 處置期間靠左，其餘置中
                    align = "left" if col == '處置期間' else "center"
                    css = f"font-size: 18px; padding: 12px; border-bottom: 1px solid #444; text-align: {align};"
                    
                    if is_mine: css += "background-color: #1A237E; color: #FFF; font-weight: bold;"
                    
                    if col == '狀態':
                        if row[col] == '🚫處置股': css += "color: white; background-color: #8B0000;"
                        elif row[col] == '📢注意股': css += "color: black; background-color: #FFD700;"
                    elif col == '分盤':
                        if row[col] == '20分': css += "color: white; background-color: #4B0082;"
                        elif row[col] == '5分': css += "color: white; background-color: #E85D04;"
                    
                    styles.append(css)
                return styles

            st.write(df_final.style.apply(custom_style, axis=1).to_html(), unsafe_allow_html=True)
