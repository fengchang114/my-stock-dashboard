import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import datetime
import urllib3
import re
from supabase import create_client, Client

# 基礎設定
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="注意處置股監測", layout="wide", page_icon="🚨")

# --- 1. 初始化 Supabase ---
@st.cache_resource
def init_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = init_supabase()

# --- 2. 核心抓取邏輯 (只抓公告) ---
def get_official_list_only(target_date):
    today_str = target_date.strftime('%Y%m%d')
    headers = {'User-Agent': 'Mozilla/5.0'}
    notice_set, punish_db = set(), {}
    
    try:
        # A. 抓注意股
        url_n = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={today_str}&endDate={today_str}&response=json"
        res_n = requests.get(url_n, timeout=10, headers=headers, verify=False).json()
        if res_n.get('stat') == 'OK':
            for row in res_n['data']:
                for item in row:
                    val = str(item).strip()
                    if re.match(r'^\d{4}$', val): notice_set.add(val); break

        # B. 抓處置股 (針對台玻這類 20 分盤加強判斷)
        url_p = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={today_str}&endDate={today_str}&response=json"
        res_p = requests.get(url_p, timeout=10, headers=headers, verify=False).json()
        if res_p.get('stat') == 'OK' and res_p.get('data'):
            for row in res_p['data']:
                row_str = " ".join(str(item) for item in row)
                code_match = re.search(r'(\d{4})', row_str)
                if code_match:
                    code = code_match.group(1)
                    # 🌟 分盤判定邏輯補強
                    if "20分" in row_str or "二十分" in row_str:
                        m_time = "20分"
                    elif "45分" in row_str or "四十五分" in row_str:
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
        st.error(f"公告抓取失敗: {e}")
    return notice_set, punish_db

# --- 3. 獲取股票基本資訊 (名稱與後置碼) ---
@st.cache_data(ttl=86400)
def get_stock_info_map():
    headers = {'User-Agent': 'Mozilla/5.0'}
    info_map = {}
    try:
        # 上市
        r_l = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", headers=headers, verify=False, timeout=10).json()
        for r in r_l: info_map[r['公司代號'].strip()] = {"名稱": r['公司簡稱'].strip(), "suffix": ".TW"}
        # 上櫃
        r_o = requests.get("https://www.tpex.org.tw/openapi/v1/t187ap03_O", headers=headers, verify=False, timeout=10).json()
        for r in r_o: info_map[r['公司代號'].strip()] = {"名稱": r['公司簡稱'].strip(), "suffix": ".TWO"}
    except: pass
    return info_map

# ==========================================
# 4. 主程式渲染
# ==========================================
st.title("🚨 注意 / 處置股精確監測")
target_date = st.date_input("📅 選擇查詢日期", datetime.date.today())
start_btn = st.button("🚀 執行公告同步", width='stretch', type="primary")

# 您的持股
my_stocks = ['6548', '3297', '1815', '8112', '0050', '2492', '1802']

if start_btn:
    info_map = get_stock_info_map()
    with st.spinner("正在同步證交所最新公告並下載行情..."):
        notice_set, punish_db = get_official_list_only(target_date)
        
        # 只下載出現在名單中的股票
        codes_to_download = list(set(list(notice_set) + list(punish_db.keys())))
        all_results = []
        
        if codes_to_download:
            # 建立 yf 代號清單
            tickers = []
            for c in codes_to_download:
                suffix = info_map.get(c, {}).get("suffix", ".TW") # 預設上市
                tickers.append(f"{c}{suffix}")
            
            # 一次性下載這幾檔的數據
            data = yf.download(tickers, period="1mo", group_by='ticker', progress=False)
            
            for c in codes_to_download:
                try:
                    suffix = info_map.get(c, {}).get("suffix", ".TW")
                    ticker = f"{c}{suffix}"
                    df = data[ticker].dropna() if len(tickers) > 1 else data.dropna()
                    
                    last_c = df.iloc[-1]['Close']
                    prev_c = df.iloc[-2]['Close']
                    six_day_c = df.iloc[-7]['Close'] if len(df) >= 7 else df.iloc[0]['Close']
                    
                    status, m_time, p_period = "一般", "-", ""
                    if c in punish_db:
                        status, m_time, p_period = "🚫處置股", punish_db[c]["分盤"], punish_db[c]["期間"]
                    elif c in notice_set:
                        status = "📢注意股"
                    
                    all_results.append({
                        "代碼": c,
                        "名稱": info_map.get(c, {}).get("名稱", "未知"),
                        "狀態": status,
                        "分盤": m_time,
                        "收盤": round(last_c, 2),
                        "單日漲幅%": round(((last_c-prev_c)/prev_c)*100, 2),
                        "6日累計漲幅%": round(((last_c-six_day_c)/six_day_c)*100, 2),
                        "處置期間": p_period
                    })
                except: continue

        if all_results:
            df_final = pd.DataFrame(all_results)
            # 排序：處置 > 注意
            df_final['w'] = df_final['狀態'].map({'🚫處置股': 2, '📢注意股': 1}).fillna(0)
            df_final = df_final.sort_values('w', ascending=False).drop(columns='w')

            # 🌟 樣式定義
            def custom_style(row):
                styles = []
                is_mine = row['代碼'] in my_stocks
                for col in row.index:
                    # 處置期間靠左，其餘置中
                    align = "left" if col == '處置期間' else "center"
                    css = f"font-size: 18px; padding: 12px; border-bottom: 1px solid #444; text-align: {align};"
                    
                    # 持股高亮 (深藍底金色框)
                    if is_mine:
                        css += "background-color: #1A237E; color: #FFF; font-weight: bold; border: 1px solid #FFD700;"
                    
                    # 狀態標籤顏色
                    if col == '狀態':
                        if row[col] == '🚫處置股': css += "color: white; background-color: #8B0000;"
                        elif row[col] == '📢注意股': css += "color: black; background-color: #FFD700;"
                    
                    # 分盤顏色
                    if col == '分盤':
                        if row[col] == '20分': css += "color: white; background-color: #4B0082;" # 深紫
                        elif row[col] == '5分': css += "color: white; background-color: #E85D04;" # 橘色
                    
                    styles.append(css)
                return styles

            st.write(df_final.style.apply(custom_style, axis=1).to_html(), unsafe_allow_html=True)
        else:
            st.info(f"{target_date} 證交所無任何注意或處置股公告。")

