import streamlit as st
import pandas as pd
import json
import os

st.set_page_config(page_title="強弱勢股掃描", layout="wide", page_icon="🔥")
st.title("🔥 強弱勢飆股掃描器")
st.markdown("利用本地官方資料庫，瞬間篩選出盤面上**爆量且高振幅**的主力焦點股！")

# ==========================================
# 1. 讀取並整合上市櫃本地資料 (極速掃描核心)
# ==========================================
@st.cache_data(ttl=3600)
def load_all_market_data():
    all_stocks = []
    
    # 解析上市資料
    if os.path.exists("STOCK_DAY_ALL.json"):
        try:
            with open("STOCK_DAY_ALL.json", "r", encoding="utf-8") as f:
                for row in json.load(f):
                    try:
                        code = str(row.get('Code', '')).strip()
                        name = str(row.get('Name', '')).strip()
                        # 上市成交量單位是「股」，除以 1000 變「張」
                        vol = int(row.get('TradeVolume', 0).replace(',', '')) // 1000
                        open_p = float(row.get('OpeningPrice', 0).replace(',', ''))
                        high_p = float(row.get('HighestPrice', 0).replace(',', ''))
                        low_p = float(row.get('LowestPrice', 0).replace(',', ''))
                        close_p = float(row.get('ClosingPrice', 0).replace(',', ''))
                        change = float(row.get('Change', 0).replace(',', ''))
                        
                        # 避開沒有成交或暫停交易的股票
                        if vol > 0 and close_p > 0:
                            all_stocks.append({
                                '代碼': code, '商品': name, '開盤': open_p, '最高': high_p,
                                '最低': low_p, '收盤': close_p, '漲跌': change, '成交量(張)': vol
                            })
                    except: continue
        except: pass

    # 解析上櫃資料
    if os.path.exists("dlyquote.json"):
        try:
            with open("dlyquote.json", "r", encoding="utf-8") as f:
                for row in json.load(f):
                    try:
                        code = str(row.get('SecuritiesCompanyCode', '')).strip()
                        name = str(row.get('CompanyName', '')).strip()
                        # 上櫃成交量單位是「千股」，也就是「張」
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
        except: pass

    df = pd.DataFrame(all_stocks)
    if not df.empty:
        # 計算昨收與振幅
        df['昨收'] = df['收盤'] - df['漲跌']
        # 避免除以 0 的錯誤
        df = df[df['昨收'] > 0]
        df['漲幅%'] = (df['漲跌'] / df['昨收']) * 100
        df['振幅%'] = ((df['最高'] - df['最低']) / df['昨收']) * 100
    return df

# ==========================================
# 2. 篩選介面與邏輯
# ==========================================
df_all = load_all_market_data()

if df_all.empty:
    st.error("⚠️ 找不到本地資料庫！請確認 `STOCK_DAY_ALL.json` 與 `dlyquote.json` 存在於主目錄中。")
else:
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        scan_type = st.selectbox("🎯 掃描方向", ["📈 強勢多頭 (漲幅>0)", "📉 弱勢空頭 (跌幅<0)"])
    with col2:
        min_vol = st.number_input("📊 最低成交量 (張) [爆量過濾]", min_value=100, value=2000, step=500)
    with col3:
        min_amp = st.number_input("🎢 最低振幅 (%) [波動過濾]", min_value=0.0, value=5.0, step=1.0)
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

    # 整理最後要顯示的欄位
    df_display = df_result[['代碼', '商品', '開盤', '最高', '最低', '收盤', '漲跌', '漲幅%', '振幅%', '成交量(張)']].head(50) # 最多顯示前50名

    # ==========================================
    # 3. 大字體 HTML 完美渲染
    # ==========================================
    st.divider()
    st.subheader(f"🔍 掃描結果：共發現 {len(df_result)} 檔符合條件的標的 (顯示前50檔)")
    
    if not df_display.empty:
        def custom_style(row):
            styles = []
            for col in row.index:
                css = ""
                if col == '收盤': css += "font-weight: bold; "
                
                if col in ['漲跌', '漲幅%']:
                    if row[col] > 0: css += "color: #ff4b4b; "
                    elif row[col] < 0: css += "color: #1e7b1e; " 
                
                # 振幅特別標示顏色 (橘色)
                if col == '振幅%':
                    css += "color: #ff8c00; font-weight: bold; "
                
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
