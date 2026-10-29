"""飲料訂購後端：Cloud Run (Flask + WebSocket) 串接 Google Sheets、Twilio Voice 與 Gemini Live API。"""
import asyncio
import array
import base64
import json
import os
import re
import struct
import threading
import time
import uuid
from typing import Optional

import gspread
import requests
from dotenv import load_dotenv
from flask import Flask, Response, request
from flask_sock import Sock
from google import genai
from google.genai import types
from gspread.utils import ValueRenderOption
from pydantic import BaseModel, Field

# 本機開發時讀取 .env；Cloud Run 上則直接使用環境變數
load_dotenv()

SHEET_URL = os.environ.get("SHEET_URL", "")
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

# ==========================================
# 路線二：Twilio + Google Gemini Live API 設定
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live").strip()
GEMINI_SUMMARY_MODEL = os.environ.get("GEMINI_SUMMARY_MODEL", "gemini-3.8-flash").strip()
# 可選語音名稱：Puck, Charon, Kore, Fenrir, Aoede（預設 Aoede 自然女聲）
GEMINI_VOICE_NAME = os.environ.get("GEMINI_VOICE_NAME", "Aoede").strip()

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
# Twilio 購買或試用配發的發話號碼（如 +1234567890），若填入已在 Twilio 驗證過的台灣手機亦可作為 Caller ID
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
# 選填：Cloud Run 公開網址（如 https://beverageorder-xxx.asia-east1.run.app），未填則自動由 request.host 推斷
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

app = Flask(__name__)
sock = Sock(app)

_gc = None

# 儲存進行中與已完成的 AI 通話狀態（Cloud Run 單一容器多執行緒共用）
ACTIVE_CALLS: dict[str, dict] = {}


# ==========================================
# 純 Python 音訊轉碼器：Twilio 8kHz G.711 μ-law <-> Gemini 16kHz/24kHz PCM16
# （相容 Python 3.12 ~ 3.14+，不依賴已移除的 audioop 模組）
# ==========================================
def _build_ulaw_tables():
    """建立 8-bit μ-law 與 16-bit Signed Linear PCM 的高速查表。"""
    ulaw_to_pcm = [0] * 256
    for i in range(256):
        u = ~i & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        ulaw_to_pcm[i] = -sample if sign else sample

    # 將 -32768..32767 右移 2 位元映射到 0..16383 的查表
    pcm14_to_ulaw = bytearray(16384)
    BIAS = 0x84
    CLIP = 32635
    for idx in range(16384):
        pcm_val = (idx - 8192) << 2
        sign = 0x80 if pcm_val < 0 else 0x00
        if pcm_val < 0:
            pcm_val = -pcm_val
        if pcm_val > CLIP:
            pcm_val = CLIP
        pcm_val += BIAS
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not (pcm_val & mask):
            exponent -= 1
            mask >>= 1
        mantissa = (pcm_val >> (exponent + 3)) & 0x0F
        ulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
        pcm14_to_ulaw[idx] = ulaw_byte

    return ulaw_to_pcm, bytes(pcm14_to_ulaw)


_ULAW_TO_PCM16, _PCM14_TO_ULAW = _build_ulaw_tables()


def ulaw_8k_to_pcm16_16k(ulaw_bytes: bytes) -> bytes:
    """將 Twilio 傳來的 8kHz μ-law 音訊轉為 Gemini Live 需要的 16kHz 16-bit PCM (2倍升頻)。"""
    n = len(ulaw_bytes)
    out = bytearray(n * 4)
    pos = 0
    for b in ulaw_bytes:
        s = _ULAW_TO_PCM16[b]
        lo = s & 0xFF
        hi = (s >> 8) & 0xFF
        # 8kHz -> 16kHz：每個樣本重複 2 次 (4 bytes)
        out[pos] = lo
        out[pos + 1] = hi
        out[pos + 2] = lo
        out[pos + 3] = hi
        pos += 4
    return bytes(out)


