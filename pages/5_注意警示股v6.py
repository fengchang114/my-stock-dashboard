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
    except Exception as e:
        st.error("❌ 找不到 Supabase Secrets 設定，請檢查 Streamlit Cloud 的 Secrets 設定。")
        st.stop()

supabase = init_supabase()

# --- 2. 智慧解析與功能函式 ---
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

# --- 3. Supabase 快取邏輯 ---
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
                "date": date_str, "stock_id": code, "status": "注意股",
                "period": "", "match_time": "-"
            })
    if data_to_insert:
        try:
            supabase.table("warning_stocks_cache").insert(data_to_insert).execute()
        except: pass

# --- 4. 取得全市場代號清單 ---
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

# --- 5. 官方數據與籌碼爬蟲 ---
def get_official_market_data(target_date):
    date_str = target_date.strftime('%Y-%m-%d')
    today_str_twse = target_date.strftime('%Y%m%d')
    roc_year = target_date.year - 1911
    tpex_date_str = f"{roc_year}/{target_date.strftime('%m/%d')}"
    
    chips_db, notice_set, punish_db = {}, set(), {}
    headers_base = {'User-Agent': 'Mozilla/5.0'}

    # 1. 抓取即時籌碼 (法人買賣超)
    try:
        twse_chip_url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={today_str_twse}&selectType=ALL&response=json"
        res_c = requests.get(twse_chip_url, timeout=5, headers=headers_base, verify=False).json()
        if res_c.get('stat') == 'OK':
            for row in res_c['data']:
                chips_db[row[0]] = {"外資": int(row[4].replace(',', '')) // 1000, "投信": int(row[10].replace(',', '')) // 1000}
        
        tpex_chip_url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=AL&t=D&d={tpex_date_str}"
        res_tc = requests.get(tpex_chip_url, timeout=5, headers=headers_base, verify=False).json()
        tpex_data = res_tc.get('aaData') or res_tc.get('tables', [{}])[0].get('data', [])
        for row in tpex_data:
            chips_db[row[0]] = {"外資": int(row[10].replace(',', '')) // 1000, "投信": int(row[13].replace(',', '')) // 1000}
    except: pass

    # 2. 處理注意/處置股 (查快取或爬蟲)
    cached_notice, cached_punish = get_market_data_from_cache(date_str)
    if cached_notice is not None:
        st.toast("✅ 已載入 Supabase 雲端快取數據")
        return chips_db, cached_notice, cached_punish

    try:
        # 爬注意股
        url_notice = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res = requests.get(url_notice, timeout=10, headers=headers_base, verify=False).json()
        if res.get('stat') == 'OK' and res.get('data'):
            notice_set = smart_extract_codes_to_set(res['data'])
        
        # 爬處置股
        url_punish = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={today_str_twse}&endDate={today_str_twse}&response=json"
        res = requests.get(url_punish, timeout=10, headers=headers_base, verify=False).json()
        if res.get('stat') == 'OK' and res.get('data'):
            raw_data = res['data']
            code_idx, time_idx = -1, -1
            # 尋找代號與時間欄位
            for i, col in enumerate(raw_data[0]):
                val = str(col).strip()
                if re.match(r'^\d{4}', val): code_idx = i
                elif '/' in val and len(val) > 5: time_idx = i
            
            if code_idx != -1:
                for row in raw_data:
                    code_str = str(row[code_idx]).split()[0].strip()
                    time_str = str(row[time_idx]).strip() if time_idx != -1 else ""
                    row_text = "".join(str(item) for item in row)
                    match_time = "45分" if "45分" in row_text else ("20分" if "20分" in row_text else "5分")
                    if code_str.isdigit() and len(code_str) == 4:
                        punish_db[code_str] = {"期間": time_str, "分盤": match_time}
        
        save_market_data_to_cache(date_str, notice_set, punish_db)
    except: pass
    return chips_db, notice_set, punish_db

# --- 6. UI 與 顯示邏輯 ---
st.title("🚨 異常注意警示股雷達 (全面升級版)")
st.divider()

col1, col2, col3 = st.columns([1, 1, 1])
with col1:
    target_date = st.date_input("📅 選擇查詢日期", datetime.date.today())
with col2:
    scan_mode = st.selectbox("🎯 選擇掃描模式", ["全市場自動掃描 (推薦)", "上傳自訂 Excel 清單"])
with col3:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    start_btn = st.button("🚀 開始連線查核", width='stretch')

# 我的持股清單 (供高亮顯示)
my_stocks = ['6548', '3297', '1815', '8112', '0050', '2492']

if start_btn:
    yf_tickers_all, info_map = get_all_stock_tickers()
    target_tickers = yf_tickers_all if scan_mode == "全市場自動掃描 (推薦)" else []
    
    with st.spinner("查詢官方數據與籌碼中..."):
        chips_db, notice_set, punish_db = get_official_market_data(target_date)

    all_results = []
    chunk_size = 50
    yf_start = target_date - datetime.timedelta(days=45)
    yf_end = target_date + datetime.timedelta(days=1)

    with st.spinner(f"正在分析 {len(target_tickers)} 檔標的之技術指標..."):
        for i in range(0, len(target_tickers), chunk_size):
            chunk = target_tickers[i:i+chunk_size]
            try:
                data = yf.download(chunk, start=yf_start, end=yf_end, group_by='ticker', threads=True, progress=False, auto_adjust=True)
                for ticker in chunk:
                    try:
                        df = data[ticker] if len(chunk) > 1 else data
                        df = df.dropna(how='all')
                        if len(df) < 7: continue
                        
                        code = info_map.get(ticker, {}).get("代碼", ticker[:4])
                        last_row, prev_row = df.iloc[-1], df.iloc[-2]
                        close = float(last_row['Close'])
                        change_pct = ((close - float(prev_row['Close'])) / float(prev_row['Close'])) * 100
                        six_day_change = ((close - float(df['Close'].iloc[-7])) / float(df['Close'].iloc[-7])) * 100
                        
                        status, match_time, punish_period = "一般", "-", ""
                        if code in punish_db: 
                            status, match_time, punish_period = "🚫處置股", punish_db[code]["分盤"], punish_db[code]["期間"]
                        elif code in notice_set: 
                            status = "📢注意股"
                        
                        warning = "正常"
                        if status == "一般":
                            if six_day_change >= 25: warning = "🚨達注意標準"
                            elif six_day_change >= 22: warning = "⚠️即將注意"
                        
                        # 過濾條件：只顯示異常或注意的標的
                        if status == "一般" and warning == "正常": continue

                        all_results.append({
                            "代碼": code, "名稱": info_map.get(ticker, {}).get("名稱", "未知"),
                            "狀態": status, "分盤": match_time, "預警": warning,
                            "收盤": round(close, 2), "單日漲幅%": round(change_pct, 2),
                            "6日累計漲幅%": round(six_day_change, 2),
                            "外資": chips_db.get(code, {}).get("外資", 0),
                            "投信": chips_db.get(code, {}).get("投信", 0),
                            "處置期間": punish_period
                        })
                    except: continue
            except: pass

    if all_results:
        df_final = pd.DataFrame(all_results)
        
        # 1. 排序權重 (處置>注意>預警)
        df_final['weight'] = df_final['狀態'].map({'🚫處置股': 3, '📢注意股': 2, '一般': 1})
        df_final = df_final.sort_values(by=['weight', '6日累計漲幅%'], ascending=[False, False]).drop(columns=['weight'])

        # 2. 定義樣式
        def custom_style(row):
            styles = []
            is_mine = row['代碼'] in my_stocks
            for col in row.index:
                # 基礎樣式與持股高亮 (深藍色底)
                base_css = "font-size: 18px; text-align: center; padding: 12px; border-bottom: 1px solid #444;"
                if is_mine: base_css += "background-color: #1A237E; color: #FFF; border: 2px solid #FFD700;"
                
                # 狀態與顏色渲染
                if col == '狀態':
                    if row[col] == '🚫處置股': base_css += "color: white; background-color: #8B0000; font-weight: bold;"
                    elif row[col] == '📢注意股': base_css += "color: black; background-color: #FFD700; font-weight: bold;"
                elif col == '分盤':
                    if row[col] == '5分': base_css += "color: white; background-color: #E85D04; font-weight: bold;"
                    elif row[col] == '20分': base_css += "color: white; background-color: #4B0082; font-weight: bold;"
                    elif row[col] == '45分': base_css += "color: white; background-color: #000; font-weight: bold;"
                
                styles.append(base_css)
            return styles

        st.success(f"🔍 掃描完成！共發現 {len(df_final)} 檔異常標的")
        
        # 使用 HTML 渲染
        st.write(df_final.style.apply(custom_style, axis=1).to_html(), unsafe_allow_html=True)
    else:
        st.info("今日無異常注意/處置股。")
