import streamlit as st
import pandas as pd
import yfinance as yf
import twstock
import datetime
import os
import json

st.set_page_config(page_title="全市場MACD選股", layout="wide", page_icon="📈")

st.title("📈 全市場 MACD 爆量選股雷達")
st.markdown("將單機版程式完美移植上雲端！一鍵掃描上市櫃近 1800 檔股票，找出 **均線多頭 + MACD 轉強 + 爆量表態** 的主力飆股。")

# ==========================================
# 1. 取得全市場股票代號清單 (加入快取加快速度)
# ==========================================
@st.cache_data(ttl=86400)
def get_all_stock_tickers():
    try:
        twstock.__update_codes()
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

with st.spinner("正在同步台股上市櫃股票清單..."):
    yf_tickers, info_map = get_all_stock_tickers()

# ==========================================
# 2. 核心策略運算 (100% 移植自您的附件)
# ==========================================
def calculate_macd_strategy(ticker, df, min_volume_k):
    if df.empty or len(df) < 60:
        return None

    close = df['Close']
    volume = df['Volume']
    
    ma20 = close.rolling(window=20).mean()
    ma60 = close.rolling(window=60).mean()
    vol_ma5 = volume.rolling(window=5).mean()
    
    exp12 = close.ewm(span=12, adjust=False).mean()
    exp26 = close.ewm(span=26, adjust=False).mean()
    dif = exp12 - exp26
    dem = dif.ewm(span=9, adjust=False).mean() 

    curr_idx = -1
    prev_idx = -2
    
    p_close = close.iloc[curr_idx]
    y_close = close.iloc[prev_idx]
    
    p_ma20 = ma20.iloc[curr_idx]
    y_ma20 = ma20.iloc[prev_idx]
    p_ma60 = ma60.iloc[curr_idx]
    y_ma60 = ma60.iloc[prev_idx]
    
    p_dif = dif.iloc[curr_idx]
    p_dem = dem.iloc[curr_idx]
    y_dif = dif.iloc[prev_idx]
    y_dem = dem.iloc[prev_idx]
    
    p_vol = volume.iloc[curr_idx]
    p_vol_ma5 = vol_ma5.iloc[curr_idx]

    current_vol_k = int(p_vol // 1000)
    
    # --- 您的核心選股條件 ---
    cond_trend = (p_ma20 > y_ma20) and (p_ma60 > y_ma60)
    cond_rebound = (y_close <= y_ma20 * 1.015) and (p_close > p_ma20) and (p_close > y_close)
    cond_macd_bull = p_dif > p_dem
    cond_volume = (current_vol_k >= min_volume_k) and (p_vol > p_vol_ma5)

    if cond_trend and cond_rebound and cond_macd_bull and cond_volume:
        notes = ["均線回測成功"]
        if (y_dif < y_dem) and (p_dif > p_dem):
            notes.append("MACD剛金叉")
        notes.append("爆量轉強")
        
        pct = ((p_close - y_close) / y_close) * 100
        
        info = info_map.get(ticker, {})
        return {
            "產業類別": info.get("產業", ""),
            "代碼": info.get("代碼", ticker),
            "名稱": info.get("名稱", ""),
            "收盤": round(p_close, 2),
            "漲跌幅": pct, # 用於排序
            "漲幅%": round(pct, 2),
            "成交量(張)": current_vol_k,
            "MA20": round(p_ma20, 2),
            "MACD快線": round(p_dif, 2),
            "型態描述": " + ".join(notes)
        }
    return None

# ==========================================
# 3. 介面操作與進度條掃描區
# ==========================================
col1, col2 = st.columns(2)
with col1:
    min_volume_k = st.number_input("📊 最低成交量門檻 (張) [爆量過濾]", min_value=100, value=1000, step=500)
with col2:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    start_btn = st.button("🚀 開始全市場深度掃描", use_container_width=True)

st.divider()

if start_btn:
    if not yf_tickers:
        st.error("無法取得台股清單，請確認網路連線。")
    else:
        st.info(f"準備掃描 {len(yf_tickers)} 檔股票，這大約需要 1~2 分鐘，請喝口水稍候片刻...")
        
        # 雲端版專屬：進度條顯示
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        all_results = []
        chunk_size = 50
        total_chunks = (len(yf_tickers) + chunk_size - 1) // chunk_size
        
        # 只抓取近 150 天資料即可計算 MA60 與 MACD，大幅加快下載速度
        end_date = datetime.datetime.today() + datetime.timedelta(days=1)
        start_date = end_date - datetime.timedelta(days=150) 
        
        for i in range(0, len(yf_tickers), chunk_size):
            chunk = yf_tickers[i:i+chunk_size]
            current_chunk = (i // chunk_size) + 1
            
            status_text.text(f"📥 正在下載並運算批次 {current_chunk} / {total_chunks} ...")
            
            try:
                # 批次下載歷史 K 線
                data = yf.download(chunk, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False, auto_adjust=True)
                
                for ticker in chunk:
                    try:
                        df = data[ticker] if len(chunk) > 1 else data
                        df = df.dropna(how='all')
                        res = calculate_macd_strategy(ticker, df, min_volume_k)
                        if res:
                            all_results.append(res)
                    except: continue
            except: pass
                
            progress_bar.progress(current_chunk / total_chunks)
            
        status_text.text("✅ 全市場掃描完畢！")
        
        # ==========================================
        # 4. 大字體 HTML 完美渲染輸出
        # ==========================================
        if all_results:
            df_final = pd.DataFrame(all_results)
            df_final = df_final.sort_values(by="漲跌幅", ascending=False).drop(columns=['漲跌幅'])
            
            def custom_style(row):
                styles = []
                for col in row.index:
                    css = "font-size: 18px; "
                    if col == '收盤': css += "font-weight: bold; "
                    if col == '漲幅%':
                        if row[col] > 0: css += "color: #ff4b4b; "
                        elif row[col] < 0: css += "color: #1e7b1e; "
                    
                    if row['漲幅%'] >= 9.85: css += "background-color: rgba(255, 75, 75, 0.2); "
                    elif row['漲幅%'] <= -9.85: css += "background-color: rgba(0, 136, 0, 0.15); "
                    
                    if col == '型態描述': css += "color: #ff8c00; font-weight: bold; " # 亮橘色強調訊號
                    styles.append(css)
                return styles

            styled_df = df_final.style.apply(custom_style, axis=1)\
                              .format({"收盤": "{:.2f}", "漲幅%": "{:.2f} %", "MA20": "{:.2f}", "MACD快線": "{:.2f}", "成交量(張)": "{:.0f}"})\
                              .hide(axis="index")\
                              .set_table_attributes('style="width: 100%; border-collapse: collapse; text-align: center;"')\
                              .set_table_styles([
                                  {'selector': 'th', 'props': [('font-size', '18px'), ('text-align', 'center'), ('padding', '12px'), ('border-bottom', '2px solid #555')]},
                                  {'selector': 'td', 'props': [('font-size', '18px'), ('text-align', 'center'), ('padding', '12px'), ('border-bottom', '1px solid #ddd')]}
                              ])
            
            st.success(f"🎉 恭喜！在近 1800 檔股票中，共發現 {len(df_final)} 檔符合您主力爆量與 MACD 轉強條件的標的：")
            st.markdown(styled_df.to_html(), unsafe_allow_html=True)
        else:
            st.warning("🕵️‍♂️ 掃描完成。今日全市場沒有發現符合條件的股票，您可以考慮稍微降低「成交量」門檻再試一次。")
