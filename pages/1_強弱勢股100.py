import streamlit as st
import pandas as pd
import requests
import datetime
import urllib3  # 新增：用來處理 SSL 警告

# 關閉忽略 SSL 驗證時產生的警告訊息
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(page_title="強弱勢股掃描", layout="wide", page_icon="🔥")
st.title("🔥 強弱勢飆股掃描器 (自動更新版)")
st.markdown("連線證交所與櫃買中心抓取**最新盤後資料**，瞬間篩選出盤面上爆量且高振幅的主力焦點股！")

# ==========================================
# 1. 自動連線官方 API 抓取最新資料 
# ==========================================
@st.cache_data(ttl=3600)  
def load_all_market_data():
    all_stocks = []
    
    # --- 抓取「上市」最新資料 ---
    twse_url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        # 新增 verify=False 略過 SSL 憑證檢查
        res_twse = requests.get(twse_url, timeout=10, verify=False)
        if res_twse.status_code == 200:
            for row in res_twse.json():
                try:
                    code = str(row.get('Code', '')).strip()
                    name = str(row.get('Name', '')).strip()
                    vol = int(row.get('TradeVolume', 0).replace(',', '')) // 1000
                    open_p = float(row.get('OpeningPrice', 0).replace(',', ''))
                    high_p = float(row.get('HighestPrice', 0).replace(',', ''))
                    low_p = float(row.get('LowestPrice', 0).replace(',', ''))
                    close_p = float(row.get('ClosingPrice', 0).replace(',', ''))
                    change = float(row.get('Change', 0).replace(',', ''))
                    
                    if vol > 0 and close_p > 0:
                        all_stocks.append({
                            '代碼': code, '商品': name, '開盤': open_p, '最高': high_p,
                            '最低': low_p, '收盤': close_p, '漲跌': change, '成交量(張)': vol
                        })
                except: continue
    except Exception as e:
        st.error(f"上市資料連線失敗: {e}")

    # --- 抓取「上櫃」最新資料 ---
    tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
    try:
        # 新增 verify=False 略過 SSL 憑證檢查
        res_tpex = requests.get(tpex_url, timeout=10, verify=False)
        if res_tpex.status_code == 200:
            for row in res_tpex.json():
                try:
                    code = str(row.get('SecuritiesCompanyCode', '')).strip()
                    name = str(row.get('CompanyName', '')).strip()
                    vol = int(row.get('TradingVolume', 0).replace(',', '')) 
                    open_p = float(row.get('Open', 0).replace(',', ''))
                    high_p = float(row.get('High', 0).replace(',', ''))
                    low_p = float(row.get('Low', 0).replace(',', ''))
                    close_p = float(row.get('Close', 0).replace(',', ''))
                    change = float(row.get('Change', 0).replace(',', ''))
                    
                    if vol > 0 and close_p > 0:
                        all_stocks.append({
                            '代碼': code, '商品': name, '開盤': open_p, '最高': high_p,
                            '最低': low_p, '收盤': close_p, '漲跌': change, '成交量(張)': vol
                        })
                except: continue
    except Exception as e:
        st.error(f"上櫃資料連線失敗: {e}")

    # --- 整理成 DataFrame 並計算 ---
    df = pd.DataFrame(all_stocks)
    if not df.empty:
        df['昨收'] = df['收盤'] - df['漲跌']
        df = df[df['昨收'] > 0]
        df['漲幅%'] = (df['漲跌'] / df['昨收']) * 100
        df['振幅%'] = ((df['最高'] - df['最低']) / df['昨收']) * 100
    return df

# ==========================================
# 2. 篩選介面與邏輯
# ==========================================
with st.spinner('連線官方 API 抓取最新行情中，這可能需要幾秒鐘，請稍候...'):
    df_all = load_all_market_data()

if df_all.empty:
    st.error("⚠️ 無法取得行情資料，請稍後再試或檢查網路連線。")
