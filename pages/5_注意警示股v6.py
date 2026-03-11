import streamlit as st
import pandas as pd
import yfinance as yf
import twstock
import requests
import datetime
import urllib3
import re
from io import StringIO
from supabase import create_client, Client

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="異常注意警示股", layout="wide", page_icon="🚨")

# --- 1. 初始化 Supabase ---
@st.cache_resource
def init_supabase() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except:
        st.error("❌ 找不到 Supabase Secrets 設定，請檢查 Cloud 設定。")
        st.stop()

supabase = init_supabase()

# --- 2. 智慧解析與快取邏輯 ---
def smart_extract_codes_to_set(data_list):
    code_set = set()
    if not data_list: return code_set
    sample_rows = data_list[:5]
    best_col_index = -1
    num_cols = len(sample_rows[0]) if sample_rows else 0
    for col_idx in range(num_cols):
        score = 0
        for row in sample_rows:
            if col_idx >= len(row): continue
            val = str(row[col_idx]).strip()
            if re.match(r'^\d{4}', val):
                if '/' in val or '-' in val: score -= 10
                else: score += 1
        if score >= len(sample_rows) * 0.8:
            best_col_index = col_idx
            break
    if best_col_index == -1: best_col_index = 1
    for row in data_list:
        if best_col_index >= len(row): continue
        raw_val = str(row[best_col_index]).strip()
        parts = raw_val.split()
        if parts:
            code = parts[0].strip()
            if code.isdigit() and len(code) == 4:
                code_set.add(code)
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
        data_to_insert.append({
            "date": date_str, "stock_id": code, "status": "處置股",
            "period": info['期間'], "match_time": info['分盤']
        })
    for code in notice_set:
        if code not in punish_db:
            data_to_insert.append({
                "date": date_str, "stock_id": code, "status": "注意股", "period": "", "match_time": "-"
            })
    if data_to_insert:
        try: supabase.table("warning_stocks_cache").insert(data_to_insert).execute()
        except: pass

@st.cache_data(ttl=86400)
def get_all_stock_tickers():
    try: twstock.__update_codes()
    except: pass
    yf_tickers, info_map = [], {}
    for code, info in twstock.codes.items():
        if info.type == '股票':
            suffix = ".TWO" if info.market == "上櫃" else ".TW"
            ticker = f"{code}{suffix}"
            yf_tickers.append(ticker)
            info_map[ticker] = {"代碼": code, "名稱": info.name}
    return yf_tickers, info_map

# --- 3. 官方數據抓取 (強化 20分盤 辨識) ---
def get_official_market_data(target_date):
    date_str = target_date.strftime('%Y-%m-%d')
    today_str_twse = target_date.strftime('%Y%m%d')
    headers_base = {'User-Agent': 'Mozilla/5.0'}
    
    # 1. 查快取
    cached_notice, cached_punish = get_market_data_from_cache(date_str)
    if cached_notice is not None:
        st.toast("✅ 已載入 Supabase 雲端快取")
        return cached_notice, cached_punish

    # 2. 爬蟲
    notice_set, punish_db = set(), {}
    try:
        # 注意股
        url_n = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res_n = requests.get(url_n, timeout=10, headers=headers_base, verify=False).json()
        if res_n.get('stat') == 'OK' and res_n.get('data'):
            notice_set = smart_extract_codes_to_set(res_n['data'])

        # 處置股
        url_p = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res_p = requests.get(url_p, timeout=10, headers=headers_base, verify=False).json()
        if res_p.get('stat') == 'OK' and res_p.get('data'):
            raw_data = res_p['data']
            code_idx, time_idx = -1, -1
            for i, col in enumerate(raw_data[0]):
                val = str(col).strip()
                if re.match(r'^\d{4}', val): code_idx = i
                elif '/' in val and len(val) > 5: time_idx = i
            
            if code_idx != -1:
                for row in raw_data:
                    code_str = str(row[code_idx]).split()[0].strip()
                    time_str = str(row[time_idx]).strip() if time_idx != -1 else ""
                    
                    # 🌟 強化判斷邏輯
                    full_text = "".join(str(item) for item in row)
                    if "45分" in full_text: m_time = "45分"
                    elif "20分" in full_text: m_time = "20分"
                    elif "5分" in full_text: m_time = "5分"
                    else: m_time = "5分"

                    if code_str.isdigit() and len(code_str) == 4:
                        punish_db[code_str] = {"期間": time_str, "分盤": m_time}
        
        save_market_data_to_cache(date_str, notice_set, punish_db)
    except: pass
    return notice_set, punish_db

