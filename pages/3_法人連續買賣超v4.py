import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import urllib3
import io
import time
import datetime as dt

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="法人連續買賣超", layout="wide")

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

def convert_to_float(val):
    try:
        val_str = str(val).strip()
        if val_str in ['-', '', 'nan', 'None']: return 0.0
        return float(val_str.replace(',', ''))
    except: return 0.0

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

@st.cache_data(ttl=3600)
def get_fundamental_data(target_date):
    fundamental_dict = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    date_str_twse = target_date.strftime('%Y%m%d')
    try:
        res = requests.get(f"https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={date_str_twse}&selectType=ALL&response=json", headers=headers, verify=False).json()
        if res.get('stat') == 'OK':
            for row in res['data']:
                fundamental_dict[str(row[0]).strip()] = {'pe': convert_to_float(row[5]), 'pb': convert_to_float(row[6])}
    except: pass

    roc_year = target_date.year - 1911
    date_str_tpex = f"{roc_year}/{target_date.strftime('%m/%d')}"
    try:
        res = requests.get(f"https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php?l=zh-tw&o=json&d={date_str_tpex}", headers=headers, verify=False).json()
        raw = res.get('tables', [{}])[0].get('data', []) or res.get('aaData', [])
        for row in raw:
            fundamental_dict[str(row[0]).strip()] = {'pe': convert_to_float(row[2]), 'pb': convert_to_float(row[6])}
    except: pass
    return fundamental_dict