def pcm16_24k_to_ulaw_8k(pcm24k_bytes: bytes) -> bytes:
    """將 Gemini Live 輸出的 24kHz 16-bit PCM 降頻為 Twilio 需要的 8kHz μ-law (每 3 個樣本取 1)。"""
    # 每個 16-bit 樣本佔 2 bytes；24kHz -> 8kHz 每 6 bytes 取 1 個樣本
    total_samples_8k = len(pcm24k_bytes) // 6
    if total_samples_8k <= 0:
        return b""
    out = bytearray(total_samples_8k)
    for i in range(total_samples_8k):
        offset = i * 6
        sample = struct.unpack_from("<h", pcm24k_bytes, offset)[0]
        idx = (sample >> 2) + 8192
        if idx < 0:
            idx = 0
        elif idx > 16383:
            idx = 16383
        out[i] = _PCM14_TO_ULAW[idx]
    return bytes(out)


# ==========================================
# Google Sheets 工具函數
# ==========================================
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
# 核心邏輯：Twilio + Gemini Live API 語音訂飲料
# ==========================================
def format_tw_phone_e164(phone_str: str) -> str:
    """將台灣市話或手機號碼（如 07-8012345、0912-345-678）轉為 E.164 格式 (+886...)。"""
    raw = (phone_str or "").strip()
    if raw.startswith("+"):
        return "+" + re.sub(r"\D", "", raw[1:])
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError("電話號碼格式無效")
    if digits.startswith("886"):
        return "+" + digits
    if digits.startswith("0"):
        return "+886" + digits[1:]
    return "+886" + digits


def format_spoken_phone(phone_str: str) -> str:
    """將電話號碼轉為適合中文語音清楚唸出的斷句格式（例：0912-345-678）。"""
    digits = re.sub(r"\D", "", phone_str or "")
    if len(digits) == 10 and digits.startswith("09"):
        return f"{digits[:4]}，{digits[4:7]}，{digits[7:]}"
    return phone_str or ""


def build_beverage_system_prompt(call_data: dict) -> str:
    """組合專門針對台灣實體手搖飲店電話點餐的 Gemini Live System Instruction。"""
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
        tasks.append("- 主動跟店員說：「我們需要買塑膠袋幫我們裝起來」。")
    if ask_price:
        tasks.append(
            f"- 詢問店員：「請問這樣加上袋子總共多少錢？」（我們試算表不含袋預估是 {estimated_price} 元）。"
        )
    if ask_pickup_time:
        tasks.append("- 詢問店員：「請問大概幾點可以過去拿飲料？」或「大概幾分鐘後好？」。")
    if custom_notes:
        tasks.append(f"- 轉達額外需求：「{custom_notes}」。")
    tasks.append(
        f"- 主動或於店員詢問時留下取餐資料：「我姓{caller_name[0] if caller_name else '陳'}（{caller_name}），電話是 {spoken_phone}」。"
    )

    tasks_block = "\n".join(tasks)

    return f"""你是一位禮貌、自然、講話道地台灣口音的辦公室助理，正在打電話給台灣實體手搖飲店「{shop_name}」幫同事訂飲料自取。

【極重要通話守則（必讀）】
1. 請全程使用繁體中文（台灣口語習慣，例如：微糖微冰、大杯、好謝謝、對）對話，語氣自然親切，每次說話簡短扼要。
2. 電話剛接通或店員說「喂你好」時，請先打招呼並直接開始點餐：「你好，請問是{shop_name}嗎？我要電話訂飲料自取，總共 {total_cups} 杯，我先跟你報第一杯。」說完後立刻唸出清單第 1 個品項，然後停下來等店員回應。
   - 店員回「好」、「OK」、「請說」、「嗯」、「可以」、「繼續」等任何簡短回應，都代表已記下上一個品項，你必須**立刻唸下一個品項**，絕對不可以再問一次「方便嗎」，也不可以沉默等待。
   - 每一輪你說完話，如果還有品項沒唸完、或第 5 點的事項還沒完成，下一輪就必須主動繼續，直到全部完成。
3. 店員需要時間按 POS 收銀機！報飲料時，**一次只唸 1 個品項（含杯數、大小、甜度、冰量、配料）**，唸完後務必停下來，等店員出聲回應（例如「好」、「嗯」、「還有嗎」）後，才唸下一個品項！絕對不要連續唸多個品項。若店員沒有回應，系統會提醒你，屆時請簡短詢問店員並重複剛才的品項。
4. 如果某個品項或配料賣完了，請先問店員有什麼推薦替代的，或改選同價位的相近配料（例如波霸賣完改珍珠）。
5. 全部共 {total_cups} 杯飲料唸完後，請務必完成以下事項：
{tasks_block}
6. 當確認完「總金額」與「幾點可以拿」且留完電話後，禮貌說聲「好，那我們待會過去拿，謝謝你，拜拜！」即可結束對話。

【今日要訂購的飲料清單（總共 {total_cups} 杯）】
{drinks_block}
"""


