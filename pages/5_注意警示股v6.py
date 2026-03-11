import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import datetime
import urllib3
import re
import time
from supabase import create_client, Client

# 基礎設定
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="異常注意警示股", layout="wide", page_icon="🚨")

# --- 1. 初始化 Supabase ---
@st.cache_resource
def init_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = init_supabase()

# --- 2. 核心抓取邏輯：改用最穩定的代碼來源 ---
@st.cache_data(ttl=86400)
def get_stock_list_stable():
    """從證交所開放資料直接獲取清單，避開 twstock SSL 錯誤"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    info_map = {}
    try:
        # 上市股票
        r1 = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", headers=headers, verify=False, timeout=10)
        for row in r1.json():
            code = row['公司代號'].strip()
            info_map[code] = {"名稱": row['公司簡稱'].strip(), "suffix": ".TW"}
        # 上櫃股票
        r2 = requests.get("https://www.tpex.org.tw/openapi/v1/t187ap03_O", headers=headers, verify=False, timeout=10)
        for row in r2.json():
            code = row['公司代號'].strip()
            info_map[code] = {"名稱": row['公司簡稱'].strip(), "suffix": ".TWO"}
    except:
        st.error("無法取得股票清單，請檢查網路連線。")
    return info_map

def get_official_market_data(target_date):
    today_str = target_date.strftime('%Y%m%d')
    headers = {'User-Agent': 'Mozilla/5.0'}
    notice_set, punish_db = set(), {}
    
    try:
        # 爬注意股
        url_n = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={today_str}&endDate={today_str}&response=json"
        res_n = requests.get(url_n, timeout=10, headers=headers, verify=False).json()
        if res_n.get('stat') == 'OK':
            for row in res_n['data']:
                for item in row:
                    val = str(item).strip()
                    if re.match(r'^\d{4}$', val): notice_set.add(val); break

        # 爬處置股 (台玻 20 分盤關鍵修正點)
        url_p = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={today_str}&endDate={today_str}&response=json"
        res_p = requests.get(url_p, timeout=10, headers=headers, verify=False).json()
        if res_p.get('stat') == 'OK' and res_p.get('data'):
            for row in res_p['data']:
                row_str = " ".join(str(item) for item in row)
                code_match = re.search(r'(\d{4})', row_str)
                if code_match:
                    code = code_match.group(1)
                    # 🌟 邏輯強化：只要字串包含 '20' 且包含 '分'，就判定為 20 分盤
                    if "20" in row_str and "分" in row_str:
                        m_time = "20分"
                    elif "45" in row_str and "分" in row_str:
                        m_time = "45分"
                    else:
                        m_time = "5分"
                    
                    period = ""
                    for item in row:
                        if "~" in str(item) or "～" in str(item):
                            period = str(item)
                            break
                    punish_db[code] = {"期間": period, "分盤": m_time}
    except Exception as e:
        st.error(f"公告抓取異常: {e}")
    return notice_set, punish_db

# ==========================================
# 3. 執行與顯示
# ==========================================
st.title("🚨 異常注意警示股雷達 (最終修復版)")
target_date = st.date_input("📅 選擇查詢日期", datetime.date.today())
start_btn = st.button("🚀 執行全市場掃描", width='stretch', type="primary")

# 持股清單
my_stocks = ['6548', '3297', '1815', '8112', '0050', '2492', '1802'] # 加入 1802 台玻測試

if start_btn:
    stock_info = get_stock_list_stable()
    with st.spinner("正在同步證交所公告..."):
        notice_set, punish_db = get_official_market_data(target_date)
        
        all_results = []
        # 將需要下載的代號分組
        codes_to_check = list(set(list(notice_set) + list(punish_db.keys())))
        
        # 🌟 重要：優先確保『公告名單』內的股票一定要出現
        st.write(f"今日掃描到 {len(notice_set)} 檔注意股，{len(punish_db)} 檔處置股。")

        # 為了效能，我們只下載公告中股票以及全市場部分標的
        # 這裡為了保證台玻不消失，我們直接強制下載公告標的
        check_list = []
        for code in codes_to_check:
            if code in stock_info:
                check_list.append(f"{code}{stock_info[code]['suffix']}")

        if check_list:
            data = yf.download(check_list, period="1mo", group_by='ticker', progress=False)
            
            for ticker in check_list:
                try:
                    code = ticker.split('.')[0]
                    df = data[ticker].dropna() if len(check_list) > 1 else data.dropna()
                    
                    last_c = df.iloc[-1]['Close']
                    prev_c = df.iloc[-2]['Close']
                    
                    status, m_time, p_period = "一般", "-", ""
                    if code in punish_db:
                        status, m_time, p_period = "🚫處置股", punish_db[code]["分盤"], punish_db[code]["期間"]
                    elif code in notice_set:
                        status = "📢注意股"
                    
                    # 只要是公告名單，就加入結果，不進行漲幅過濾
                    all_results.append({
                        "代碼": code, "名稱": stock_info[code]["名稱"], "狀態": status,
                        "分盤": m_time, "收盤": round(last_c, 2),
                        "單日漲幅%": round(((last_c-prev_c)/prev_c)*100, 2), "處置期間": p_period
                    })
                except: continue

        if all_results:
            df_final = pd.DataFrame(all_results).sort_values("狀態", ascending=False)
            
            def custom_style(row):
                styles = []
                is_mine = row['代碼'] in my_stocks
                for col in row.index:
                    align = "left" if col == '處置期間' else "center"
                    css = f"font-size: 18px; padding: 12px; border-bottom: 1px solid #444; text-align: {align};"
                    if is_mine: css += "background-color: #1A237E; color: #FFF; font-weight: bold; border: 1px solid #FFD700;"
                    if col == '狀態':
                        if row[col] == '🚫處置股': css += "color: white; background-color: #8B0000;"
                        elif row[col] == '📢注意股': css += "color: black; background-color: #FFD700;"
                    elif col == '分盤':
                        if row[col] == '20分': css += "color: white; background-color: #4B0082;"
                    styles.append(css)
                return styles

            st.write(df_final.style.apply(custom_style, axis=1).to_html(), unsafe_allow_html=True)
        else:
            st.info("查無資料。")
