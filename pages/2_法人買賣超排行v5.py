import streamlit as st
import requests
import pandas as pd
import datetime
import urllib3
import io
from supabase import create_client, Client

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="法人買賣超排行", layout="wide")

# --- 1. 初始化 Supabase ---
@st.cache_resource
def init_supabase() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error("❌ 找不到 Supabase Secrets 設定，請檢查 Streamlit Cloud 設定。")
        st.stop()

supabase = init_supabase()

# --- 2. 資料庫邏輯 ---
def get_chips_rank_from_cache(date_str):
    """從資料庫讀取該日買超與賣超排行"""
    try:
        res = supabase.table("chips_ranking_cache").select("*").eq("date", date_str).execute()
        if res.data:
            df = pd.DataFrame(res.data)
            # 拆分回買超與賣超
            df_buy = df[df['rank_type'] == 'buy'].sort_values('rank_no')
            df_sell = df[df['rank_type'] == 'sell'].sort_values('rank_no')
            return df_buy, df_sell
    except: pass
    return None, None

def save_chips_rank_to_cache(date_str, df_buy, df_sell):
    """將精算後的 Top 100 存入資料庫"""
    data_to_insert = []
    
    # 處理買超
    for _, row in df_buy.iterrows():
        item = row.to_dict()
        item['date'] = date_str
        item['rank_type'] = 'buy'
        item['rank_no'] = row['排名']
        data_to_insert.append(item)
    
    # 處理賣超
    for _, row in df_sell.iterrows():
        item = row.to_dict()
        item['date'] = date_str
        item['rank_type'] = 'sell'
        item['rank_no'] = row['排名']
        data_to_insert.append(item)
        
    if data_to_insert:
        try:
            # 為了避免欄位衝突，移除 DataFrame 多出的標籤欄位
            clean_data = [{k: v for k, v in d.items() if k not in ['排名', 'rank_type', 'rank_no'] or k in ['rank_type', 'rank_no']} for d in data_insert]
            # 這裡建議直接使用您的欄位清單進行過濾
            supabase.table("chips_ranking_cache").insert(data_to_insert).execute()
        except: pass

# --- 3. 原始抓取與精算邏輯 (保留您 V5 的精髓) ---
# ... (這裡包含您原本的 get_industry_map, fetch_kline_data, fetch_twse_data, fetch_tpex_data 等函式) ...

# ==========================================
# 網頁主介面
# ==========================================
st.title("📊 法人買賣超排行 (Supabase 快取版)")
st.markdown("結合雲端快取，歷史排行數據瞬時載入，無需重複精算。")

col1, col2 = st.columns([1, 3])
with col1:
    target_date = st.date_input("選擇查詢日期", datetime.date.today())
    date_str = target_date.strftime('%Y-%m-%d')
    run_btn = st.button("🚀 執行抓取與精算", width='stretch')

if run_btn:
    # STEP 1: 先查快取
    df_buy, df_sell = get_chips_rank_from_cache(date_str)
    
    if df_buy is not None:
        st.success(f"✅ 已從 Supabase 載入 {date_str} 的快取數據。")
    else:
        # STEP 2: 沒快取才執行原本耗時的運算
        with st.spinner("資料庫無紀錄，啟動全市場爬蟲與 Yahoo 精算..."):
            industry_map = get_industry_map()
            df_twse = fetch_twse_data(target_date)
            df_tpex = fetch_tpex_data(target_date)
            
            if df_twse is None and df_tpex is None:
                st.error("查無資料。")
                st.stop()
                
            # ... (執行您 V5 中間所有的數值轉換、Yahoo 精算與 apply_calibration 邏輯) ...
            
            # STEP 3: 算完後存入快取
            save_chips_rank_to_cache(date_str, df_buy, df_sell)
            st.toast("🔥 數據精算完畢，並已同步至雲端資料庫。")

    # --- 顯示結果 (與 V5 相同) ---
    st.divider()
    tab1, tab2 = st.tabs(["🚀 法人買超 Top 100", "🔻 法人賣超 Top 100"])
    with tab1:
        st.dataframe(df_buy, width='stretch', hide_index=True)
    with tab2:
        st.dataframe(df_sell, width='stretch', hide_index=True)
