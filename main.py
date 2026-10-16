"""飲料訂購後端：由 Google Apps Script (code.gs) 改寫，供 Cloud Run 部署使用。"""
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import requests
from dotenv import load_dotenv
from flask import Flask, Response, request
from gspread.utils import ValueRenderOption

# 本機開發時讀取 .env；Cloud Run 上則直接使用環境變數
load_dotenv()

SHEET_URL = os.environ.get("SHEET_URL", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
# 服務帳戶金鑰檔路徑；未設定時使用 Cloud Run 的預設服務帳戶 (ADC)
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

TZ = ZoneInfo("Asia/Taipei")
COMPANY_CONTEXT = "我們是中鋼公司「手銲股」的夥伴，工作時常需電銲，眼睛跟精神容易疲勞，會想靠飲料回血。"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

app = Flask(__name__)

_gc = None
# Cloud Run 為無狀態，此快取只在單一執行個體存活期間有效（原本存於 ScriptProperties）
_ai_cache = {}


def get_sheet():
    """取得試算表第一個工作表；用戶端重複使用以避免每次重新授權。"""
    global _gc
    if _gc is None:
        if GOOGLE_APPLICATION_CREDENTIALS:
            _gc = gspread.service_account(
                filename=GOOGLE_APPLICATION_CREDENTIALS, scopes=SCOPES
            )
        else:
            import google.auth

            creds, _ = google.auth.default(scopes=SCOPES)
            _gc = gspread.authorize(creds)
    return _gc.open_by_url(SHEET_URL).get_worksheet(0)


def json_response(result):
    return Response(
        json.dumps(result, ensure_ascii=False), mimetype="application/json"
    )


@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/", methods=["OPTIONS"])
def preflight():
    return Response(status=204)


@app.route("/healthz", methods=["GET"])
def healthz():
    return "ok"


# ==========================================
# 路由：對應原本的 doGet / doPost
# ==========================================
@app.route("/", methods=["GET"])
def do_get():
    action = request.args.get("action")
    result = {}
    try:
        if action == "getStaffNames":
            result["data"] = get_staff_names()
        elif action == "getCurrentShop":
            result["data"] = get_current_shop()
        elif action == "getOrderData":
            result["data"] = get_order_data()
        else:
            result["error"] = f"未知的 GET 請求: {action}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return json_response(result)


@app.route("/", methods=["POST"])
def do_post():
    result = {}
    try:
        # 前端以 text/plain 送出 JSON（避免 CORS preflight），故需強制解析
        payload = json.loads(request.get_data(as_text=True))
        action = payload.get("action")
        if action == "addOrder":
            result["data"] = add_order(payload["orderData"])
        elif action == "getAiRecommendation":
            result["data"] = fetch_ai_recommendation(
                payload.get("shopName", ""),
                payload.get("menuText", ""),
                payload.get("requestType", ""),
            )
        else:
            result["error"] = f"未知的 POST 請求: {action}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return json_response(result)


# ==========================================
# 核心邏輯
# ==========================================
def get_staff_names():
    """讀取 A 欄第 3 列起的人員名單，略過空白。"""
    names = get_sheet().col_values(1)[2:]
    return [n.strip() for n in names if n.strip()]


def get_current_shop():
    """讀取 A1~D1：標題、店家、扣打標籤、扣打數值。"""
    try:
        row = get_sheet().get(
            "A1:D1", value_render_option=ValueRenderOption.unformatted
        )
        cells = (row[0] if row else []) + [""] * 4
        return {
            "title": str(cells[0]).strip(),
            "shop": str(cells[1]).strip(),
            "quotaLabel": str(cells[2]).strip(),
            "quotaValue": cells[3],
        }
    except Exception as e:
        raise RuntimeError(f"試算表讀取失敗: {e}")


def get_order_data():
    """回傳第 2 列起的所有資料，並補齊各列長度以對齊 getValues 的行為。"""
    rows = get_sheet().get_all_values(
        value_render_option=ValueRenderOption.unformatted
    )[1:]
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def add_order(data):
    """依姓名更新既有訂單列；找不到則新增一列。"""
    sheet = get_sheet()
    names = sheet.col_values(1)
    row_index = -1
    for i in range(2, len(names)):
        if names[i].strip() == data["name"].strip():
            row_index = i + 1
            break

    order_row = [
        data.get("drink"),
        data.get("size"),
        data.get("sugar"),
        data.get("ice"),
        data.get("topping"),
        data.get("totalPrice"),
        data.get("diff"),
    ]
    order_row = ["" if v is None else v for v in order_row]

    if row_index != -1:
        sheet.update(
            range_name=f"B{row_index}:H{row_index}",
            values=[order_row],
            value_input_option="USER_ENTERED",
        )
    else:
        sheet.append_row([data["name"]] + order_row, value_input_option="USER_ENTERED")
    return "成功"


def build_prompt(shop_name, menu_text):
    return f"""
  你是一個超懂台灣手搖飲的達人。請務必使用 Google 搜尋（包含店家官網、PTT、Dcard、美食部落格等網路評價），針對「{shop_name}」這家飲料店，一次性推薦 6 種不同方向的飲料給使用者。
  使用者背景：{COMPANY_CONTEXT}

  【各分類的推薦準則（請強烈依賴網路搜尋結果）】：
  - "招牌"：請根據店家官方主推、主打星，或是最廣為人知的經典飲料來推薦（偏向官方說法）。
  - "純茶"：網路上評價最好的解渴純茶類。
  - "奶類"：網路上最多人推的濃郁奶類或鮮奶茶。
  - "新鮮"：網路上推薦的果茶、果汁或酸甜系飲品。
  - "熱飲"：網路上推薦適合做成溫熱飲的品項。
  - "內行"：請完全根據 PTT、Dcard 等網友的真實無業配分享，推薦「隱藏點法」、「神級甜度冰塊比例」或「老饕專屬配料搭法」（偏向真實客戶說法）。

  【極度嚴格的輸出格式要求 - 攸關系統存亡】：
  1. 你「必須」且「只能」輸出純 JSON 格式的字串。
  2. 絕對不要加上任何開場白、結尾問候。
  3. 絕對不要在前後加上 ```json 或是 ``` 的標記，直接用 {{ 開頭，用 }} 結尾。
  4. JSON 的 Key 必須完全精確是這六個字串："招牌", "純茶", "奶類", "新鮮", "熱飲", "內行"。
  5. 每個 Value 是約 80~100 字的推薦理由（語氣要專業，有禮貌），並給出建議的甜度冰塊。

  【目前可選菜單（請用來核對網路上推薦的飲料是否還有在賣）】：
  {menu_text}

  【預期輸出的 JSON 結構範例】：
  {{
    "招牌": "推薦理由...",
    "純茶": "推薦理由...",
    "奶類": "推薦理由...",
    "新鮮": "推薦理由...",
    "熱飲": "推薦理由...",
    "內行": "推薦理由..."
  }}
  """


def fetch_ai_recommendation(shop_name, menu_text, request_type):
    """呼叫 Gemini 取得六類推薦，並以「店家 + 日期」快取當日結果。"""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cache = _ai_cache.get(shop_name)
    if cache and cache["date"] == today and request_type in cache["results"]:
        return (
            cache["results"][request_type]
            + "<br><br><span style='font-size:12px; color:#888;'>(💡 秒回！此為 AI 今日特製推薦)</span>"
        )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    payload = {
        "contents": [{"parts": [{"text": build_prompt(shop_name, menu_text)}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"temperature": 0.8},
    }

    try:
        # API Key 放在 header，避免出現在 URL 與日誌中
        resp = requests.post(
            url,
            json=payload,
            headers={"x-goog-api-key": GEMINI_API_KEY},
            timeout=120,
        )
        data = resp.json()

        candidates = data.get("candidates")
        if candidates:
            if candidates[0].get("finishReason") == "SAFETY":
                return "不好意思，這家店的菜單似乎觸發了 AI 的安全過濾機制。"

            text = candidates[0]["content"]["parts"][0]["text"]
            clean = text.strip()
            clean = re.sub(r"^```(?:json)?", "", clean)
            clean = re.sub(r"```$", "", clean).strip()

            try:
                result_obj = json.loads(clean)
            except ValueError:
                return "【AI 格式化失敗，它講了太多廢話】：<br>" + text

            _ai_cache[shop_name] = {"date": today, "results": result_obj}
            return result_obj.get(request_type)
        if "error" in data:
            return "【API 錯誤抓到了】：" + data["error"].get("message", "")
        return "【預期外的回傳格式】：" + json.dumps(data, ensure_ascii=False)
    except Exception as e:
        return f"連線到 AI 時發生小意外：{e}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
