import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import datetime
import urllib3
import re
import twstock
from supabase import create_client, Client

# 基礎設定
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="注意處置股監測", layout="wide", page_icon="🚨")

# --- 1. 初始化 Supabase ---
@st.cache_resource
def init_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = init_supabase()

# --- 2. 雲端資料庫邏輯 ---
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

# --- 3. 核心抓取邏輯 (OpenAPI) ---
def fetch_official_announcements(target_date):
    today_str_twse = target_date.strftime('%Y%m%d')
    roc_date_str = f"{target_date.year - 1911}{target_date.strftime('%m%d')}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    notice_set, punish_db = set(), {}

    # --- A. 證交所 (上市) ---
    try:
        url_n = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res_n = requests.get(url_n, timeout=10, headers=headers, verify=False).json()
        if res_n.get('stat') == 'OK':
            for row in res_n['data']:
                for item in row:
                    val = str(item).strip()
                    if re.match(r'^\d{4}$', val): notice_set.add(val); break
        
        url_p = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res_p = requests.get(url_p, timeout=10, headers=headers, verify=False).json()
        if res_p.get('stat') == 'OK' and res_p.get('data'):
            for row in res_p['data']:
                row_str = " ".join(str(item) for item in row)
                code_match = re.search(r'(\d{4})', row_str)
                if code_match:
                    code = code_match.group(1)
                    m_time = "20分" if "20分" in row_str or "二十分" in row_str else ("45分" if "45分" in row_str or "四十五分" in row_str else "5分")
                    period = next((str(item) for item in row if "~" in str(item) or "～" in str(item)), "")
                    punish_db[code] = {"期間": period, "分盤": m_time}
    except Exception:
        st.toast("⚠️ 證交所資料讀取受阻")

    # --- B. 櫃買中心 OpenAPI (上櫃) ---
    try:
        # 處置股
        res_tp = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information", headers=headers, timeout=10, verify=False).json()
        for row in res_tp:
            code = str(row.get("SecuritiesCompanyCode", "")).strip()
            if not re.match(r'^\d{4}$', code): continue
            
            period = str(row.get("DispositionPeriod", ""))
            is_active = False
            if "~" in period or "～" in period:
                parts = re.split(r'[~～]', period)
                if len(parts) >= 2:
                    start_d, end_d = parts[0].strip(), parts[1].strip()
                    if len(start_d) == len(roc_date_str) and len(end_d) == len(roc_date_str):
                        if start_d <= roc_date_str <= end_d: is_active = True
            
            if not is_active and str(row.get("Date")) == roc_date_str:
                is_active = True

            if is_active:
                cond = str(row.get("DisposalCondition", ""))
                m_time = "20分" if "20分" in cond or "二十分" in cond else ("45分" if "45分" in cond or "四十五分" in cond else "5分")
                punish_db[code] = {"期間": period, "分盤": m_time}

        # 注意股
        res_tn = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information", headers=headers, timeout=10, verify=False).json()
        for row in res_tn:
            if str(row.get("Date")) == roc_date_str:
                code = str(row.get("SecuritiesCompanyCode", "")).strip()
                if re.match(r'^\d{4}$', code): notice_set.add(code)
    except Exception:
        st.toast("⚠️ 櫃買中心 OpenAPI 連線失敗")

    return notice_set, punish_db

@st.cache_data(ttl=86400)
def get_stock_info_map():
    info_map = {}
    for code, info in twstock.codes.items():
        if info.type == '股票':
            info_map[code] = {
                "名稱": info.name, 
                "suffix": ".TW" if info.market == "上市" else ".TWO", 
                "市場": "上市" if info.market == "上市" else "上櫃"
            }
            
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r_l = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", headers=headers, verify=False, timeout=5).json()
        for r in r_l: info_map[r['公司代號'].strip()] = {"名稱": r['公司簡稱'].strip(), "suffix": ".TW", "市場": "上市"}
        r_o = requests.get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", headers=headers, verify=False, timeout=5).json()
        for r in r_o: info_map[r['公司代號'].strip()] = {"名稱": r['公司簡稱'].strip(), "suffix": ".TWO", "市場": "上櫃"}
    except: pass
    return info_map

