import streamlit as st
import pandas as pd
import yfinance as yf
import twstock
import requests
import datetime
import time
import urllib3
import re
import os

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="異常注意警示股", layout="wide", page_icon="🚨")

st.title("🚨 異常注意警示股雷達")
st.markdown("自動比對交易所官方公告，結合技術面 **6 日累計漲幅**，為您提前揪出「處置股」、「注意股」與「即將關禁閉」的高風險/高熱度妖股！")

# ==========================================
# 1. 智慧解析函式 (注意股用)
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

# ==========================================
# 2. 取得全市場代號清單
# ==========================================
@st.cache_data(ttl=86400)
def get_all_stock_tickers():
    try: twstock.__update_codes()
    except: pass
    
    yf_tickers = []
    info_map = {}
    for code, info in twstock.codes.items():
        if info.type == '股票':
            suffix = ".TWO" if info.market == "上櫃" else ".TW"
            ticker = f"{code}{suffix}"
            yf_tickers.append(ticker)
            info_map[ticker] = {"代碼": code, "名稱": info.name, "產業": info.group}
    return yf_tickers, info_map

# ==========================================
# 3. 抓取官方公告數據
# ==========================================
@st.cache_data(ttl=1800)
def get_official_market_data(target_date):
    chips_db, notice_set, punish_db = {}, set(), {}
    today_str = target_date.strftime('%Y%m%d')
    roc_year = target_date.year - 1911
    tpex_date_str = f"{roc_year}/{target_date.strftime('%m/%d')}"
    
    headers_base = {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json, text/javascript, */*; q=0.01'
    }

    try:
        # A. 注意股
        url_notice = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={today_str}&endDate={today_str}&response=json"
        res = requests.get(url_notice, timeout=10, headers=headers_base, verify=False).json()
        if res.get('stat') == 'OK' and res.get('data'):
            notice_set = smart_extract_codes_to_set(res['data'])

        # B. 處置股
        url_punish = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={today_str}&endDate={today_str}&response=json"
        res = requests.get(url_punish, timeout=10, headers=headers_base, verify=False).json()
        if res.get('stat') == 'OK' and res.get('data'):
            raw_data = res['data']
            code_idx, time_idx = -1, -1
            for i, col in enumerate(raw_data[0]):
                val = str(col).strip()
                if re.match(r'^\d{4}', val): code_idx = i
                elif '/' in val and len(val) > 5 and ('~' in val or '～' in val): time_idx = i
            
            if code_idx != -1:
                for row in raw_data:
                    code_str = str(row[code_idx]).split()[0].strip()
                    time_str = str(row[time_idx]).strip() if time_idx != -1 else "未抓到時間"
                    if code_str.isdigit() and len(code_str) == 4:
                        punish_db[code_str] = time_str

        # C. 三大法人籌碼
        try:
            twse_chip_url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={today_str}&selectType=ALL&response=json"
            res_c = requests.get(twse_chip_url, timeout=5, headers=headers_base, verify=False).json()
            if res_c.get('stat') == 'OK':
                for row in res_c['data']:
                    chips_db[row[0]] = {"外資": int(row[4].replace(',', '')) // 1000, "投信": int(row[10].replace(',', '')) // 1000, "自營商": int(row[11].replace(',', '')) // 1000}
        except: pass
        try:
            tpex_chip_url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=AL&t=D&d={tpex_date_str}"
            res_tc = requests.get(tpex_chip_url, timeout=5, headers=headers_base, verify=False).json()
            tpex_data = res_tc.get('aaData') or res_tc.get('tables', [{}])[0].get('data', [])
            for row in tpex_data:
                chips_db[row[0]] = {"外資": int(row[10].replace(',', '')) // 1000, "投信": int(row[13].replace(',', '')) // 1000, "自營商": int(row[22].replace(',', '')) // 1000}
        except: pass

    except Exception as e:
        st.toast(f"官方數據抓取部分發生錯誤: {e}")
        
    return chips_db, notice_set, punish_db

# ==========================================
# 4. 介面設定與操作
# ==========================================
st.divider()
col1, col2, col3 = st.columns([1, 1, 1])

with col1:
    target_date = st.date_input("📅 選擇查詢日期", datetime.date.today())
with col2:
    scan_mode = st.selectbox("🎯 選擇掃描模式", ["全市場自動掃描 (推薦)", "上傳自訂 Excel 清單"])
with col3:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    start_btn = st.button("🚀 開始連線查核", use_container_width=True)

uploaded_file = None
if scan_mode == "上傳自訂 Excel 清單":
    uploaded_file = st.file_uploader("📂 請上傳您的 Excel 檔案 (第一欄必須為股票代碼)", type=["xlsx"])

# ==========================================
# 5. 掃描執行核心
# ==========================================
if start_btn:
    if target_date.weekday() >= 5:
        st.warning("⚠️ 您選擇的是週末假日，可能無法取得當日官方資料。")
        
    yf_tickers_all, info_map = get_all_stock_tickers()
    target_tickers = []
    
    # 決定掃描清單
    if scan_mode == "全市場自動掃描 (推薦)":
        target_tickers = yf_tickers_all
    else:
        if uploaded_file is None:
            st.error("請先上傳 Excel 檔案！")
            st.stop()
        else:
            try:
                df_input = pd.read_excel(uploaded_file)
                raw_codes = df_input.iloc[:, 0].astype(str).tolist()
                for c in raw_codes:
                    clean_c = c.strip()
                    suffix = ".TWO" if clean_c in twstock.codes and twstock.codes[clean_c].market in ["上櫃", "興櫃"] else ".TW"
                    target_tickers.append(f"{clean_c}{suffix}")
            except Exception as e:
                st.error(f"讀取 Excel 失敗: {e}")
                st.stop()

    # 執行資料抓取
    with st.spinner("連線交易所擷取官方異常公告與籌碼..."):
        chips_db, notice_set, punish_db = get_official_market_data(target_date)

    st.info(f"官方數據取得完畢：共發現處置股 {len(punish_db)} 檔、注意股 {len(notice_set)} 檔。正在計算近期漲幅預警...")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    all_results = []
    chunk_size = 50
    total_chunks = (len(target_tickers) + chunk_size - 1) // chunk_size
    
    yf_start = target_date - datetime.timedelta(days=45)
    yf_end = target_date + datetime.timedelta(days=1)

    for i in range(0, len(target_tickers), chunk_size):
        chunk = target_tickers[i:i+chunk_size]
        current_chunk = (i // chunk_size) + 1
        status_text.text(f"📥 運算技術漲幅進度: {current_chunk} / {total_chunks}")
        
        try:
            data = yf.download(chunk, start=yf_start, end=yf_end, group_by='ticker', threads=True, progress=False, auto_adjust=True)
            for ticker in chunk:
                try:
                    df = data[ticker] if len(chunk) > 1 else data
                    df = df.dropna(how='all')
                    if df.empty or len(df) < 7: continue
                    
                    last_idx_date = df.index[-1].replace(tzinfo=None).date()
                    if last_idx_date > target_date: continue # 避免抓到未來的資料

                    code = info_map.get(ticker, {}).get("代碼", ticker.replace('.TW','').replace('.TWO',''))
                    last_row, prev_row = df.iloc[-1], df.iloc[-2]
                    
                    close = float(last_row['Close'])
                    change_pct = ((close - float(prev_row['Close'])) / float(prev_row['Close'])) * 100 if float(prev_row['Close']) != 0 else 0
                    
                    close_6d_ago = float(df['Close'].iloc[-7]) if len(df) >= 7 else float(df['Close'].iloc[0])
                    six_day_change = ((close - close_6d_ago) / close_6d_ago) * 100 if close_6d_ago != 0 else 0
                    
                    # 判斷官方狀態
                    status = "一般"
                    if code in punish_db: status = "🚫處置股"
                    elif code in notice_set: status = "📢注意股"
                    
                    # 判斷技術預警
                    warning = "正常"
                    if status == "一般":
                        if six_day_change >= 25: warning = "🚨達注意標準"
                        elif six_day_change >= 22: warning = "⚠️即將注意"

                    # 🌟 核心過濾機制：只保留有異常的股票！
                    if status == "一般" and warning == "正常":
                        continue

                    all_results.append({
                        "代碼": code,
                        "名稱": info_map.get(ticker, {}).get("名稱", "未知"),
                        "狀態": status,
                        "預警": warning,
                        "收盤": round(close, 2),
                        "單日漲幅%": round(change_pct, 2),
                        "6日累計漲幅%": round(six_day_change, 2),
                        "外資": chips_db.get(code, {}).get("外資", 0),
                        "投信": chips_db.get(code, {}).get("投信", 0),
                        "處置期間": punish_db.get(code, "") if status == "🚫處置股" else ""
                    })
                except: continue
        except: pass
        progress_bar.progress(current_chunk / total_chunks)

    status_text.text("✅ 掃描運算完畢！")

    # ==========================================
    # 6. 大字體 HTML 完美渲染輸出
    # ==========================================
    if all_results:
        df_final = pd.DataFrame(all_results)
        # 依照危險程度與漲幅排序
        df_final['排序權重'] = df_final['狀態'].map({'🚫處置股': 3, '📢注意股': 2, '一般': 1})
        df_final = df_final.sort_values(by=['排序權重', '6日累計漲幅%'], ascending=[False, False]).drop(columns=['排序權重'])
        
        def custom_style(row):
            styles = []
            for col in row.index:
                css = "font-size: 18px; "
                if col == '收盤': css += "font-weight: bold; "
                
                if col in ['單日漲幅%', '6日累計漲幅%']:
                    if row[col] > 0: css += "color: #ff4b4b; "
                    elif row[col] < 0: css += "color: #1e7b1e; "
                
                if col in ['外資', '投信']:
                    if row[col] > 0: css += "color: #ff4b4b; "
                    elif row[col] < 0: css += "color: #1e7b1e; "

                # 狀態專屬強調色
                if col == '狀態':
                    if row[col] == '🚫處置股': css += "color: white; background-color: #8B0000; font-weight: bold;" # 深紅底
                    elif row[col] == '📢注意股': css += "color: black; background-color: #FFD700; font-weight: bold;" # 金黃底
                
                # 預警專屬強調色
                if col == '預警':
                    if row[col] == '🚨達注意標準': css += "color: #ff4b4b; font-weight: bold;"
                    elif row[col] == '⚠️即將注意': css += "color: #ff8c00; font-weight: bold;" # 橘色

                styles.append(css)
            return styles

        styled_df = df_final.style.apply(custom_style, axis=1)\
                          .format({"收盤": "{:.2f}", "單日漲幅%": "{:.2f} %", "6日累計漲幅%": "{:.2f} %", 
                                   "外資": "{:,.0f}", "投信": "{:,.0f}"})\
                          .hide(axis="index")\
                          .set_table_attributes('style="width: 100%; border-collapse: collapse; text-align: center;"')\
                          .set_table_styles([
                              {'selector': 'th', 'props': [('font-size', '18px'), ('text-align', 'center'), ('padding', '12px'), ('border-bottom', '2px solid #555')]},
                              {'selector': 'td', 'props': [('font-size', '18px'), ('text-align', 'center'), ('padding', '12px'), ('border-bottom', '1px solid #ddd')]}
                          ])
        
        st.success(f"🔍 掃描完成！共發現 {len(df_final)} 檔異常風險標的：")
        st.markdown(styled_df.to_html(), unsafe_allow_html=True)
    else:
        st.success("🎉 掃描完成！在您選擇的範圍內，目前沒有任何股票觸發異常警示，盤面非常健康！")