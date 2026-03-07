import streamlit as st
import requests
import urllib3
import ssl

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="連線診斷工具", page_icon="🕵️‍♂️")

st.title("🕵️‍♂️ 伺服器連線測試診斷工具 (SSL 終極版)")
st.markdown("用來測試主機加上 `verify=False` 後，是否被更底層的 OpenSSL 協定阻擋。")

# 1. 取得執行環境資訊
st.subheader("1. 確認主機環境")
try:
    ip_info = requests.get('http://ip-api.com/json/', timeout=5).json()
    st.info(f"🌐 目前主機 IP：**{ip_info.get('query')}**")
    st.info(f"📍 主機所在地：**{ip_info.get('country')} ({ip_info.get('isp')})**")
    st.info(f"🔒 OpenSSL 版本：**{ssl.OPENSSL_VERSION}**")
except Exception as e:
    st.error(f"無法取得 IP 資訊：{e}")

# 2. 測試連線
st.subheader("2. 測試連線政府 OpenAPI (強制忽略憑證)")
url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
st.write(f"連線目標：`{url}`")

if st.button("🚀 發送連線測試"):
    with st.spinner("正在向證交所發送請求，等待回應中..."):
        try:
            # 這裡明確加上 verify=False
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15, verify=False)
            
            st.write(f"**狀態碼 (Status Code):** `{res.status_code}`")
            
            if res.status_code == 200:
                st.success("✅ 連線成功！完全沒有被擋！(如果這裡成功，代表是原本主程式抓取邏輯的問題)")
                data = res.json()
                st.write(f"成功抓取到 **{len(data)}** 筆資料。")
                st.json(data[:3])
            else:
                st.error("❌ 連線被拒絕！")
                st.code(f"錯誤內容：\n{res.text[:300]}")
                
        except Exception as e:
            st.error("❌ 發生底層例外錯誤 (這就是我們要抓的兇手)：")
            st.code(str(e))
