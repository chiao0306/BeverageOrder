# 部署到 Cloud Run

## 1. 前置設定
```powershell
gcloud config set project <PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com sheets.googleapis.com drive.googleapis.com
```

## 2. 授權試算表
取得 Cloud Run 使用的服務帳戶 email（預設為 `<專案編號>-compute@developer.gserviceaccount.com`），
到 Google 試算表按「共用」，將該 email 加為**編輯者**。

## 3. 部署（含 Twilio + Gemini Live API 語音訂飲料設定）

本專案後端 `main.py` 採用 **【路線二：Twilio Programmable Voice + Google Gemini Live API (`gemini-3.8-live`)】** 全自建雙向音訊串流架構：
- 當電話撥通時，Twilio 會透過 WebSocket (`wss://<你的Cloud-Run網址>/twilio-media-stream/<call_id>`) 將即時電話音訊 (`8kHz μ-law`) 串流至 Cloud Run。
- `main.py` 內建純 Python 高速音訊轉碼器，即時橋接至 **`gemini-3.8-live`** 進行雙向低延遲對話，並支援**店員插話打斷 (Barge-in)** 與即時逐字稿。
- 通話結束後，自動呼叫 **`gemini-3.8-flash`** Structured Outputs 擷取「總金額、預計取餐時間、塑膠袋狀態、備註」填回前端。

### 需要的 4 個環境變數：
1. `GEMINI_API_KEY`：從 [Google AI Studio](https://aistudio.google.com/) 取得。
2. `TWILIO_ACCOUNT_SID`：從 [Twilio Console 首頁](https://console.twilio.com/) 複製（`AC...` 開頭）。
3. `TWILIO_AUTH_TOKEN`：從 Twilio Console 首頁複製。
4. `TWILIO_FROM_NUMBER`：在 Twilio Console 首頁點 **Get a trial phone number** 免費取得的電話號碼（例如 `+1256xxxxxxx`）。
   *(💡 提示：免費 Trial 帳號可以直接撥打給你註冊 Twilio 時驗證過的那支台灣手機號碼 `+8869xxxxxxxx`，不需升級付費即可先打給自己的手機實測 AI 對話！)*

```powershell
gcloud run deploy beverage-order --source . --region asia-east1 --allow-unauthenticated `
  --set-env-vars "SHEET_URL=<試算表網址>,ALLOWED_ORIGIN=*,GEMINI_API_KEY=<Gemini金鑰>,TWILIO_ACCOUNT_SID=<AC開頭SID>,TWILIO_AUTH_TOKEN=<AuthToken>,TWILIO_FROM_NUMBER=<+1開頭Twilio號碼>"
```
`GOOGLE_APPLICATION_CREDENTIALS` 不需設定，Cloud Run 會自動使用服務帳戶身分。

## 4. 更新前端
將輸出的服務網址填入 `app.js` 的 `GAS_API_URL`。

## 本機測試
```powershell
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```
