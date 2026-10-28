"""飲料訂購後端：由 Google Apps Script (code.gs) 改寫，供 Cloud Run 部署使用。"""
import json
import os
import re

import gspread
import requests
from dotenv import load_dotenv
from flask import Flask, Response, request
from gspread.utils import ValueRenderOption

# 本機開發時讀取 .env；Cloud Run 上則直接使用環境變數
load_dotenv()

SHEET_URL = os.environ.get("SHEET_URL", "")
# 服務帳戶金鑰檔路徑；未設定時使用 Cloud Run 的預設服務帳戶 (ADC)
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

# ==========================================
# Vapi.ai + Twilio 語音訂飲料設定（環境變數）
# ==========================================
VAPI_API_KEY = os.environ.get("VAPI_API_KEY", "").strip()
# 在 Vapi 後台綁定 Twilio 號碼後取得的 Phone Number ID
VAPI_PHONE_NUMBER_ID = os.environ.get("VAPI_PHONE_NUMBER_ID", "").strip()
# 選填：若已在 Vapi 後台建立固定 Assistant 可填入，否則由程式動態產生台灣手搖飲專用設定
VAPI_ASSISTANT_ID = os.environ.get("VAPI_ASSISTANT_ID", "").strip()
# 預設使用 Azure 台灣繁體中文女聲（曉臻），自然親切不機械
VAPI_VOICE_PROVIDER = os.environ.get("VAPI_VOICE_PROVIDER", "azure").strip()
VAPI_VOICE_ID = os.environ.get("VAPI_VOICE_ID", "zh-TW-HsiaoChenNeural").strip()
VAPI_MODEL_PROVIDER = os.environ.get("VAPI_MODEL_PROVIDER", "openai").strip()
VAPI_MODEL_NAME = os.environ.get("VAPI_MODEL_NAME", "gpt-4o-mini").strip()

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
        elif action == "getAiCallStatus":
            call_id = request.args.get("callId", "").strip()
            result["data"] = get_ai_call_status(call_id)
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
        elif action == "startAiPhoneCall":
            result["data"] = start_ai_phone_call(payload.get("callData", {}))
        elif action == "getAiCallStatus":
            result["data"] = get_ai_call_status(payload.get("callId", ""))
        else:
            result["error"] = f"未知的 POST 請求: {action}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return json_response(result)


# ==========================================
# 核心邏輯：試算表讀寫
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


# ==========================================
# 核心邏輯：Vapi.ai + Twilio 自動打電話訂飲料
# ==========================================
def format_tw_phone_e164(phone_str: str) -> str:
    """將台灣市話或手機號碼（如 07-8012345、0912-345-678）轉為 E.164 格式 (+886...)。"""
    raw = (phone_str or "").strip()
    if raw.startswith("+"):
        digits = "+" + re.sub(r"\D", "", raw[1:])
        return digits
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError("電話號碼格式無效")
    if digits.startswith("886"):
        return "+" + digits
    if digits.startswith("0"):
        return "+886" + digits[1:]
    return "+886" + digits


def format_spoken_phone(phone_str: str) -> str:
    """將電話號碼轉為適合中文 TTS 清楚唸出的斷句格式（例：0912-345-678）。"""
    digits = re.sub(r"\D", "", phone_str or "")
    if len(digits) == 10 and digits.startswith("09"):
        return f"{digits[:4]}，{digits[4:7]}，{digits[7:]}"
    return phone_str or ""


