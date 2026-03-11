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

# --- 2. 雲端資料庫邏輯 (維持不變) ---
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

# --- 3. 核心抓取邏輯 (分開處理上市櫃，避免連鎖錯誤) ---
def fetch_official_announcements(target_date):
    today_str_twse = target_date.strftime('%Y%m%d')
    roc_year = target_date.year - 1911
    today_str_tpex = f"{roc_year}/{target_date.strftime('%m/%d')}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    notice_set, punish_db = set(), {}

    # --- A. 證交所部分 (上市) ---
    try:
        url_n = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res_n = requests.get(url_n, timeout=10, headers=headers, verify=False)
        if res_n.status_code == 200:
            data_n = res_n.json()
            if data_n.get('stat') == 'OK':
                for row in data_n['data']:
                    for item in row:
                        val = str(item).strip()
                        if re.match(r'^\d{4}$', val): notice_set.add(val); break
        
        url_p = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res_p = requests.get(url_p, timeout=10, headers=headers, verify=False)
        if res_p.status_code == 200:
            data_p = res_p.json()
            if data_p.get('stat') == 'OK' and data_p.get('data'):
                for row in data_p['data']:
                    row_str = " ".join(str(item) for item in row)
                    code_match = re.search(r'(\d{4})', row_str)
                    if code_match:
                        code = code_match.group(1)
                        m_time = "20分" if "20分" in row_str else ("45分" if "45分" in row_str else "5分")
                        period = next((str(item) for item in row if "~" in str(item) or "～" in str(item)), "")
                        punish_db[code] = {"期間": period, "分盤": m_time}
    except Exception as e:
        st.toast(f"⚠️ 證交所資料讀取受阻")

    # --- B. 櫃買中心部分 (上櫃) - 增加錯誤攔截 ---
    try:
        # 處置股
        url_tpex_p = f"https://www.tpex.org.tw/web/stock/margin_trading/disposal/disposal_result.php?l=zh-tw&d={today_str_tpex}&o=json"
        res_tp = requests.get(url_tpex_p, timeout=10, headers=headers, verify=False)
        if res_tp.status_code == 200 and len(res_tp.text) > 10:
            data_tp = res_tp.json()
            if data_tp.get('aaData'):
                for row in data_tp['aaData']:
                    code = str(row[0]).strip()
                    m_time = "20分" if "20分" in str(row[2]) else ("45分" if "45分" in str(row[2]) else "5分")
                    punish_db[code] = {"期間": str(row[1]), "分盤": m_time}

        # 注意股
        url_tpex_n = f"https://www.tpex.org.tw/web/stock/margin_trading/attention/attention_result.php?l=zh-tw&d={today_str_tpex}&o=json"
        res_tn = requests.get(url_tpex_n, timeout=10, headers=headers, verify=False)
        if res_tn.status_code == 200 and len(res_tn.text) > 10:
            data_tn = res_tn.json()
            if data_tn.get('aaData'):
                for row in data_tn['aaData']:
                    notice_set.add(str(row[0]).strip())
    except Exception:
        # 櫃買中心 API 較不穩定，失敗時不報錯，僅以 toast 提示
        st.toast("ℹ️ 櫃買中心 API 無法連線，僅顯示上市資料")
        
    return notice_set, punish_db

# --- 4. 股票資訊映射 (維持不變) ---
@st.cache_data(ttl=86400)
def get_stock_info_map():
    headers = {'User-Agent': 'Mozilla/5.0'}
    info_map = {}
    try:
        r_l = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", headers=headers, verify=False, timeout=10).json()
        for r in r_l: info_map[r['公司代號'].strip()] = {"名稱": r['公司簡稱'].strip(), "suffix": ".TW"}
        r_o = requests.get("https://www.tpex.org.tw/openapi/v1/t187ap03_O", headers=headers, verify=False, timeout=10).json()
        for r in r_o: info_map[r['公司代號'].strip()] = {"名稱": r['公司簡稱'].strip(), "suffix": ".TWO"}
    except: pass
    return info_map

