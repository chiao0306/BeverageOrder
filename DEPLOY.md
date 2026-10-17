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
