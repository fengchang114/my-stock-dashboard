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

# --- [新增] 初始化 Supabase ---
@st.cache_resource
def init_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = init_supabase()

# ==========================================
# 1. 智慧解析函式 (保留原始邏輯)
# ==========================================
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
                elif len(val) == 8 and val.startswith("20"): score -= 10
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

# --- [新增] Supabase 讀取/寫入邏輯 ---
def get_market_data_from_cache(date_str):
    """從 Supabase 取得該日快取"""
    res = supabase.table("warning_stocks_cache").select("*").eq("date", date_str).execute()
    if res.data:
        # 重組成原始程式碼需要的格式
        notice_set = {row['stock_id'] for row in res.data if row['status'] == '注意股'}
        punish_db = {row['stock_id']: {"期間": row['period'], "分盤": row['match_time']} 
                     for row in res.data if row['status'] == '處置股'}
        return notice_set, punish_db
    return None, None

def save_market_data_to_cache(date_str, notice_set, punish_db):
    """將結果存入 Supabase"""
    data_to_insert = []
    # 處理處置股
    for code, info in punish_db.items():
        data_to_insert.append({
            "date": date_str, "stock_id": code, "status": "處置股",
            "period": info['期間'], "match_time": info['分盤']
        })
    # 處理注意股
    for code in notice_set:
        if code not in punish_db: # 避免重複存入
            data_to_insert.append({
                "date": date_str, "stock_id": code, "status": "注意股",
                "period": "", "match_time": "-"
            })
    if data_to_insert:
        supabase.table("warning_stocks_cache").insert(data_to_insert).execute()

# ==========================================
# 2. 取得全市場代號清單 (保留原始邏輯)
# ==========================================
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
            info_map[ticker] = {"代碼": code, "名稱": info.name, "產業": info.group}
    return yf_tickers, info_map

# ==========================================
# 3. 🌟 抓取官方公告數據 (整合 Supabase)
# ==========================================
def get_official_market_data(target_date):
    date_str = target_date.strftime('%Y-%m-%d')
    today_str_twse = target_date.strftime('%Y%m%d')
    
    # 1. 先查快取
    cached_notice, cached_punish = get_market_data_from_cache(date_str)
    if cached_notice is not None:
        st.toast("✅ 已載入 Supabase 雲端快取數據")
        # 籌碼資料仍需即時抓取 (因為籌碼較大，建議分開處理或維持即時)
        return {}, cached_notice, cached_punish

    # 2. 若無快取才爬蟲
    chips_db, notice_set, punish_db = {}, set(), {}
    headers_base = {'User-Agent': 'Mozilla/5.0'}

    try:
        # A. 注意股
        url_notice = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res = requests.get(url_notice, timeout=10, headers=headers_base, verify=False).json()
        if res.get('stat') == 'OK' and res.get('data'):
            notice_set = smart_extract_codes_to_set(res['data'])

        # B. 處置股
        url_punish = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res = requests.get(url_punish, timeout=10, headers=headers_base, verify=False).json()
        if res.get('stat') == 'OK' and res.get('data'):
            raw_data = res['data']
            code_idx, time_idx = -1, -1
            # ... (保留您原本的 index 尋找邏輯) ...
            for i, col in enumerate(raw_data[0]):
                val = str(col).strip()
                if re.match(r'^\d{4}', val): code_idx = i
                elif '/' in val and len(val) > 5 and ('~' in val or '～' in val): time_idx = i
            
            if code_idx != -1:
                for row in raw_data:
                    code_str = str(row[code_idx]).split()[0].strip()
                    time_str = str(row[time_idx]).strip() if time_idx != -1 else "未抓到時間"
                    row_text = "".join(str(item) for item in row)
                    if "45分" in row_text: match_time = "45分"
                    elif "20分" in row_text: match_time = "20分"
                    else: match_time = "5分"
                    if code_str.isdigit() and len(code_str) == 4:
                        punish_db[code_str] = {"期間": time_str, "分盤": match_time}

        # 3. 寫入快取
        save_market_data_to_cache(date_str, notice_set, punish_db)
        
    except Exception as e:
        st.error(f"官方數據抓取失敗: {e}")
        
    return chips_db, notice_set, punish_db

# ==========================================
# 4. 介面與顯示 (延用您的 HTML 渲染邏輯)
# ==========================================
st.title("🚨 異常注意警示股雷達 (DB快取版)")
# ... (中間 UI 邏輯與您的 5_注意警示股v6.py 完全相同) ...

# 這裡省略重複的 UI 與 yf 運算代碼，請將您原本的第 4、5、6 段直接貼在下方
# 只需確保呼叫的是 get_official_market_data(target_date) 即可！
