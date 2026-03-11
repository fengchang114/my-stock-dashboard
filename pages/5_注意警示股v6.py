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

# --- 2. 雲端資料庫邏輯 (警示股快取) ---
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

# --- 3. 股票代碼主檔管理 (全新核心) ---
def update_stock_info_to_db():
    headers = {'User-Agent': 'Mozilla/5.0'}
    stock_list = []
    try:
        # 上市
        r_l = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", headers=headers, verify=False, timeout=10).json()
        for r in r_l:
            stock_list.append({"stock_id": r['公司代號'].strip(), "stock_name": r['公司簡稱'].strip(), "market": "上市", "suffix": ".TW"})
        
        # 上櫃
        r_o = requests.get("https://www.tpex.org.tw/openapi/v1/t187ap03_O", headers=headers, verify=False, timeout=10).json()
        for r in r_o:
            stock_list.append({"stock_id": r['公司代號'].strip(), "stock_name": r['公司簡稱'].strip(), "market": "上櫃", "suffix": ".TWO"})
        
        # 新股/興櫃 手動補丁
        patch_dict = {
            '7728': '光焱科技', '4749': '新應材', '6907': '雅特力-KY',
            '7751': '竑騰', '7744': '崴寶', '7717': '萊德光電-KY'
        }
        for code, name in patch_dict.items():
            stock_list.append({"stock_id": code, "stock_name": name, "market": "上櫃", "suffix": ".TWO"})

        # 寫入 Supabase (使用 upsert 避免主鍵重複報錯)
        if stock_list:
            # 分批寫入，避免單次 Payload 過大
            chunk_size = 1000
            for i in range(0, len(stock_list), chunk_size):
                supabase.table("stock_info").upsert(stock_list[i:i+chunk_size]).execute()
            return True, len(stock_list)
    except Exception as e:
        return False, str(e)
    return False, "未知錯誤"

@st.cache_data(ttl=3600)
def get_stock_info_from_db():
    """只從我們自己的資料庫讀取股票主檔"""
    info_map = {}
    try:
        res = supabase.table("stock_info").select("*").execute()
        for row in res.data:
            info_map[row['stock_id']] = {
                "名稱": row['stock_name'],
                "市場": row['market'],
                "suffix": row['suffix']
            }
    except: pass
    return info_map

# --- 4. 核心抓取公告邏輯 (OpenAPI) ---
def fetch_official_announcements(target_date):
    today_str_twse = target_date.strftime('%Y%m%d')
    roc_date_str = f"{target_date.year - 1911}{target_date.strftime('%m%d')}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    notice_set, punish_db = set(), {}

    # 上市
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

    # 上櫃 OpenAPI
    try:
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
            
            if not is_active and str(row.get("Date")) == roc_date_str: is_active = True

            if is_active:
                cond = str(row.get("DisposalCondition", ""))
                m_time = "20分" if "20分" in cond or "二十分" in cond else ("45分" if "45分" in cond or "四十五分" in cond else "5分")
                punish_db[code] = {"期間": period, "分盤": m_time}

        res_tn = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information", headers=headers, timeout=10, verify=False).json()
        for row in res_tn:
            if str(row.get("Date")) == roc_date_str:
                code = str(row.get("SecuritiesCompanyCode", "")).strip()
                if re.match(r'^\d{4}$', code): notice_set.add(code)
    except Exception:
        st.toast("⚠️ 櫃買中心 OpenAPI 連線失敗")

    return notice_set, punish_db

# ==========================================
# 5. 側邊欄與主程式渲染
# ==========================================
# 側邊欄：資料庫主檔管理
with st.sidebar:
    st.header("⚙️ 資料庫管理區")
    st.info("初次使用或有新股上市時，請點擊下方按鈕更新全市場代碼至資料庫。")
    if st.button("🔄 同步全市場代碼至資料庫", width='stretch'):
        with st.spinner("正在抓取官方名冊寫入資料庫..."):
            success, msg = update_stock_info_to_db()
            if success:
                st.success(f"更新成功！共寫入 {msg} 筆股票代碼。")
                get_stock_info_from_db.clear() # 清除舊的快取
            else:
                st.error(f"更新失敗: {msg}")

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
            st.success("快取已清除！請重新同步。")
        except:
            st.error("清除失敗")

start_btn = st.button("🚀 執行公告同步", width='stretch', type="primary")
st.divider()

if start_btn:
    date_str = target_date.strftime('%Y-%m-%d')
    # 🌟 直接秒讀資料庫名單
    info_map = get_stock_info_from_db()
    
    if not info_map:
        st.warning("⚠️ 查無股票主檔，請先至左側邊欄點擊「同步全市場代碼至資料庫」。")
    else:
        with st.spinner("同步公告與下載行情中..."):
            notice_set, punish_db = get_market_data_from_cache(date_str)
            if notice_set is None:
                notice_set, punish_db = fetch_official_announcements(target_date)
                if notice_set or punish_db:
                    save_market_data_to_cache(date_str, notice_set, punish_db)
            
            codes = list(set(list(notice_set) + list(punish_db.keys())))
            all_results = []
            if codes:
                tickers = [f"{c}{info_map.get(c, {'suffix':'.TWO'})['suffix']}" for c in codes]
                data = yf.download(tickers, period="1mo", group_by='ticker', progress=False)
                
                for c in codes:
                    # 使用資料庫查到的資訊，找不到的預設為上櫃新股
                    market_info = info_map.get(c, {'suffix':'.TWO', '名稱':c, '市場':'上櫃'})
                    ticker = f"{c}{market_info['suffix']}"
                    
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
