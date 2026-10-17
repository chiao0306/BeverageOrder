"""飲料訂購後端：由 Google Apps Script (code.gs) 改寫，供 Cloud Run 部署使用。"""
import json
import os

import gspread
from dotenv import load_dotenv
from flask import Flask, Response, request
from gspread.utils import ValueRenderOption

# 本機開發時讀取 .env；Cloud Run 上則直接使用環境變數
load_dotenv()

SHEET_URL = os.environ.get("SHEET_URL", "")
# 服務帳戶金鑰檔路徑；未設定時使用 Cloud Run 的預設服務帳戶 (ADC)
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

app = Flask(__name__)

_gc = None


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