# ==========================================
# 4. 執行與渲染
# ==========================================
st.title("🚨 異常注意警示股雷達")
target_date = st.date_input("📅 選擇查詢日期", datetime.date.today())
start_btn = st.button("🚀 開始查核", width='stretch', type="primary")

my_stocks = ['6548', '3297', '1815', '8112', '0050', '2492']

if start_btn:
    yf_tickers_all, info_map = get_all_stock_tickers()
    with st.spinner("正在分析市場數據..."):
        notice_set, punish_db = get_official_market_data(target_date)
        
        all_results = []
        chunk_size = 50
        yf_start = target_date - datetime.timedelta(days=45)
        yf_end = target_date + datetime.timedelta(days=1)

        # 批次下載 Yahoo 數據
        for i in range(0, len(yf_tickers_all), chunk_size):
            chunk = yf_tickers_all[i:i+chunk_size]
            try:
                data = yf.download(chunk, start=yf_start, end=yf_end, group_by='ticker', progress=False, threads=True, auto_adjust=True)
                for ticker in chunk:
                    df = data[ticker] if len(chunk) > 1 else data
                    df = df.dropna(how='all')
                    if len(df) < 7: continue
                    
                    code = info_map[ticker]["代碼"]
                    last_c, prev_c = df.iloc[-1]['Close'], df.iloc[-2]['Close']
                    six_day_c = df.iloc[-7]['Close']
                    
                    status, m_time, p_period = "一般", "-", ""
                    if code in punish_db:
                        status, m_time, p_period = "🚫處置股", punish_db[code]["分盤"], punish_db[code]["期間"]
                    elif code in notice_set:
                        status = "📢注意股"
                    
                    six_pct = ((last_c - six_day_c) / six_day_c) * 100
                    warning = "🚨達注意標準" if status == "一般" and six_pct >= 25 else ("⚠️即將注意" if status == "一般" and six_pct >= 22 else "正常")
                    
                    if status != "一般" or warning != "正常":
                        all_results.append({
                            "代碼": code, "名稱": info_map[ticker]["名稱"], "狀態": status,
                            "分盤": m_time, "預警": warning, "收盤": round(last_c, 2),
                            "單日漲幅%": round(((last_c-prev_c)/prev_c)*100, 2),
                            "6日累計漲幅%": round(six_pct, 2), "處置期間": p_period
                        })
            except: continue

        df_final = pd.DataFrame(all_results)
        if not df_final.empty:
            # 排序與權重
            df_final['w'] = df_final['狀態'].map({'🚫處置股': 3, '📢注意股': 2, '一般': 1})
            df_final = df_final.sort_values(['w', '6日累計漲幅%'], ascending=False).drop(columns='w')

            def custom_style(row):
                styles = []
                is_mine = row['代碼'] in my_stocks
                for col in row.index:
                    # 🌟 基礎設定 + 處置期間靠左
                    css = "font-size: 18px; padding: 12px; border-bottom: 1px solid #444;"
                    css += "text-align: left;" if col == '處置期間' else "text-align: center;"
                    
                    if is_mine: css += "background-color: #1A237E; color: #FFF; border: 1px solid #FFD700;"
                    
                    if col == '狀態':
                        if row[col] == '🚫處置股': css += "color: white; background-color: #8B0000; font-weight: bold;"
                        elif row[col] == '📢注意股': css += "color: black; background-color: #FFD700; font-weight: bold;"
                    elif col == '分盤':
                        if row[col] == '5分': css += "color: white; background-color: #E85D04; font-weight: bold;"
                        elif row[col] == '20分': css += "color: white; background-color: #4B0082; font-weight: bold;"
                    
                    styles.append(css)
                return styles

            st.write(df_final.style.apply(custom_style, axis=1).to_html(), unsafe_allow_html=True)
        else:
            st.info("今日無異常標的。")
