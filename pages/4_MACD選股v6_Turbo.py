import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import urllib3
import io
import datetime as dt

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="MACD極速選股", layout="wide")

# ==========================================
# 產業代碼與工具函式
# ==========================================
INDUSTRY_CODE_MAP = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "07": "化學生技醫療", "08": "玻璃陶瓷",
    "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業",
    "13": "電子工業", "14": "建材營造", "15": "航運業", "16": "觀光餐旅",
    "17": "金融保險", "18": "貿易百貨", "19": "綜合", "20": "其他",
    "21": "化學工業", "22": "生技醫療", "23": "油電燃氣", "24": "半導體業",
    "25": "電腦及週邊", "26": "光電業", "27": "通信網路", "28": "電子零組件",
    "29": "電子通路", "30": "資訊服務", "31": "其他電子", "32": "文化創意",
    "33": "農業科技", "34": "電子商務", "35": "綠能環保", "36": "數位雲端",
    "37": "運動休閒", "38": "居家生活", "80": "管理股票", "91": "存託憑證",
}

@st.cache_data(ttl=86400)
def get_market_stocks():
    """獲取全市場股票清單，並直接排除 ETF、權證與 -KY"""
    stocks = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # 抓取上市
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", headers=headers, verify=False, timeout=5).json()
        for row in res:
            code = row.get('公司代號', '').strip()
            name = row.get('公司簡稱', '').strip()
            ind = INDUSTRY_CODE_MAP.get(row.get('產業別', ''), '其他')
            if not code.startswith('00') and len(code) < 6 and 'KY' not in name:
                stocks[code] = {'name': name, 'industry': ind, 'market': '上市'}
    except: pass
    
    # 抓取上櫃
    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/t187ap03_O", headers=headers, verify=False, timeout=5).json()
        for row in res:
            code = row.get('公司代號', '').strip()
            name = row.get('公司簡稱', '').strip()
            ind = INDUSTRY_CODE_MAP.get(row.get('產業別', ''), '其他')
            if not code.startswith('00') and len(code) < 6 and 'KY' not in name:
                stocks[code] = {'name': name, 'industry': ind, 'market': '上櫃'}
    except: pass
    return stocks