def _get_base_url() -> str:
    """取得目前 Cloud Run 服務的外部 HTTPS 網址，用於產生 Twilio Webhook 與 WSS 網址。"""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    proto = request.headers.get("X-Forwarded-Proto", request.scheme or "https")
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}"


def start_ai_phone_call(call_data: dict) -> dict:
    """使用 Twilio Programmable Voice 發起外撥電話，並將音訊串流導向本服務的 Gemini Live WebSocket。"""
    if not GEMINI_API_KEY:
        raise RuntimeError("後端尚未設定 GEMINI_API_KEY 環境變數。")
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        raise RuntimeError(
            "後端尚未設定 TWILIO_ACCOUNT_SID 或 TWILIO_AUTH_TOKEN 環境變數。"
        )

    shop_name = (call_data.get("shopName") or "飲料店").strip()
    shop_phone_raw = (call_data.get("shopPhone") or "").strip()
    caller_phone_raw = (call_data.get("callerPhone") or "").strip()
    if not shop_phone_raw:
        raise ValueError("請提供店家電話號碼")

    e164_shop_phone = format_tw_phone_e164(shop_phone_raw)
    from_phone = TWILIO_FROM_NUMBER or (
        format_tw_phone_e164(caller_phone_raw) if caller_phone_raw else ""
    )
    if not from_phone:
        raise ValueError("請在頁面輸入發話電話，或於後端設定 TWILIO_FROM_NUMBER")
    system_prompt = build_beverage_system_prompt(call_data)

    call_id = str(uuid.uuid4())
    base_url = _get_base_url()
    ws_scheme = "wss" if base_url.startswith("https") else "ws"
    host_part = re.sub(r"^https?://", "", base_url)
    stream_ws_url = f"{ws_scheme}://{host_part}/twilio-media-stream/{call_id}"
    status_callback_url = f"{base_url}/twilio-status-callback/{call_id}"

    ACTIVE_CALLS[call_id] = {
        "callId": call_id,
        "twilioCallSid": "",
        "shopName": shop_name,
        "e164ShopPhone": e164_shop_phone,
        "callData": call_data,
        "systemPrompt": system_prompt,
        "status": "queued",
        "endedReason": "",
        "transcriptLines": [],
        "summary": "",
        "pickupInfo": {
            "isSuccess": False,
            "totalAmount": "",
            "pickupTime": "",
            "bagInfo": "",
            "orderNotes": "",
        },
        "extractionDone": False,
        "createdAt": time.time(),
    }

    # TwiML：電話一接通立即建立雙向 Media Stream 連回 Cloud Run 的 /twilio-media-stream/<call_id>
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_ws_url}" />
    </Connect>