def get_chip_history(base_date, days_to_fetch=25):
    daily_data, fetched_days, attempts = {}, 0, 0
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    progress_bar = st.progress(0, text="正在回推下載籌碼歷史數據...")

    while fetched_days < days_to_fetch and attempts < 60:
        target_date = base_date - dt.timedelta(days=attempts)
        attempts += 1
        if target_date.weekday() > 4: continue
        
        date_str_twse = target_date.strftime('%Y%m%d')
        roc_year = target_date.year - 1911
        date_str_tpex = f"{roc_year}/{target_date.strftime('%m/%d')}"
        date_display = target_date.strftime('%Y-%m-%d')
        
        progress_bar.progress((fetched_days + 1) / days_to_fetch, text=f"正在獲取 {date_display} 籌碼...")
        
        day_chips, has_data = {}, False
        try:
            res_twse = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str_twse}&selectType=ALL&response=json", timeout=5, verify=False, headers=headers).json()
            if res_twse.get('stat') == 'OK':
                has_data = True
                idx_f = next((i for i, f in enumerate(res_twse['fields']) if '外陸資買賣超股數' in f), 4)
                idx_t = next((i for i, f in enumerate(res_twse['fields']) if '投信買賣超股數' in f), 10)
                for row in res_twse['data']:
                    try: day_chips[row[0]] = {'f': int(row[idx_f].replace(',', '')) // 1000, 't': int(row[idx_t].replace(',', '')) // 1000}
                    except: pass
            
            res_tpex = requests.get(f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=AL&t=D&d={date_str_tpex}", timeout=5, verify=False, headers=headers).json()
            rows = res_tpex.get('aaData', []) or res_tpex.get('tables', [{}])[0].get('data', [])
            if rows:
                has_data = True
                for row in rows:
                    try: day_chips[row[0]] = {'f': int(row[10].replace(',', '')) // 1000, 't': int(row[13].replace(',', '')) // 1000}
                    except: pass
                    
            if has_data:
                daily_data[date_display] = day_chips
                fetched_days += 1
            time.sleep(0.2)
        except: pass
        
    progress_bar.empty()
    sorted_dates = sorted(daily_data.keys(), reverse=True)
    chips_history = {}
    all_codes = set()
    for d in daily_data: all_codes.update(daily_data[d].keys())
    for code in all_codes:
        chips_history[code] = [daily_data[d].get(code, {'f': 0, 't': 0}) for d in sorted_dates]
    return chips_history

def calculate_streaks(history_list):
    f_buy = f_sell = t_buy = t_sell = 0
    for day in history_list:
        if day['f'] > 0: f_buy += 1
        else: break
    for day in history_list:
        if day['f'] < 0: f_sell += 1
        else: break
    for day in history_list:
        if day['t'] > 0: t_buy += 1
        else: break
    for day in history_list:
        if day['t'] < 0: t_sell += 1
        else: break
    return f_buy, f_sell, t_buy, t_sell

# ==========================================
# 網頁主程式
# ==========================================
st.title("🔥 法人連續買賣超全市場掃描")
st.markdown("自動掃描全市場，並已**排除 ETF、權證與 -KY 股**。")

# --- 將選項移至主畫面 ---
col1, col2 = st.columns([1, 3])
with col1:
    target_date = st.date_input("選擇查詢日期", dt.date.today())
    run_btn = st.button("🚀 開始全市場掃描", use_container_width=True)

if run_btn:
    with st.spinner("正在取得全市場股票清單..."):
        market_stocks = get_market_stocks()
        
    fund_data = get_fundamental_data(target_date)
    chips_history = get_chip_history(target_date, days_to_fetch=25)
    
    st.info(f"已載入 {len(market_stocks)} 檔純個股，開始運算連續籌碼...")
    
    # --- 效能優化：只抓取有連續買賣超的股票股價 ---
    active_codes = []
    stock_streaks = {}
    
    for code, info in market_stocks.items():
        history = chips_history.get(code, [])
        f_buy, f_sell, t_buy, t_sell = calculate_streaks(history)
        max_buy = max(f_buy, t_buy)
        max_sell = max(f_sell, t_sell)
        
        # 只要法人有動作(>0)，才加入 yfinance 下載清單
        if max_buy > 0 or max_sell > 0:
            active_codes.append(code)
            stock_streaks[code] = {
                'f_buy': f_buy, 'f_sell': f_sell, 
                't_buy': t_buy, 't_sell': t_sell,
                'max_buy': max_buy, 'max_sell': max_sell,
                'last_chip': history[0] if history else {'f': 0, 't': 0}
            }

    yf_tickers = []
    for code in active_codes:
        suffix = ".TWO" if market_stocks[code]['market'] == '上櫃' else ".TW"
        yf_tickers.append(f"{code}{suffix}")

    price_data = {}
    with st.spinner(f"正在透過 yfinance 下載 {len(yf_tickers)} 檔活躍個股股價與均量..."):
        data = yf.download(yf_tickers, period="2mo", group_by='ticker', threads=True, progress=False, auto_adjust=True)
        for ticker in yf_tickers:
            df = data if len(yf_tickers) == 1 else data.get(ticker, pd.DataFrame())
            if not df.empty:
                df.index = pd.to_datetime(df.index).normalize()
                df = df[df.index <= pd.Timestamp(target_date)]
                if len(df) > 20: 
                    price_data[ticker.split('.')[0]] = df

    # --- 整理報表 ---
    buy_list, sell_list = [], []
    
    for code in active_codes:
        info = market_stocks[code]
        streak_info = stock_streaks[code]
        
        df = price_data.get(code, pd.DataFrame())
        close = change = vol = vol_ma5 = ma20 = 0
        if not df.empty:
            last_row = df.iloc[-1]
            close = round(last_row['Close'], 2) if pd.notna(last_row['Close']) else 0
            ma20 = round(df['Close'].rolling(20).mean().iloc[-1], 2)
            vol = int(last_row['Volume'] // 1000)
            vol_ma5 = int(df['Volume'].rolling(5).mean().iloc[-1] // 1000)
            if len(df) >= 2 and df['Close'].iloc[-2] > 0:
                change = round(((close - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100, 2)

        pe_val = fund_data.get(code, {}).get('pe', 0.0)
        pb_val = fund_data.get(code, {}).get('pb', 0.0)
        nav_val = round(close / pb_val, 2) if pb_val > 0 else 0.0

        row_base = {
            "代號": code, 
            "名稱": info['name'], 
            "產業別": info['industry'], 
            "收盤": close, 
            "漲幅%": change,
            "成交量": vol, 
            "5日均量": vol_ma5, 
            "本益比": pe_val, 
            "股價淨值比": pb_val,
            "每股淨值": nav_val, 
            "MA20": ma20, 
            "外資": streak_info['last_chip']['f'], 
            "投信": streak_info['last_chip']['t']
        }

        if streak_info['max_buy'] > 0:
            r = row_base.copy()
            f_buy, t_buy = streak_info['f_buy'], streak_info['t_buy']
            if f_buy > 0 and t_buy > 0: desc = f"土洋同步連買 (外{f_buy}/投{t_buy})"
            elif f_buy > t_buy: desc = f"外資連買 {f_buy} 天"
            else: desc = f"投信連買 {t_buy} 天"
            r["連續天數"], r["詳細說明"] = streak_info['max_buy'], desc
            buy_list.append(r)

        if streak_info['max_sell'] > 0:
            r = row_base.copy()
            f_sell, t_sell = streak_info['f_sell'], streak_info['t_sell']
            if f_sell > 0 and t_sell > 0: desc = f"土洋同步連賣 (外{f_sell}/投{t_sell})"
            elif f_sell > t_sell: desc = f"外資連賣 {f_sell} 天"
            else: desc = f"投信連賣 {t_sell} 天"
            r["連續天數"], r["詳細說明"] = streak_info['max_sell'], desc
            sell_list.append(r)

    cols_order = ["代號", "名稱", "產業別", "收盤", "漲幅%", "成交量", "5日均量", "本益比", "股價淨值比", "每股淨值", "MA20", "外資", "投信", "連續天數", "詳細說明"]
    df_buy = pd.DataFrame(buy_list)[cols_order].sort_values(by="連續天數", ascending=False) if buy_list else pd.DataFrame(columns=cols_order)
    df_sell = pd.DataFrame(sell_list)[cols_order].sort_values(by="連續天數", ascending=False) if sell_list else pd.DataFrame(columns=cols_order)

    st.divider()
    st.success("✅ 全市場分析完成！")
    tab_buy, tab_sell = st.tabs(["🔥 連續買超清單", "🧊 連續賣超清單"])
    with tab_buy: st.dataframe(df_buy, hide_index=True, use_container_width=True)
    with tab_sell: st.dataframe(df_sell, hide_index=True, use_container_width=True)

    # --- 下載按鈕也移回主畫面底部 ---
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_buy.to_excel(writer, sheet_name='法人連續買超', index=False)
        df_sell.to_excel(writer, sheet_name='法人連續賣超', index=False)
    output.seek(0)
    
    st.download_button("📥 下載結果報表 (Excel)", data=output, file_name=f"{target_date}_法人連續買賣超.xlsx", type="primary")