def build_beverage_system_prompt(call_data: dict) -> str:
    """組合專門針對台灣實體手搖飲店電話點餐的 AI System Prompt。"""
    shop_name = call_data.get("shopName", "飲料店")
    caller_name = call_data.get("callerName", "陳先生")
    caller_phone = call_data.get("callerPhone", "")
    spoken_phone = format_spoken_phone(caller_phone)
    total_cups = call_data.get("totalCups", 0)
    estimated_price = call_data.get("estimatedPrice", 0)
    spoken_lines = call_data.get("spokenLines", [])
    reqs = call_data.get("requirements", {}) or {}

    ask_price = reqs.get("askPrice", True)
    need_bag = reqs.get("needBag", True)
    ask_pickup_time = reqs.get("askPickupTime", True)
    custom_notes = (reqs.get("customNotes") or "").strip()

    drinks_block = "\n".join(spoken_lines) if spoken_lines else "（無品項）"

    tasks = []
    if need_bag:
        tasks.append("- 主動跟店員說「我們需要買塑膠袋幫我們裝起來」。")
    if ask_price:
        tasks.append(
            f"- 詢問店員「請問這樣加上袋子總共多少錢？」（我們試算表不含袋預估是 {estimated_price} 元，若店員報價差很多可禮貌確認一下）。"
        )
    if ask_pickup_time:
        tasks.append("- 詢問店員「請問大概幾點可以過去拿飲料？」或「大概幾分鐘後好？」。")
    if custom_notes:
        tasks.append(f"- 轉達額外需求：「{custom_notes}」。")
    tasks.append(
        f"- 若店員詢問貴姓或電話，請回答：「我姓{caller_name[0] if caller_name else '陳'}（{caller_name}），電話是 {spoken_phone}」。"
    )

    tasks_block = "\n".join(tasks)

    return f"""你是一位禮貌、自然、講話道地台灣口音的辦公室助理，正在打電話給台灣實體手搖飲店「{shop_name}」幫同事訂飲料自取。

【極重要通話守則（必讀）】
1. 講話要簡短、自然、像真人台灣顧客，不要像機器人背稿，語氣客氣（常用「你好」、「好，謝謝」、「對」）。
2. 店員需要時間按 POS 收銀機！報飲料時，**一次只唸 1 到 2 個品項**，唸完後稍微停頓，等店員回應「好」、「嗯」、「還有嗎」之後，再唸下一個品項！千萬不要一口氣把所有飲料連珠炮唸完。
3. 如果某個品項或配料賣完了，請先問店員有什麼推薦替代的，或者改選同價位的相近配料（例如波霸賣完改珍珠），並記下變更。
4. 全部共 {total_cups} 杯飲料唸完後，請務必完成以下事項：
{tasks_block}
5. 當確認完「總金額」與「幾點可以拿」且留完電話後，禮貌說聲「好，那我們待會過去拿，謝謝你，拜拜！」即可結束通話。

【今日要訂購的飲料清單（總共 {total_cups} 杯）】
{drinks_block}
"""