# ==========================================
# MACD 策略核心運算
# ==========================================
def calculate_macd_strategy(ticker, df, stock_name, industry):
    """計算 MACD 與均線策略"""
    clean_ticker = ticker.replace('.TW', '').replace('.TWO', '')
    
    result = {
        "產業類別": industry,
        "代號": clean_ticker,
        "名稱": stock_name,
        "現價": 0, "漲幅%": 0, "成交量": 0,
        "MA20": 0, "MACD快線": 0, "MACD慢線": 0,
        "型態描述": ""
    }

    try:
        df = df.dropna()
        if df.empty or len(df) < 60:
            return None

        # --- 指標計算 ---
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
        
        p_close, y_close = close.iloc[curr_idx], close.iloc[prev_idx]
        p_ma20, y_ma20 = ma20.iloc[curr_idx], ma20.iloc[prev_idx]
        p_ma60, y_ma60 = ma60.iloc[curr_idx], ma60.iloc[prev_idx]
        p_dif, p_dem = dif.iloc[curr_idx], dem.iloc[curr_idx]
        y_dif, y_dem = dif.iloc[prev_idx], dem.iloc[prev_idx]
        p_vol, p_vol_ma5 = volume.iloc[curr_idx], vol_ma5.iloc[curr_idx]

        result["現價"] = round(p_close, 2)
        try:
            result["漲幅%"] = round((p_close - y_close) / y_close * 100, 2)
        except:
            result["漲幅%"] = 0
        result["成交量"] = int(p_vol // 1000)
        result["MA20"] = round(p_ma20, 2)
        result["MACD快線"] = round(p_dif, 2)
        result["MACD慢線"] = round(p_dem, 2)

        # --- 篩選條件 ---
        cond_trend = (p_ma20 > y_ma20) and (p_ma60 > y_ma60)
        cond_rebound = (y_close <= y_ma20 * 1.015) and (p_close > p_ma20) and (p_close > y_close)
        cond_macd_bull = p_dif > p_dem

        if cond_trend and cond_rebound and cond_macd_bull:
            notes = ["均線回測成功"]
            if (y_dif < y_dem) and (p_dif > p_dem):
                notes.append("MACD剛金叉")
            if p_vol > p_vol_ma5:
                notes.append("量增")
            
            result["型態描述"] = "+".join(notes)
            return result
        else:
            return None 

    except Exception:
        return None

# ==========================================
# 網頁主程式
# ==========================================
st.title("🎯 MACD 技術面極速選股")
st.markdown("全市場自動掃描，尋找 **均線多頭排列 + 股價回測月線 + MACD偏多** 的潛力股。")
st.markdown("*(已自動排除 ETF、權證與 -KY 股)*")

col1, col2 = st.columns([1, 3])
with col1:
    target_date = st.date_input("選擇查詢日期", dt.date.today())
    run_btn = st.button("🚀 開始全市場掃描", use_container_width=True)

if run_btn:
    target_date_str = target_date.strftime("%Y-%m-%d")
    
    with st.spinner("正在取得全市場股票清單..."):
        market_stocks = get_market_stocks()
        
    st.info(f"已載入 {len(market_stocks)} 檔純個股，準備向 yfinance 下載歷史資料 (約需30~45秒)...")
    
    yf_tickers = []
    for code, info in market_stocks.items():
        suffix = ".TWO" if info['market'] == '上櫃' else ".TW"
        yf_tickers.append(f"{code}{suffix}")

    all_results = []
    
    # 由於需要計算 MA60 與 MACD，必須抓取至少 3 個月的資料
    with st.spinner("📦 正在下載全市場股價資料並運算技術指標..."):
        # 為了避免資料遺漏或假日起算問題，一次下載半年 (6mo) 資料確保安全
        data = yf.download(yf_tickers, period="6mo", group_by='ticker', threads=True, progress=False, auto_adjust=True)
        
        # 建立進度條
        progress_bar = st.progress(0, text="正在篩選符合條件的股票...")
        total_stocks = len(yf_tickers)
        
        for idx, ticker in enumerate(yf_tickers):
            # 更新進度條
            if idx % 100 == 0:
                progress_bar.progress(idx / total_stocks, text=f"正在篩選符合條件的股票... ({idx}/{total_stocks})")
                
            try:
                if len(yf_tickers) == 1:
                    stock_df = data
                else:
                    stock_df = data.get(ticker, pd.DataFrame())
                
                if not stock_df.empty:
                    # 去除時區與時間，對齊日期格式
                    stock_df.index = pd.to_datetime(stock_df.index).normalize()
                    # 裁切日期到使用者指定的日期
                    stock_df = stock_df[stock_df.index <= pd.Timestamp(target_date)]
                    
                    if not stock_df.empty:
                        # 檢查最後一筆資料日期是否與目標日期一致 (確保當天有交易/有開盤)
                        last_data_date = stock_df.index[-1].strftime("%Y-%m-%d")
                        if last_data_date == target_date_str:
                            code_only = ticker.split('.')[0]
                            info = market_stocks.get(code_only, {"name": "未知", "industry": "未知"})
                            
                            res = calculate_macd_strategy(ticker, stock_df, info['name'], info['industry'])
                            if res:
                                all_results.append(res)
            except:
                continue
                
        progress_bar.empty()

    # --- 輸出結果 ---
    st.divider()
    if not all_results:
        st.warning(f"⚠️ 掃描結束：在 {target_date_str} 沒有任何股票符合您的技術面條件。")
    else:
        df_output = pd.DataFrame(all_results)
        # 排序：可以優先把帶有"MACD剛金叉"的排在前面，或者以漲幅排序
        df_output = df_output.sort_values(by=["型態描述", "漲幅%"], ascending=[False, False])
        
        st.success(f"✅ 掃描完成！共找到 **{len(df_output)}** 檔符合條件的潛力股。")
        st.dataframe(df_output, hide_index=True, use_container_width=True)

        # 產出 Excel 下載
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            title_df = pd.DataFrame([f"查詢日期: {target_date_str}"])
            title_df.to_excel(writer, sheet_name='MACD選股結果', startrow=0, header=False, index=False)
            df_output.to_excel(writer, sheet_name='MACD選股結果', startrow=1, index=False)
        output.seek(0)
        
        st.download_button(
            label="📥 下載 MACD 選股報表 (Excel)", 
            data=output, 
            file_name=f"{target_date_str}_MACD極速選股.xlsx", 
            type="primary"
        )