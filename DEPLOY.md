# 飲料訂購系統部署與管理

## 架構與注意事項

- 靜態前端部署於 GitHub Pages；Cloud Run 的 Flask 後端以服務帳戶存取 Cloud Firestore。
- Firestore 儲存後台帳號、授權、人員名單、扣打、每日店家與訂單。執行時不再讀寫試算表；原試算表及舊訂單保留供查閱。
- 所有訂購同事依然不必登入，選擇姓名即可訂購；因此仍可能冒用他人姓名。
- 後台初始及重設密碼等於工號，容易被猜到。請透過可信方式通知本人儘速修改，勿在公開管道散布工號。
- 每天以 `Asia/Taipei` 的日期區分資料，跨日自動關店並清除當日指派，但歷史訂單仍留在 Firestore，不需 Cloud Scheduler。
- 既有 AI 電話功能仍需 Gemini、Twilio 等設定與費用；Firestore、Cloud Run、網路及備份也可能產生費用。參閱 [Firestore 定價](https://cloud.google.com/firestore/pricing?hl=zh-tw)。

## Google Cloud 與 Firestore 設定

1. 在正確的 Google Cloud 專案啟用 Cloud Run、Cloud Build、Artifact Registry、Firestore、Firebase Management 和 Firebase Rules API。專案必須有適當的計費設定。
2. 將專案加入 Firebase，建立 Firestore Native 模式的 `(default)` 資料庫，位置建議與 Cloud Run 相同。**資料庫位置建立後無法任意變更。**
3. 發布本專案的 `firestore.rules`：拒絕瀏覽器與行動用戶端直接讀寫。後端使用的 Admin SDK 走 IAM，不受這些規則限制。
4. 只授予 Cloud Run 服務帳戶必要的 Firestore IAM 權限，例如 `roles/datastore.user`。限制可管理 Firebase Console、帳號及 IAM 的人員。
5. Firestore Security Rules 與 IAM 是兩層不同的保護；規則禁止直接用戶端存取，不會限制持有高權限服務帳戶的人。

## 一次性初始化及舊資料移轉

在具有 Firestore 寫入權限的可信電腦執行；先設定 Application Default Credentials，並確認選擇正確專案：

```powershell
gcloud auth application-default login
$env:GOOGLE_CLOUD_PROJECT = "<PROJECT_ID>"
.\.venv\Scripts\pip install -r requirements.txt
```

建立第一位主管理員。工號以互動方式輸入，不放進程式碼；此指令只能成功初始化一次，不會覆寫既有帳號。

```powershell
.\.venv\Scripts\python.exe setup_firestore.py --bootstrap-admin
```

從舊 Cloud Run 服務讀取現有人員及扣打，先預覽、核對後才匯入。來源服務在切換到新版前必須仍運作；只讀取舊服務，並不修改試算表或匯入舊訂單。

```powershell
$env:OLD_API_URL = "<原 Cloud Run HTTPS 網址>"
.\.venv\Scripts\python.exe setup_firestore.py --import-api
.\.venv\Scripts\python.exe setup_firestore.py --import-api --apply
```

也可使用 `--import-sheet` 直接讀取試算表，需設定 `SHEET_URL`，且 Application Default Credentials 必須有 Sheets 與 Drive 唯讀範圍及檢視權限。若看到 `insufficient authentication scopes`，請取得適當範圍的憑證，或改用上面的 `--import-api`。

重複姓名會使匯入停止，必須先區分；若目標已有資料或已匯入則不覆寫。匯入後仍為關店，主管理員應核對姓名、扣打與舊訂單，再手動開放。舊試算表訂單不會自動顯示在新系統。

## Cloud Run 部署

保留原本 Gemini、Twilio、`AI_CALL_PASSWORD` 等環境變數；不要在命令列、程式碼或版本控制中暴露金鑰。部署前確認 `.env`、`.venv` 和憑證不在上傳來源中：

```powershell
gcloud meta list-files-for-upload
gcloud run deploy beverageorder --source . --region asia-east1 --project <PROJECT_ID> `
  --no-traffic --tag firestore-preview `
  --update-env-vars "ALLOWED_ORIGIN=https://chiao0306.github.io"
```

新修訂版先不接正式流量；使用輸出的預覽網址檢查 `/healthz`、管理登入與 Firestore 讀取。正式環境應依實際 GitHub Pages origin 調整 `ALLOWED_ORIGIN`；不要使用 `*`。若前端 API 網址不同，修改 `api-config.js`。

**新舊前後端的 API 資料格式不同，不能只切換其中一端。** 正式上線前應安排短暫維護時段，先停止舊系統訂購並確認舊訂單已妥善保存；部署包含所有新前端檔案的 GitHub Pages 版本，並將 Cloud Run 流量切至已驗證的新修訂版。完成後確認登入、未開店狀態、姓名與扣打，再開放訂購。切換期間新舊版本可能短暫不相容，應通知使用者不要送單。

保留舊修訂版及舊前端版本作為回復方案。若要回復，須同時回復前後端；回復後新系統產生的 Firestore 訂單不會出現在舊試算表，先保存並人工處理這些訂單，避免遺漏。

## 使用與故障處理

- 從訂購首頁進入「管理後台」登入。主管理員可新增／停用次級帳號、重設密碼、指派今日負責人、管理姓名及扣打；次級人員只有受指派當日可選店家。
- 每筆訂單以人員識別碼與台灣日期區分；已存在訂單需使用者確認才覆寫。管理員更改扣打後，今日總表重新計算差額。
- 今日已有訂單時，不能切換或關閉店家。若必須切換，先保存訂單並確認操作人員，再由具 Firestore Console 權限的主管理員到 `days/{YYYY-MM-DD}/orders` 查詢與刪除當日不再有效的訂單文件。刪除是永久操作，完成前應先匯出或另行保存；刪除全部有效訂單後才可切換。不要修改 `days` 的狀態來繞過檢查。
- 歷史訂單位於 `days/{YYYY-MM-DD}/orders`。停用人員不會刪除其訂單；修改姓名後，歷史文件仍保留送出當時的姓名。
- 後台帳號停用、密碼重設或修改後既有 session 失效；登入連續失敗會暫時鎖定該帳號。登入資料與限流記錄儲存在 Firestore；過期 session 可由維運人員依 `expiresAt` 定期清理。
- 主管理員忘記密碼時，具有 Firestore 寫入權限的維運人員可在可信電腦執行 `.\.venv\Scripts\python.exe setup_firestore.py --reset-admin-password`，互動輸入工號後重設為工號；所有舊 session 將失效。請儘速登入修改密碼。
- 登入成功及管理資料異動會記錄於 Firestore 的 `audit` 集合，不包含密碼。Firebase Console 直接修改或刪除資料不會經由應用程式寫入此稽核紀錄，應另外依組織規範記錄人工操作。
- 定期檢查 Cloud Run 錯誤、Firestore 用量及備份；備份與復原可能產生額外費用。Firestore 無法存取時應停止操作、保留現有資料並排除故障，不要手動以舊試算表覆寫新系統資料。

## 本機測試

```powershell
$env:FIRESTORE_EMULATOR_HOST = "127.0.0.1:8787"
$env:GOOGLE_CLOUD_PROJECT = "beverage-order-test"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check app.js
node --check management.js
```

整合測試需要先在本機 `127.0.0.1:8787` 啟動 Firestore Emulator。測試會清空 `beverage-order-test` Emulator 中的資料，**不可將測試指向正式資料庫**。