</Response>"""
    ACTIVE_CALLS[call_id]["twiml"] = twiml
    twiml_url = f"{base_url}/twilio-twiml/{call_id}"

    # 注意：Twilio Trial 免費試用帳號不允許傳送 inline Twiml 與 StatusCallbackEvent 參數，
    # 必須改用 Url 指向本服務的 /twilio-twiml/<call_id> 端點，並僅傳送 StatusCallback
    twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json"
    resp = requests.post(
        twilio_url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={
            "To": e164_shop_phone,
            "From": from_phone,
            "Url": twiml_url,
            "StatusCallback": status_callback_url,
        },
        timeout=20,
    )

    if resp.status_code >= 400:
        ACTIVE_CALLS.pop(call_id, None)
        raise RuntimeError(f"Twilio 撥號失敗 ({resp.status_code}): {resp.text}")

    twilio_data = resp.json()
    ACTIVE_CALLS[call_id]["twilioCallSid"] = twilio_data.get("sid", "")
    ACTIVE_CALLS[call_id]["status"] = "ringing"

    return {
        "callId": call_id,
        "twilioCallSid": twilio_data.get("sid", ""),
        "status": "ringing",
        "e164ShopPhone": e164_shop_phone,
        "message": f"已透過 Twilio + Gemini Live ({GEMINI_LIVE_MODEL}) 撥號至 {shop_name} ({e164_shop_phone})！",
    }


@app.route("/twilio-twiml/<call_id>", methods=["GET", "POST"])
def twilio_twiml(call_id: str):
    """回傳 TwiML XML 給 Twilio，指示接通後建立雙向 WebSocket Media Stream。"""
    call_state = ACTIVE_CALLS.get(call_id)
    if not call_state:
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
            mimetype="text/xml",
        )
    call_state["status"] = "in-progress"
    return Response(call_state["twiml"], mimetype="text/xml")


@app.route("/twilio-status-callback/<call_id>", methods=["POST"])
def twilio_status_callback(call_id: str):
    """接收 Twilio 的通話狀態變更通知（ringing / in-progress / completed 等）。"""
    call_state = ACTIVE_CALLS.get(call_id)
    if not call_state:
        return ("", 204)

    twilio_status = request.form.get("CallStatus", "")
    if twilio_status in ("queued", "initiated"):
        call_state["status"] = "queued"
    elif twilio_status == "ringing":
        call_state["status"] = "ringing"
    elif twilio_status == "in-progress":
        call_state["status"] = "in-progress"
    elif twilio_status in ("completed", "busy", "failed", "no-answer", "canceled"):
        call_state["status"] = "ended"
        call_state["endedReason"] = twilio_status
        if not call_state.get("extractionDone"):
            threading.Thread(
                target=extract_pickup_info_with_gemini,
                args=(call_id,),
                daemon=True,
            ).start()

    return ("", 204)


def _build_live_config(system_prompt: str) -> types.LiveConnectConfig:
    """建立 Gemini Live 連線設定（降低對環境雜音/回音的誤打斷敏感度）。"""
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=GEMINI_VOICE_NAME
                )
            )
        ),
        system_instruction=types.Content(parts=[types.Part(text=system_prompt)]),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                silence_duration_ms=600,
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


SILENCE_NUDGE_SECONDS = 5.0
MAX_CONSECUTIVE_NUDGES = 3
SILENCE_NUDGE_TEXT = (
    "（系統提示：店員已經超過 5 秒沒有出聲回應。請用一句簡短的台灣口語禮貌詢問店員，"
    "例如「不好意思，請問聽得到嗎？」，並簡短重複你剛剛最後唸的那個品項，然後等店員回應。）"
)


class SilenceWatcher:
    """追蹤 AI 說完話後店員是否超過 5 秒沒出聲；若是，提醒 AI 再次詢問店員。"""

    def __init__(self):
        self.play_end = 0.0  # AI 語音預計播放完畢的時間點
        self.last_voice = 0.0  # 最近一次偵測到店員聲音
        self.waiting = False  # AI 已說完一輪、正在等店員回應
        self.nudges = 0

    def on_audio_out(self, nbytes: int, sample_rate: int = 24000):
        now = time.time()
        self.play_end = max(now, self.play_end) + nbytes / 2 / sample_rate

    def on_turn_complete(self):
        self.waiting = True

    def on_clerk_chunk(self, pcm16: bytes):
        samples = array.array("h")
        samples.frombytes(pcm16[: len(pcm16) // 2 * 2])
        if samples and max(abs(s) for s in samples[::8]) > 1200:
            self.last_voice = time.time()
            self.waiting = False
            self.nudges = 0

    def due(self) -> bool:
        if not self.waiting or self.nudges >= MAX_CONSECUTIVE_NUDGES:
            return False
        return time.time() - max(self.play_end, self.last_voice) > SILENCE_NUDGE_SECONDS

    def mark_nudged(self):
        self.waiting = False
        self.nudges += 1


async def _silence_watch_loop(session, watcher: SilenceWatcher, stop_event: asyncio.Event):
    while not stop_event.is_set():
        await asyncio.sleep(0.5)
        if watcher.due():
            watcher.mark_nudged()
            try:
                await session.send_realtime_input(text=SILENCE_NUDGE_TEXT)
            except Exception as e:
                print(f"[Silence Nudge Error]: {e}")
                return


@sock.route("/twilio-media-stream/<call_id>")
def twilio_media_stream(ws, call_id: str):
    """處理 Twilio 雙向音訊 WebSocket 並橋接至 Google Gemini Live API (gemini-3.8-live)。"""
    call_state = ACTIVE_CALLS.get(call_id)
    if not call_state:
        ws.close()
        return

    call_state["status"] = "in-progress"
    asyncio.run(_bridge_twilio_and_gemini_live(ws, call_state))

    # WebSocket 結束後標記為 ended 並啟動 Gemini 結構化摘要擷取
    call_state["status"] = "ended"
    if not call_state.get("extractionDone"):
        threading.Thread(
            target=extract_pickup_info_with_gemini,
            args=(call_id,),
            daemon=True,
        ).start()


async def _bridge_twilio_and_gemini_live(ws, call_state: dict):
    """在非同步事件迴圈中雙向轉發 Twilio WebSocket 與 Gemini Live Session。"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    config = _build_live_config(call_state["systemPrompt"])

    stream_sid: Optional[str] = None
    stop_event = asyncio.Event()
    watcher = SilenceWatcher()
    loop = asyncio.get_running_loop()

    async with client.aio.live.connect(model=GEMINI_LIVE_MODEL, config=config) as session:
        # 1. 背景任務：接收來自 Gemini Live 的語音與逐字稿，轉成 8kHz μ-law 送回 Twilio
        async def receive_from_gemini():
            nonlocal stream_sid
            ai_text_buffer = ""
            user_text_buffer = ""
            try:
                async for response in session.receive():
                    if stop_event.is_set():
                        break
                    content = response.server_content
                    if not content:
                        continue

                    # 處理語音輸出 (24kHz PCM -> 8kHz μ-law)
                    if content.model_turn:
                        for part in content.model_turn.parts:
                            if part.inline_data and part.inline_data.data and stream_sid:
                                watcher.on_audio_out(len(part.inline_data.data))
                                ulaw_bytes = pcm16_24k_to_ulaw_8k(part.inline_data.data)
                                if ulaw_bytes:
                                    b64_payload = base64.b64encode(ulaw_bytes).decode("ascii")
                                    ws.send(
                                        json.dumps(
                                            {
                                                "event": "media",
                                                "streamSid": stream_sid,
                                                "media": {"payload": b64_payload},
                                            }
                                        )
                                    )

                    # 處理店員插話打斷 (Barge-in)：立即清空 Twilio 播放佇列
                    if content.interrupted is True and stream_sid:
                        ws.send(json.dumps({"event": "clear", "streamSid": stream_sid}))

                    # 收集即時逐字稿
                    if content.input_transcription and content.input_transcription.text:
                        user_text_buffer += content.input_transcription.text
                        if user_text_buffer.endswith(("。", "？", "！", "\n")) or len(user_text_buffer) > 25:
                            call_state["transcriptLines"].append(f"👩‍🍳 店員：{user_text_buffer.strip()}")
                            user_text_buffer = ""

                    if content.output_transcription and content.output_transcription.text:
                        ai_text_buffer += content.output_transcription.text
                        if ai_text_buffer.endswith(("。", "？", "！", "\n")) or len(ai_text_buffer) > 30:
                            call_state["transcriptLines"].append(f"🤖 AI：{ai_text_buffer.strip()}")
                            ai_text_buffer = ""

                    if content.turn_complete:
                        watcher.on_turn_complete()
                        if user_text_buffer.strip():
                            call_state["transcriptLines"].append(f"👩‍🍳 店員：{user_text_buffer.strip()}")
                            user_text_buffer = ""
                        if ai_text_buffer.strip():
                            call_state["transcriptLines"].append(f"🤖 AI：{ai_text_buffer.strip()}")
                            ai_text_buffer = ""
            except Exception as e:
                print(f"[Gemini Live Receive Loop Closed]: {e}")
            finally:
                if user_text_buffer.strip():
                    call_state["transcriptLines"].append(f"👩‍🍳 店員：{user_text_buffer.strip()}")
                if ai_text_buffer.strip():
                    call_state["transcriptLines"].append(f"🤖 AI：{ai_text_buffer.strip()}")
                stop_event.set()

        # 2. 背景任務：從 Twilio WebSocket 讀取音訊事件 (8kHz μ-law -> 16kHz PCM) 送進 Gemini Live
        async def receive_from_twilio():
            nonlocal stream_sid
            try:
                while not stop_event.is_set():
                    raw_msg = await loop.run_in_executor(None, ws.receive)
                    if raw_msg is None:
                        break
                    msg = json.loads(raw_msg)
                    event_type = msg.get("event")

                    if event_type == "start":
                        stream_sid = msg.get("start", {}).get("streamSid")
                        # 電話接通後，觸發 AI 主動開口跟店員打招呼
                        await session.send_realtime_input(
                            text="（電話已接通，請立刻主動向店員打招呼，說明要訂飲料自取，並開始唸第一個品項）"
                        )
                    elif event_type == "media":
                        b64_audio = msg.get("media", {}).get("payload")
                        if b64_audio:
                            ulaw_bytes = base64.b64decode(b64_audio)
                            pcm16k_bytes = ulaw_8k_to_pcm16_16k(ulaw_bytes)
                            watcher.on_clerk_chunk(pcm16k_bytes)
                            await session.send_realtime_input(
                                audio=types.Blob(
                                    data=pcm16k_bytes,
                                    mime_type="audio/pcm;rate=16000",
                                )
                            )
                    elif event_type == "stop":
                        break
            except Exception as e:
                print(f"[Twilio WebSocket Receive Loop Closed]: {e}")
            finally:
                stop_event.set()

        await asyncio.gather(
            receive_from_gemini(),
            receive_from_twilio(),
            _silence_watch_loop(session, watcher, stop_event),
        )


