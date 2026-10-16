# 部署到 Cloud Run

## 1. 前置設定
```powershell
gcloud config set project <PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com sheets.googleapis.com drive.googleapis.com secretmanager.googleapis.com
```

## 2. 授權試算表
取得 Cloud Run 使用的服務帳戶 email（預設為 `<專案編號>-compute@developer.gserviceaccount.com`），
到 Google 試算表按「共用」，將該 email 加為**編輯者**。

## 3. 儲存 Gemini API Key
```powershell
echo -n "<GEMINI_API_KEY>" | gcloud secrets create gemini-api-key --data-file=-
gcloud secrets add-iam-policy-binding gemini-api-key `
  --member="serviceAccount:<專案編號>-compute@developer.gserviceaccount.com" `
  --role="roles/secretmanager.secretAccessor"
```

## 4. 部署
```powershell
gcloud run deploy beverage-order --source . --region asia-east1 --allow-unauthenticated `
  --set-env-vars "SHEET_URL=<試算表網址>,ALLOWED_ORIGIN=<前端網域或*>" `
  --set-secrets "GEMINI_API_KEY=gemini-api-key:latest"
```
`GOOGLE_APPLICATION_CREDENTIALS` 不需設定，Cloud Run 會自動使用服務帳戶身分。

## 5. 更新前端
將輸出的服務網址填入 `app.js` 的 `GAS_API_URL`。

## 本機測試
```powershell
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```
