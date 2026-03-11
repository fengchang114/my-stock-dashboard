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
        st.error("❌ 找不到 Supabase Secrets 設定")
        st.stop()

supabase = init_supabase()

# --- 2. 工具函式 ---
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

# --- 3. 核心抓取邏輯 (修正分鐘數判斷) ---
def get_official_market_data(target_date):
    date_str = target_date.strftime('%Y-%m-%d')
    today_str_twse = target_date.strftime('%Y%m%d')
    headers_base = {'User-Agent': 'Mozilla/5.0'}
    
    notice_set, punish_db = set(), {}

    try:
        # A. 注意股
        url_notice = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res = requests.get(url_notice, timeout=10, headers=headers_base, verify=False).json()
        if res.get('stat') == 'OK' and res.get('data'):
            notice_set = smart_extract_codes_to_set(res['data'])

        # B. 處置股 (優化判斷邏輯)
        url_punish = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res = requests.get(url_punish, timeout=10, headers=headers_base, verify=False).json()
        if res.get('stat') == 'OK' and res.get('data'):
            raw_data = res['data']
            code_idx, time_idx = -1, -1
            for i, col in enumerate(raw_data[0]):
                val = str(col).strip()
                if re.match(r'^\d{4}', val): code_idx = i
                elif '/' in val and len(val) > 5: time_idx = i
            
            if code_idx != -1:
                for row in raw_data:
                    code_str = str(row[code_idx]).split()[0].strip()
                    time_str = str(row[time_idx]).strip() if time_idx != -1 else ""
                    
                    # 🌟 強化版分鐘數判斷：直接搜尋關鍵數字
                    full_text = "".join(str(item) for item in row)
                    if "45分" in full_text: match_time = "45分"
                    elif "20分" in full_text: match_time = "20分"
                    elif "5分" in full_text: match_time = "5分"
                    else: match_time = "5分" # 預設

                    if code_str.isdigit() and len(code_str) == 4:
                        punish_db[code_str] = {"期間": time_str, "分盤": match_time}
    except: pass
    return notice_set, punish_db

# ==========================================
# 4. 主介面
# ==========================================
st.title("🚨 異常注意警示股雷達")
target_date = st.date_input("📅 選擇查詢日期", datetime.date.today())
start_btn = st.button("🚀 開始查核", width='stretch', type="primary")

my_stocks = ['6548', '3297', '1815', '8112', '0050', '2492']

if start_btn:
    with st.spinner("正在分析市場數據..."):
        notice_set, punish_db = get_official_market_data(target_date)
        
        # 這裡簡化 yfinance 抓取邏輯，確保 demo 正常
        # 實際使用時請保留您原本的 yf 批次下載迴圈
        # ... (中間運算略) ...

        # 假設已有 df_final
        if not df_final.empty:
            
            # 🌟 修正後的樣式渲染：針對『處置期間』靠左
            def custom_style(row):
                styles = []
                is_mine = row['代碼'] in my_stocks
                for col in row.index:
                    # 基礎設定
                    base_css = "font-size: 18px; padding: 12px; border-bottom: 1px solid #444;"
                    
                    # 🌟 靠左與置中判定
                    if col == '處置期間':
                        base_css += "text-align: left;"
                    else:
                        base_css += "text-align: center;"
                    
                    # 持股高亮
                    if is_mine:
                        base_css += "background-color: #1A237E; color: #FFF; border: 1px solid #FFD700;"
                    
                    # 狀態與顏色
                    if col == '狀態':
                        if row[col] == '🚫處置股': base_css += "color: white; background-color: #8B0000; font-weight: bold;"
                        elif row[col] == '📢注意股': base_css += "color: black; background-color: #FFD700; font-weight: bold;"
                    elif col == '分盤':
                        if row[col] == '5分': base_css += "color: white; background-color: #E85D04; font-weight: bold;"
                        elif row[col] == '20分': base_css += "color: white; background-color: #4B0082; font-weight: bold;"
                    
                    styles.append(base_css)
                return styles

            st.success("分析完成")
            st.write(df_final.style.apply(custom_style, axis=1).to_html(), unsafe_allow_html=True)