def start_ai_phone_call(call_data: dict) -> dict:
    """透過 Vapi.ai (+ Twilio) 發起外撥電話給飲料店。"""
    if not VAPI_API_KEY or not VAPI_PHONE_NUMBER_ID:
        raise RuntimeError(
            "後端尚未設定 VAPI_API_KEY 或 VAPI_PHONE_NUMBER_ID 環境變數。"
        )

    shop_name = (call_data.get("shopName") or "飲料店").strip()
    shop_phone_raw = (call_data.get("shopPhone") or "").strip()
    if not shop_phone_raw:
        raise ValueError("請提供店家電話號碼")

    e164_shop_phone = format_tw_phone_e164(shop_phone_raw)
    total_cups = call_data.get("totalCups", 0)
    system_prompt = build_beverage_system_prompt(call_data)

    first_message = (
        f"你好，請問是{shop_name}嗎？我要電話訂飲料自取，總共 {total_cups} 杯，請問現在方便幫我點餐嗎？"
    )

    vapi_payload = {
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "customer": {
            "number": e164_shop_phone,
            "name": shop_name,
        },
        "assistant": {
            "name": "手銲股訂飲料助理",
            "firstMessage": first_message,
            "transcriber": {
                "provider": "deepgram",
                "model": "nova-2",
                "language": "zh-TW",
            },
            "model": {
                "provider": VAPI_MODEL_PROVIDER,
                "model": VAPI_MODEL_NAME,
                "temperature": 0.4,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
                    }
                ],
            },
            "voice": {
                "provider": VAPI_VOICE_PROVIDER,
                "voiceId": VAPI_VOICE_ID,
            },
            "silenceTimeoutSeconds": 30,
            "maxDurationSeconds": 300,
            "endCallPhrases": ["拜拜", "再見", "掰掰"],
            "analysisPlan": {
                "summaryPrompt": "請用繁體中文簡短摘要這通訂飲料電話的結果（包含是否成功、總金額、預計取餐時間、是否有買塑膠袋、任何品項變更）。",
                "structuredDataPrompt": "請從對話中擷取店家告知的訂飲料結果資訊（請使用繁體中文）：",
                "structuredDataSchema": {
                    "type": "object",
                    "properties": {
                        "isSuccess": {
                            "type": "boolean",
                            "description": "是否成功完成訂購",
                        },
                        "totalAmount": {
                            "type": "string",
                            "description": "店員告知的最終總金額（例如：$522 含塑膠袋）",
                        },
                        "pickupTime": {
                            "type": "string",
                            "description": "店員告知幾點可以拿或幾分鐘後好（例如：14:30 或 20分鐘後）",
                        },
                        "bagInfo": {
                            "type": "string",
                            "description": "塑膠袋確認狀態與費用（例如：已加購塑膠袋 $2）",
                        },
                        "orderNotes": {
                            "type": "string",
                            "description": "取餐留的電話/稱呼、取餐號碼或任何缺貨更換備註",
                        },
                    },
                },
            },
        },
    }

    # 若使用者有指定固定的 VAPI_ASSISTANT_ID，也可以改用 assistantId + assistantOverrides
    if VAPI_ASSISTANT_ID:
        vapi_payload["assistantId"] = VAPI_ASSISTANT_ID
        vapi_payload["assistantOverrides"] = vapi_payload.pop("assistant")

    resp = requests.post(
        "https://api.vapi.ai/call",
        headers={
            "Authorization": f"Bearer {VAPI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=vapi_payload,
        timeout=20,
    )

    if resp.status_code >= 400:
        raise RuntimeError(f"Vapi 撥號失敗 ({resp.status_code}): {resp.text}")

    data = resp.json()
    return {
        "callId": data.get("id"),
        "status": data.get("status", "queued"),
        "e164ShopPhone": e164_shop_phone,
        "message": f"已向 {shop_name} ({e164_shop_phone}) 發起 AI 語音撥號！",
    }


def get_ai_call_status(call_id: str) -> dict:
    """向 Vapi 查詢通話狀態、即時/完整逐字稿與通話後結構化取餐資訊。"""
    if not VAPI_API_KEY:
        raise RuntimeError("後端尚未設定 VAPI_API_KEY 環境變數。")
    if not call_id:
        raise ValueError("缺少 callId 參數")

    resp = requests.get(
        f"https://api.vapi.ai/call/{call_id}",
        headers={"Authorization": f"Bearer {VAPI_API_KEY}"},
        timeout=15,
    )

    if resp.status_code >= 400:
        raise RuntimeError(f"查詢通話狀態失敗 ({resp.status_code}): {resp.text}")

    data = resp.json()
    analysis = data.get("analysis") or {}
    structured = analysis.get("structuredData") or {}

    return {
        "callId": data.get("id"),
        "status": data.get("status"),  # queued, ringing, in-progress, forwarding, ended
        "endedReason": data.get("endedReason"),
        "transcript": data.get("transcript") or "",
        "summary": analysis.get("summary") or "",
        "recordingUrl": data.get("recordingUrl") or "",
        "pickupInfo": {
            "isSuccess": structured.get("isSuccess"),
            "totalAmount": structured.get("totalAmount") or "",
            "pickupTime": structured.get("pickupTime") or "",
            "bagInfo": structured.get("bagInfo") or "",
            "orderNotes": structured.get("orderNotes") or "",
        },
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