# ==========================================
# 通話結束後：使用 Gemini 3.8 Flash 擷取結構化取餐資訊
# ==========================================
class BeveragePickupExtraction(BaseModel):
    isSuccess: bool = Field(description="是否成功完成訂購")
    summary: str = Field(description="用繁體中文簡短摘要這通訂飲料電話的結果")
    totalAmount: str = Field(description="店員告知的最終總金額（例如：$522 含塑膠袋）")
    pickupTime: str = Field(description="店員告知幾點可以拿或幾分鐘後好（例如：14:30 或 20分鐘後）")
    bagInfo: str = Field(description="塑膠袋確認狀態與費用（例如：已加購塑膠袋 $2）")
    orderNotes: str = Field(description="取餐留的電話/稱呼、取餐號碼或任何缺貨更換備註")


def extract_pickup_info_with_gemini(call_id: str):
    """通話結束後，將對話逐字稿交由 gemini-3.8-flash 整理成結構化 JSON 取餐資訊。"""
    call_state = ACTIVE_CALLS.get(call_id)
    if not call_state or call_state.get("extractionDone"):
        return
    call_state["extractionDone"] = True

    transcript_text = "\n".join(call_state.get("transcriptLines") or [])
    if not transcript_text.strip():
        call_state["summary"] = "通話時間過短或無語音紀錄。"
        return

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""以下是我們 AI 助理打電話給飲料店「{call_state.get('shopName', '')}」訂飲料的完整通話逐字稿。
請根據對話內容，擷取出店員確認的「總金額」、「預計取餐時間」、「塑膠袋狀態」與「備註資訊」：

