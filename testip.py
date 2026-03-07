import streamlit as st
import requests

st.set_page_config(page_title="連線診斷工具", page_icon="🕵️‍♂️")

st.title("🕵️‍♂️ 伺服器連線測試診斷工具")
st.markdown("用來測試主機是否被台灣證交所防火牆阻擋。")

# 1. 取得執行這支程式的主機 IP 及其地理位置
st.subheader("1. 確認目前的對外 IP")
try:
    ip_info = requests.get('http://ip-api.com/json/', timeout=5).json()
    st.info(f"🌐 目前主機 IP：**{ip_info.get('query')}**")
    st.info(f"📍 主機所在地：**{ip_info.get('country')} ({ip_info.get('isp')})**")
except Exception as e:
    st.error(f"無法取得 IP 資訊：{e}")

# 2. 直接向政府 OpenAPI 敲門，並印出真實的回應
st.subheader("2. 測試連線政府 OpenAPI")
url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
st.write(f"連線目標：`{url}`")

if st.button("🚀 點我發送連線請求測試"):
    with st.spinner("正在向證交所發送請求，等待回應中 (最多等待 15 秒)..."):
        try:
            # 設定 15 秒 Timeout，模擬真實抓取情況
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            
            st.write(f"**狀態碼 (Status Code):** `{res.status_code}`")
            
            if res.status_code == 200:
                st.success("✅ 連線成功！完全沒有被擋！")
                data = res.json()
                st.write(f"成功抓取到 **{len(data)}** 筆資料。")
                st.json(data[:3]) # 印出前三筆證明真的有抓到
            else:
                st.error("❌ 連線被拒絕！")
                st.code(f"錯誤內容：\n{res.text[:300]}")
                
        except requests.exceptions.Timeout:
            st.error("❌ 連線逾時 (Timeout)！")
            st.markdown("💡 **診斷結果：** 證交所防火牆看到了這個 IP，不給出 403 拒絕，而是直接把連線請求『丟棄 (Drop)』，導致主機傻傻等了 15 秒都等不到回應。這也是最常見的鎖 IP 方式。")
        except Exception as e:
            st.error(f"❌ 發生其他錯誤：\n{e}")