# ==========================================
# 4. 主程式渲染
# ==========================================
st.title("🚨 上市 / 上櫃 警示股監測")

col1, col2 = st.columns([3, 1])
with col1:
    target_date = st.date_input("📅 選擇查詢日期", datetime.date.today())
with col2:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    if st.button("🧹 清除本日快取", width='stretch'):
        date_str = target_date.strftime('%Y-%m-%d')
        try:
            supabase.table("warning_stocks_cache").delete().eq("date", date_str).execute()
            st.success("快取已清除！請點擊下方同步按鈕。")
        except:
            st.error("清除失敗")

start_btn = st.button("🚀 執行公告同步", width='stretch', type="primary")
st.divider()

if start_btn:
    date_str = target_date.strftime('%Y-%m-%d')
    info_map = get_stock_info_map()
    
    with st.spinner("同步公告與下載行情中..."):
        notice_set, punish_db = get_market_data_from_cache(date_str)
        if notice_set is None:
            notice_set, punish_db = fetch_official_announcements(target_date)
            if notice_set or punish_db:
                save_market_data_to_cache(date_str, notice_set, punish_db)
        
        codes = list(set(list(notice_set) + list(punish_db.keys())))
        all_results = []
        if codes:
            tickers = [f"{c}{info_map.get(c, {'suffix':'.TWO'})['suffix']}" for c in codes] # 找不到預設給上櫃
            data = yf.download(tickers, period="1mo", group_by='ticker', progress=False)
            
            for c in codes:
                # 🌟 絕對不剔除機制
                market_info = info_map.get(c, {'suffix':'.TWO', '名稱':c, '市場':'上櫃'}) # 若真找不到資訊，預設歸類為上櫃
                ticker = f"{c}{market_info['suffix']}"
                
                # 初始化空值
                last_c, day_change, six_change = "-", "-", "-"
                
                if ticker in data and not data[ticker].dropna().empty:
                    df = data[ticker].dropna()
                    if len(df) >= 2:
                        last_c = round(df.iloc[-1]['Close'], 2)
                        prev_c = df.iloc[-2]['Close']
                        six_day_c = df.iloc[-7]['Close'] if len(df) >= 7 else df.iloc[0]['Close']
                        day_change = round(((last_c-prev_c)/prev_c)*100, 2)
                        six_change = round(((last_c-six_day_c)/six_day_c)*100, 2)
                    elif len(df) == 1:
                        last_c = round(df.iloc[-1]['Close'], 2)

                status, m_time, p_period = "一般", "-", ""
                if c in punish_db: status, m_time, p_period = "🚫處置股", punish_db[c]["分盤"], punish_db[c]["期間"]
                elif c in notice_set: status = "📢注意股"
                
                # 永遠無條件加入名單
                all_results.append({
                    "市場": market_info['市場'],
                    "代碼": c, "名稱": market_info['名稱'], "狀態": status,
                    "分盤": m_time, "收盤": last_c,
                    "單日漲幅%": day_change,
                    "6日累計漲幅%": six_change, "處置期間": p_period
                })

        if all_results:
            df_final = pd.DataFrame(all_results)
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

            tab1, tab2 = st.tabs(["🏢 上市警示股 (TWSE)", "🏪 上櫃警示股 (TPEX)"])
            
            with tab1:
                df_twse = df_final[df_final['市場'] == '上市'].drop(columns=['市場'])
                if not df_twse.empty:
                    st.write(df_twse.style.apply(custom_style, axis=1).to_html(), unsafe_allow_html=True)
                else:
                    st.info("今日無上市注意或處置公告。")

            with tab2:
                df_tpex = df_final[df_final['市場'] == '上櫃'].drop(columns=['市場'])
                if not df_tpex.empty:
                    st.write(df_tpex.style.apply(custom_style, axis=1).to_html(), unsafe_allow_html=True)
                else:
                    st.info("今日無上櫃注意或處置公告。")
        else:
            st.warning("該日期查無任何上市櫃注意或處置資料。")