【通話逐字稿】
{transcript_text}
"""
        interaction = client.interactions.create(
            model=GEMINI_SUMMARY_MODEL,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": BeveragePickupExtraction.model_json_schema(),
            },
        )
        extracted = BeveragePickupExtraction.model_validate_json(interaction.output_text)
        call_state["summary"] = extracted.summary
        call_state["pickupInfo"] = {
            "isSuccess": extracted.isSuccess,
            "totalAmount": extracted.totalAmount,
            "pickupTime": extracted.pickupTime,
            "bagInfo": extracted.bagInfo,
            "orderNotes": extracted.orderNotes,
        }
    except Exception as e:
        print(f"[Post-call Gemini Extraction Error]: {e}")
        call_state["summary"] = f"自動摘要解析完成（{e}）"


# ==========================================
# 網頁語音實測：瀏覽器麥克風 <-> Cloud Run <-> Gemini Live（免 Twilio、免電話費）
# 使用者扮演店員，AI 依訂單腳本與你對話；結束後自動擷取取餐資訊。
# 協定（JSON 文字訊息）：
#   client -> server: {"type":"setup","callData":{...}} / {"type":"audio","data":<b64 PCM16 16k>} / {"type":"end"}
#   server -> client: {"type":"ready"} / {"type":"audio","data":<b64 PCM16 24k>} / {"type":"interrupted"}
#                     {"type":"transcript","lines":[...]} / {"type":"ended","summary":..,"pickupInfo":{..},"transcript":..}
# ==========================================
@sock.route("/voice-test-stream")
def voice_test_stream(ws):
    """瀏覽器語音實測 WebSocket 入口。"""
    try:
        first = ws.receive(timeout=15)
        msg = json.loads(first) if first else {}
    except Exception:
        ws.close()
        return
    if msg.get("type") != "setup":
        ws.close()
        return
    if not GEMINI_API_KEY:
        ws.send(json.dumps({"type": "error", "message": "後端尚未設定 GEMINI_API_KEY"}))
        ws.close()
        return

    call_data = msg.get("callData") or {}
    call_id = str(uuid.uuid4())
    call_state = {
        "callId": call_id,
        "shopName": (call_data.get("shopName") or "飲料店").strip(),
        "systemPrompt": build_beverage_system_prompt(call_data),
        "status": "in-progress",
        "transcriptLines": [],
        "summary": "",
        "pickupInfo": {},
        "extractionDone": False,
        "createdAt": time.time(),
    }
    ACTIVE_CALLS[call_id] = call_state

    try:
        asyncio.run(_bridge_browser_and_gemini_live(ws, call_state))
    except Exception as e:
        print(f"[Voice Test Error]: {type(e).__name__}: {e}")
        try:
            ws.send(json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"}))
        except Exception:
            pass

    call_state["status"] = "ended"
    extract_pickup_info_with_gemini(call_id)
    try:
        ws.send(
            json.dumps(
                {
                    "type": "ended",
                    "summary": call_state.get("summary", ""),
                    "pickupInfo": call_state.get("pickupInfo") or {},
                    "transcript": "\n".join(call_state.get("transcriptLines") or []),
                }
            )
        )
    except Exception:
        pass
    ACTIVE_CALLS.pop(call_id, None)


async def _bridge_browser_and_gemini_live(ws, call_state: dict):
    """雙向轉發瀏覽器麥克風音訊（16kHz PCM16）與 Gemini Live Session。"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    config = _build_live_config(call_state["systemPrompt"])

    stop_event = asyncio.Event()
    watcher = SilenceWatcher()
    loop = asyncio.get_running_loop()
    lines = call_state["transcriptLines"]

    async with client.aio.live.connect(model=GEMINI_LIVE_MODEL, config=config) as session:
        ws.send(json.dumps({"type": "ready"}))

        async def receive_from_gemini():
            buffers = {"user": "", "ai": ""}
            labels = {"user": "👩‍🍳 店員", "ai": "🤖 AI"}

            def flush():
                for who in ("user", "ai"):
                    text = buffers[who].strip()
                    if text:
                        lines.append(f"{labels[who]}：{text}")
                        buffers[who] = ""
                try:
                    ws.send(json.dumps({"type": "transcript", "lines": list(lines)}))
                except Exception:
                    pass

            try:
                async for response in session.receive():
                    if stop_event.is_set():
                        break
                    content = response.server_content
                    if not content:
                        continue
                    if content.model_turn:
                        for part in content.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                watcher.on_audio_out(len(part.inline_data.data))
                                ws.send(
                                    json.dumps(
                                        {
                                            "type": "audio",
                                            "data": base64.b64encode(
                                                part.inline_data.data
                                            ).decode("ascii"),
                                        }
                                    )
                                )
                    if content.interrupted is True:
                        ws.send(json.dumps({"type": "interrupted"}))
                    if content.input_transcription and content.input_transcription.text:
                        buffers["user"] += content.input_transcription.text
                    if content.output_transcription and content.output_transcription.text:
                        buffers["ai"] += content.output_transcription.text
                    if content.turn_complete:
                        watcher.on_turn_complete()
                    if content.turn_complete or content.interrupted is True:
                        flush()
            except Exception as e:
                print(f"[Voice Test Gemini Loop Closed]: {e}")
            finally:
                flush()
                stop_event.set()

        async def receive_from_browser():
            try:
                while not stop_event.is_set():
                    raw = await loop.run_in_executor(None, ws.receive)
                    if raw is None:
                        break
                    m = json.loads(raw)
                    t = m.get("type")
                    if t == "audio" and m.get("data"):
                        pcm_in = base64.b64decode(m["data"])
                        watcher.on_clerk_chunk(pcm_in)
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=pcm_in,
                                mime_type="audio/pcm;rate=16000",
                            )
                        )
                    elif t == "end":
                        break
            except Exception as e:
                print(f"[Voice Test Browser Loop Closed]: {e}")
            finally:
                stop_event.set()

        gemini_task = asyncio.ensure_future(receive_from_gemini())
        browser_task = asyncio.ensure_future(receive_from_browser())
        watch_task = asyncio.ensure_future(_silence_watch_loop(session, watcher, stop_event))
        await stop_event.wait()
        # 給 Gemini 迴圈片刻收尾；不等待 blocking 的 ws.receive 執行緒
        await asyncio.wait({gemini_task}, timeout=2)
        gemini_task.cancel()
        browser_task.cancel()
        watch_task.cancel()


def get_ai_call_status(call_id: str) -> dict:
    """回傳目前通話狀態、即時逐字稿與 Gemini 整理後的取餐資訊。"""
    if not call_id:
        raise ValueError("缺少 callId 參數")
    call_state = ACTIVE_CALLS.get(call_id)
    if not call_state:
        raise ValueError(f"找不到通話紀錄 ({call_id})")

    return {
        "callId": call_id,
        "status": call_state.get("status", "ended"),
        "endedReason": call_state.get("endedReason", ""),
        "transcript": "\n".join(call_state.get("transcriptLines") or []),
        "summary": call_state.get("summary", ""),
        "recordingUrl": "",
        "pickupInfo": call_state.get("pickupInfo") or {},
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