else:
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        scan_type = st.selectbox("🎯 掃描方向", ["📈 強勢多頭 (漲幅>0)", "📉 弱勢空頭 (跌幅<0)"])
    with col2:
        min_vol = st.number_input("📊 最低成交量 (張)", min_value=100, value=2000, step=500)
    with col3:
        min_amp = st.number_input("🎢 最低振幅 (%)", min_value=0.0, value=5.0, step=1.0)
    with col4:
        st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
        is_gap = st.checkbox("🚀 必須帶有跳空 (開高/開低)")

    # 執行篩選
    mask_vol = df_all['成交量(張)'] >= min_vol
    mask_amp = df_all['振幅%'] >= min_amp
    
    if "強勢" in scan_type:
        mask_dir = df_all['漲幅%'] > 0
        mask_gap = (df_all['開盤'] > df_all['昨收']) if is_gap else True
        df_result = df_all[mask_vol & mask_amp & mask_dir & mask_gap].sort_values('漲幅%', ascending=False)
    else:
        mask_dir = df_all['漲幅%'] < 0
        mask_gap = (df_all['開盤'] < df_all['昨收']) if is_gap else True
        df_result = df_all[mask_vol & mask_amp & mask_dir & mask_gap].sort_values('漲幅%', ascending=True)

    df_display = df_result[['代碼', '商品', '開盤', '最高', '最低', '收盤', '漲跌', '漲幅%', '振幅%', '成交量(張)']].head(50)

    # ==========================================
    # 3. 大字體與漲跌停特別標示 
    # ==========================================
    st.divider()
    
    # 取得今天日期來標示
    # today_str = datetime.date.today().strftime("%Y-%m-%d")
    # st.subheader(f"🔍 最新交易日 ({today_str}) 掃描結果：共發現 {len(df_result)} 檔標的")

    # 取得精確的現在時間 (包含時分)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    st.subheader(f"🔍 最新盤後掃描結果：共發現 {len(df_result)} 檔標的 (報表執行時間：{now_str})")
    
    if not df_display.empty:
        def custom_style(row):
            styles = []
            for col in row.index:
                css = ""
                # 收盤價粗體
                if col == '收盤': css += "font-weight: bold; "
                
                # 漲跌與漲幅顏色
                if col in ['漲跌', '漲幅%']:
                    if row[col] > 0: css += "color: #ff4b4b; "
                    elif row[col] < 0: css += "color: #1e7b1e; " 
                
                # 振幅特別標示顏色 (橘色)
                if col == '振幅%':
                    css += "color: #ff8c00; font-weight: bold; "
                
                # 漲跌停背景色 (紅/綠底)
                if row['漲幅%'] >= 9.85: css += "background-color: rgba(255, 75, 75, 0.2); "
                elif row['漲幅%'] <= -9.85: css += "background-color: rgba(0, 136, 0, 0.15); " 
                    
                styles.append(css)
            return styles

        styled_df = df_display.style.apply(custom_style, axis=1)\
                          .format({"開盤": "{:.2f}", "最高": "{:.2f}", "最低": "{:.2f}", 
                                   "收盤": "{:.2f}", "漲跌": "{:.2f}", 
                                   "漲幅%": "{:.2f} %", "振幅%": "{:.2f} %", "成交量(張)": "{:.0f}"})\
                          .hide(axis="index")\
                          .set_table_attributes('style="width: 100%; border-collapse: collapse; text-align: center;"')\
                          .set_table_styles([
                              {'selector': 'th', 'props': [('font-size', '20px'), ('text-align', 'center'), ('padding', '12px'), ('border-bottom', '2px solid #555')]},
                              {'selector': 'td', 'props': [('font-size', '20px'), ('text-align', 'center'), ('padding', '12px'), ('border-bottom', '1px solid #ddd')]}
                          ])
        
        st.markdown(styled_df.to_html(), unsafe_allow_html=True)
    else:
        st.info("💡 目前沒有符合上述條件的標的，您可以嘗試放寬「成交量」或「振幅」的條件。")