# ==========================================
# 5. 主程式流程與渲染
# ==========================================
st.title("🚨 注意 / 處置股監測 (健壯修復版)")
target_date = st.date_input("📅 選擇查詢日期", datetime.date.today())
start_btn = st.button("🚀 執行公告同步", width='stretch', type="primary")

if start_btn:
    date_str = target_date.strftime('%Y-%m-%d')
    info_map = get_stock_info_map()
    
    with st.spinner("同步公告與下載行情中..."):
        # 優先快取
        notice_set, punish_db = get_market_data_from_cache(date_str)
        if notice_set is None:
            notice_set, punish_db = fetch_official_announcements(target_date)
            # 只有在有抓到任何資料的情況下才存入快取，避免存入空白資料
            if notice_set or punish_db:
                save_market_data_to_cache(date_str, notice_set, punish_db)
        
        codes = list(set(list(notice_set) + list(punish_db.keys())))
        all_results = []
        if codes:
            # 建立 YF 查詢列表
            tickers = [f"{c}{info_map.get(c, {'suffix':'.TW'})['suffix']}" for c in codes]
            data = yf.download(tickers, period="1mo", group_by='ticker', progress=False)
            
            for c in codes:
                try:
                    ticker = f"{c}{info_map.get(c, {'suffix':'.TW'})['suffix']}"
                    df = data[ticker].dropna() if len(tickers) > 1 else data.dropna()
                    
                    last_c, prev_c = df.iloc[-1]['Close'], df.iloc[-2]['Close']
                    six_day_c = df.iloc[-7]['Close'] if len(df) >= 7 else df.iloc[0]['Close']
                    
                    status, m_time, p_period = "一般", "-", ""
                    if c in punish_db: status, m_time, p_period = "🚫處置股", punish_db[c]["分盤"], punish_db[c]["期間"]
                    elif c in notice_set: status = "📢注意股"
                    
                    all_results.append({
                        "代碼": c, "名稱": info_map.get(c, {}).get("名稱", "未知"), "狀態": status,
                        "分盤": m_time, "收盤": round(last_c, 2),
                        "單日漲幅%": round(((last_c-prev_c)/prev_c)*100, 2),
                        "6日累計漲幅%": round(((last_c-six_day_c)/six_day_c)*100, 2), "處置期間": p_period
                    })
                except: continue

        if all_results:
            df_final = pd.DataFrame(all_results)
            # 排序與權重
            status_map, time_map = {'🚫處置股': 2, '📢注意股': 1}, {'45分': 45, '20分': 20, '5分': 5, '-': 0}
            df_final['s_w'] = df_final['狀態'].map(status_map).fillna(0)
            df_final['t_w'] = df_final['分盤'].map(time_map).fillna(0)
            df_final = df_final.sort_values(by=['s_w', 't_w'], ascending=[False, False]).drop(columns=['s_w', 't_w'])

            def custom_style(row):
                styles = []
                for col in row.index:
                    align = "left" if col == '處置期間' else "center"
                    css = f"font-size: 18px; padding: 12px; border-bottom: 1px solid #444; text-align: {align};"
                    if col == '狀態':
                        if row[col] == '🚫處置股': css += "color: white; background-color: #8B0000; font-weight: bold;"
                        elif row[col] == '📢注意股': css += "color: black; background-color: #FFD700; font-weight: bold;"
                    elif col == '分盤':
                        if row[col] == '45分': css += "color: white; background-color: #000; font-weight: bold;"
                        elif row[col] == '20分': css += "color: white; background-color: #4B0082; font-weight: bold;"
                        elif row[col] == '5分': css += "color: white; background-color: #E85D04; font-weight: bold;"
                    styles.append(css)
                return styles

            st.write(df_final.style.apply(custom_style, axis=1).to_html(), unsafe_allow_html=True)
        else:
            st.info("該日期無資料或 API 暫時無法連線。")
