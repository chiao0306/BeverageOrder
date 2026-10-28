# 部署到 Cloud Run

## 1. 前置設定
```powershell
gcloud config set project <PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com sheets.googleapis.com drive.googleapis.com
```

## 2. 授權試算表
取得 Cloud Run 使用的服務帳戶 email（預設為 `<專案編號>-compute@developer.gserviceaccount.com`），
到 Google 試算表按「共用」，將該 email 加為**編輯者**。

## 3. 部署
```powershell
gcloud run deploy beverage-order --source . --region asia-east1 --allow-unauthenticated `
  --set-env-vars "SHEET_URL=<試算表網址>,ALLOWED_ORIGIN=<前端網域或*>"
```
`GOOGLE_APPLICATION_CREDENTIALS` 不需設定，Cloud Run 會自動使用服務帳戶身分。

## 4. 更新前端
將輸出的服務網址填入 `app.js` 的 `GAS_API_URL`。

## 本機測試
```powershell
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```


---

## 5. 設定 AI 電話訂飲料（Vapi.ai + Twilio）

本專案後端 `main.py` 已內建串接 **Vapi.ai + Twilio**，並會自動帶入**台灣繁體中文女聲 (`zh-TW-HsiaoChenNeural`)** 與**手搖飲逐項報單 Prompt**，也會在掛斷電話後自動擷取 `總金額`、`預計取餐時間`、`塑膠袋狀態` 填回前端。

### 步驟 A：準備 Twilio 帳號與發話號碼（約 5 分鐘）
1. 註冊並登入 [Twilio Console](https://console.twilio.com/)。
2. 在左側選單取得你的 **Account SID** 與 **Auth Token**。
3. **取得撥號號碼（二選一）**：
   - **驗證你自己的台灣手機作為來電顯示（Verified Caller ID）**：到 `Phone Numbers` -> `Manage` -> `Verified Caller IDs`，輸入你的手機（如 `+886912345678`）收簡訊驗證；**並購買一支最便宜的美國號碼（約 $1.15/月）匯入 Vapi 作為外撥通道**。
   - **重要設定（必勾）**：到 Twilio Console 搜尋 **Voice Geographic Permissions (Geo-Permissions)**，搜尋 **Taiwan (+886)** 並將其打勾啟用（否則 Twilio 預設不允許打給台灣號碼）。

### 步驟 B：在 Vapi.ai 綁定 Twilio 並取得兩把 ID（約 3 分鐘）
1. 註冊並登入 [Vapi Dashboard](https://dashboard.vapi.ai/)。
2. 點選左側 **Phone Numbers** -> **Import Twilio**，填入剛才的 Twilio號碼、Account SID 與 Auth Token。
3. 匯入成功後，複製該號碼頁面上的 **Phone Number ID**（格式類似 `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）👉 這就是 `VAPI_PHONE_NUMBER_ID`。
4. 點選左側 **Organization / API Keys**，複製 **Private API Key** 👉 這就是 `VAPI_API_KEY`。

### 步驟 C：設定環境變數（本機 `.env` 或 Cloud Run）
在 `.env` 加入：
```env
VAPI_API_KEY=你的_Vapi_Private_API_Key
VAPI_PHONE_NUMBER_ID=你的_Vapi_Phone_Number_ID
```

重新部署至 Cloud Run 時加上這兩個環境變數：
```powershell
gcloud run deploy beverage-order --source . --region asia-east1 --allow-unauthenticated `
  --set-env-vars "SHEET_URL=<試算表網址>,ALLOWED_ORIGIN=*,VAPI_API_KEY=<VAPI私鑰>,VAPI_PHONE_NUMBER_ID=<VAPI電話ID>"
```
