"""以 Firestore 管理訂購資料與後台權限。"""
import hashlib
import math
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from google.cloud import firestore
from werkzeug.security import check_password_hash, generate_password_hash

TAIPEI = ZoneInfo("Asia/Taipei")
SESSION_HOURS = 12
LOCK_MINUTES = 15
MAX_FAILURES = 5
HEADER = ["姓名", "飲品", "容量", "甜度", "冰塊", "配料", "總金額", "差額"]
_client = None
_dummy_hash = generate_password_hash(secrets.token_urlsafe(24))


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def db():
    global _client
    if _client is None:
        _client = firestore.Client()
    return _client


def today():
    return datetime.now(TAIPEI).date().isoformat()


def now():
    return datetime.now(timezone.utc)


def key(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def text(value, label, limit=100):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ApiError(f"{label}不得為空，且長度不得超過 {limit} 字")
    return value.strip()


def username(value):
    value = text(value, "工號", 64)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ApiError("工號只能包含英文字母、數字、底線、點或連字號")
    return value


def shop_names():
    names = []
    for path in sorted((Path(__file__).parent / "menu").glob("shop_*.js")):
        found = re.findall(r'allShopsDatabase\s*\[\s*["\']([^"\']+)["\']\s*\]\s*=', path.read_text(encoding="utf-8"))
        if len(found) != 1:
            raise RuntimeError(f"無法辨識店家檔案：{path.name}")
        names.extend(found)
    if not names or len(set(names)) != len(names):
        raise RuntimeError("店家清單為空或包含重複店家")
    return names


def snapshot_data(snapshot):
    return snapshot.to_dict() if snapshot.exists else {}


def get_ref(transaction, ref):
    return ref.get(transaction=transaction)


def date_guard(date):
    if date != today():
        raise ApiError("日期已變更，請重新整理頁面", 409)


def account_ref(name):
    return db().collection("accounts").document(key(name))


def session_ref(token):
    return db().collection("sessions").document(key(token))


def authenticate(transaction, token, admin=False):
    if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
        raise ApiError("請先登入", 401)
    session = snapshot_data(get_ref(transaction, session_ref(token)))
    if not session or session.get("expiresAt") <= now():
        raise ApiError("登入已失效，請重新登入", 401)
    account = snapshot_data(get_ref(transaction, account_ref(session["username"])))
    if not account or not account.get("enabled") or account.get("sessionVersion") != session.get("sessionVersion"):
        raise ApiError("登入已失效，請重新登入", 401)
    if admin and account.get("role") != "admin":
        raise ApiError("沒有主管理員權限", 403)
    return account


def require_shop_access(transaction, token, date):
    account = authenticate(transaction, token)
    daily = snapshot_data(get_ref(transaction, db().collection("days").document(date)))
    if account["role"] != "admin" and daily.get("assignee") != account["username"]:
        raise ApiError("您不是今日被指派的人員", 403)
    return account, daily


def run_transaction(callback):
    return firestore.transactional(callback)(db().transaction())


def audit(transaction, actor, action, details=None):
    transaction.create(db().collection("audit").document(uuid.uuid4().hex), {
        "actor": actor, "action": action, "details": details or {}, "createdAt": now()
    })


def login(name, password):
    name = username(name)
    if not isinstance(password, str) or len(password) > 256:
        raise ApiError("帳號或密碼錯誤", 401)
    token = secrets.token_urlsafe(32)
    account_doc = account_ref(name)
    throttle = db().collection("login_limits").document(key(name))

    def perform(transaction):
        account = snapshot_data(get_ref(transaction, account_doc))
        limit = snapshot_data(get_ref(transaction, throttle))
        current = now()
        locked_until = limit.get("lockedUntil")
        if locked_until and locked_until > current:
            return {"locked": True}
        valid = check_password_hash(account.get("passwordHash", _dummy_hash), password)
        if not valid or not account.get("enabled"):
            failures = limit.get("failures", 0) + 1
            transaction.set(throttle, {
                "failures": 0 if failures >= MAX_FAILURES else failures,
                "lockedUntil": current + timedelta(minutes=LOCK_MINUTES) if failures >= MAX_FAILURES else None,
            })
            return {"failed": True}
        transaction.set(throttle, {"failures": 0, "lockedUntil": None})
        transaction.create(session_ref(token), {
            "username": name,
            "sessionVersion": account.get("sessionVersion", 0),
            "expiresAt": current + timedelta(hours=SESSION_HOURS),
        })
        audit(transaction, name, "login")
        return {"token": token, "username": name, "role": account["role"]}

    result = run_transaction(perform)
    if result.get("locked"):
        raise ApiError("登入失敗次數過多，請稍後再試", 429)
    if result.get("failed"):
        raise ApiError("帳號或密碼錯誤", 401)
    return result


def logout(token):
    if isinstance(token, str) and 32 <= len(token) <= 256:
        session_ref(token).delete()
    return {"success": True}


def change_password(token, old, new):
    if not isinstance(old, str) or not isinstance(new, str) or not 8 <= len(new) <= 256:
        raise ApiError("新密碼須介於 8 至 256 個字元")
    new_hash = generate_password_hash(new)

    def perform(transaction):
        account = authenticate(transaction, token)
        if not check_password_hash(account["passwordHash"], old):
            raise ApiError("原密碼錯誤", 400)
        transaction.update(account_ref(account["username"]), {
            "passwordHash": new_hash, "sessionVersion": account.get("sessionVersion", 0) + 1
        })
        transaction.delete(session_ref(token))
        audit(transaction, account["username"], "change_password")
        return {"success": True}
    return run_transaction(perform)


def management_state(token):
    date = today()

    def perform(transaction):
        account = authenticate(transaction, token)
        daily = snapshot_data(get_ref(transaction, db().collection("days").document(date)))
        settings = snapshot_data(get_ref(transaction, db().collection("settings").document("general")))
        date_guard(date)
        return {"username": account["username"], "role": account["role"], "date": date,
                "assignee": daily.get("assignee", ""), "shop": daily.get("shop", ""),
                "quotaLabel": settings.get("quotaLabel", "扣打"), "quotaValue": settings.get("quotaValue", 0),
                "shops": shop_names()}
    return run_transaction(perform)


def list_accounts(token):
    def perform(transaction):
        authenticate(transaction, token, admin=True)
        accounts = [s.to_dict() for s in db().collection("accounts").stream(transaction=transaction)]
        return [{"username": a["username"], "role": a["role"], "enabled": a["enabled"]} for a in accounts]
    return run_transaction(perform)


def create_account(token, name):
    name = username(name)
    ref = account_ref(name)
    password_hash = generate_password_hash(name)

    def perform(transaction):
        actor = authenticate(transaction, token, admin=True)
        if get_ref(transaction, ref).exists:
            raise ApiError("此工號已存在", 409)
        transaction.create(ref, {"username": name, "role": "operator", "enabled": True,
                                 "passwordHash": password_hash, "sessionVersion": 0})
        transaction.set(db().collection("login_limits").document(key(name)), {"failures": 0, "lockedUntil": None})
        audit(transaction, actor["username"], "create_account", {"username": name})
        return {"success": True}
    return run_transaction(perform)


def update_account(token, name, action):
    name = username(name)
    ref = account_ref(name)
    password_hash = generate_password_hash(name) if action == "reset" else None
    if action not in ("enable", "disable", "reset"):
        raise ApiError("無效的帳號操作")

    def perform(transaction):
        actor = authenticate(transaction, token, admin=True)
        account = snapshot_data(get_ref(transaction, ref))
        if not account:
            raise ApiError("找不到帳號", 404)
        if account["role"] == "admin":
            raise ApiError("不能在此修改主管理員帳號", 403)
        update = {"sessionVersion": account.get("sessionVersion", 0) + 1}
        if action == "reset":
            update["passwordHash"] = password_hash
        else:
            update["enabled"] = action == "enable"
        transaction.update(ref, update)
        if action == "reset":
            transaction.set(db().collection("login_limits").document(key(name)), {"failures": 0, "lockedUntil": None})
        audit(transaction, actor["username"], "account_" + action, {"username": name})
        return {"success": True, "actor": actor["username"]}
    return run_transaction(perform)


def set_assignee(token, name):
    date = today()
    if name:
        name = username(name)
    ref = db().collection("days").document(date)

    def perform(transaction):
        actor = authenticate(transaction, token, admin=True)
        if name:
            account = snapshot_data(get_ref(transaction, account_ref(name)))
            if not account or not account.get("enabled") or account.get("role") != "operator":
                raise ApiError("請選擇啟用中的次級帳號")
        get_ref(transaction, ref)
        date_guard(date)
        transaction.set(ref, {"assignee": name}, merge=True)
        audit(transaction, actor["username"], "set_assignee", {"date": date, "assignee": name})
        return {"success": True}
    return run_transaction(perform)


def set_shop(token, shop):
    if not isinstance(shop, str) or (shop and shop not in shop_names()):
        raise ApiError("請選擇有效店家")
    date = today()
    ref = db().collection("days").document(date)

    def perform(transaction):
        actor, daily = require_shop_access(transaction, token, date)
        orders = list(ref.collection("orders").limit(1).stream(transaction=transaction))
        if shop != daily.get("shop", "") and orders:
            raise ApiError("當日已有訂單，無法切換或關閉店家；請由主管理員先處理訂單", 409)
        date_guard(date)
        transaction.set(ref, {"shop": shop, "revision": uuid.uuid4().hex}, merge=True)
        audit(transaction, actor["username"], "set_shop", {"date": date, "shop": shop})
        return {"success": True}
    return run_transaction(perform)


def set_quota(token, label, value):
    label = text(label, "扣打名稱", 30)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100000:
        raise ApiError("扣打金額須為 0 至 100000 的數字")
    date = today()
    ref = db().collection("settings").document("general")

    def perform(transaction):
        actor = authenticate(transaction, token, admin=True)
        get_ref(transaction, ref)
        date_guard(date)
        transaction.set(ref, {"quotaLabel": label, "quotaValue": value, "revision": uuid.uuid4().hex}, merge=True)
        audit(transaction, actor["username"], "set_quota", {"label": label, "value": value})
        return {"success": True}
    return run_transaction(perform)


def list_staff(include_disabled=False):
    staff = [s.to_dict() | {"id": s.id} for s in db().collection("staff").stream()]
    return sorted([s for s in staff if include_disabled or s.get("enabled")], key=lambda s: s["name"])


def manage_staff(token, staff_id, name, enabled):
    name = text(name, "姓名", 50)
    if not isinstance(enabled, bool):
        raise ApiError("人員狀態無效")
    if staff_id and not re.fullmatch(r"[a-f0-9]{32}", staff_id):
        raise ApiError("人員識別碼無效")
    staff_id = staff_id or uuid.uuid4().hex
    ref = db().collection("staff").document(staff_id)
    index = db().collection("staff_names").document(key(name.casefold()))

    def perform(transaction):
        actor = authenticate(transaction, token, admin=True)
        previous = snapshot_data(get_ref(transaction, ref))
        existing = snapshot_data(get_ref(transaction, index))
        old_index = None
        if previous and previous["name"].casefold() != name.casefold():
            old_index = db().collection("staff_names").document(key(previous["name"].casefold()))
            get_ref(transaction, old_index)
        if existing and existing["staffId"] != staff_id:
            raise ApiError("姓名已存在，請先區分同名人員", 409)
        if old_index:
            transaction.delete(old_index)
        transaction.set(index, {"staffId": staff_id})
        transaction.set(ref, {"name": name, "enabled": enabled})
        audit(transaction, actor["username"], "manage_staff", {"staffId": staff_id, "enabled": enabled})
        return {"success": True, "id": staff_id}
    return run_transaction(perform)


def get_current_shop():
    date = today()
    daily = snapshot_data(db().collection("days").document(date).get())
    settings = snapshot_data(db().collection("settings").document("general").get())
    date_guard(date)
    return {"title": "手銲股訂飲料", "shop": daily.get("shop", ""),
            "quotaLabel": settings.get("quotaLabel", "扣打"), "quotaValue": settings.get("quotaValue", 0),
            "date": date, "shopRevision": daily.get("revision", ""), "quotaRevision": settings.get("revision", "")}


def get_order_data():
    date = today()
    settings = snapshot_data(db().collection("settings").document("general").get())
    quota = settings.get("quotaValue", 0)
    orders = [s.to_dict() for s in db().collection("days").document(date).collection("orders").stream()]
    people = {s["id"]: s["name"] for s in list_staff()}
    for order in orders:
        people.setdefault(order["staffId"], order["name"])
    by_person = {order["staffId"]: order for order in orders}
    rows = []
    for staff_id, name in sorted(people.items(), key=lambda item: item[1]):
        order = by_person.get(staff_id)
        if not order:
            rows.append([name, "", "", "", "", "", "", ""])
            continue
        price = order["totalPrice"]
        diff = max(0, price - quota) if quota > 0 else 0
        rows.append([name, order["drink"], order["size"], order["sugar"],
                     order["ice"], order["topping"], price, diff or ""])
    rows.append(["總計", "", "", "", "", "", sum(o["totalPrice"] for o in orders),
                 sum(max(0, o["totalPrice"] - quota) for o in orders) if quota > 0 else 0])
    date_guard(date)
    return [HEADER] + rows


def add_order(data):
    if not isinstance(data, dict):
        raise ApiError("訂單格式錯誤")
    staff_id = data.get("staffId", "")
    if not isinstance(staff_id, str) or not re.fullmatch(r"[a-f0-9]{32}", staff_id):
        raise ApiError("請選擇有效的訂購人")
    fields = {field: text(data.get(field), label) for field, label in (
        ("drink", "飲品"), ("size", "容量"), ("sugar", "甜度"), ("ice", "冰塊"), ("topping", "配料"))}
    price = data.get("totalPrice")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or not 0 <= price <= 100000:
        raise ApiError("訂單金額無效")
    date = today()
    day_ref = db().collection("days").document(date)
    order_ref = day_ref.collection("orders").document(staff_id)
    staff_ref = db().collection("staff").document(staff_id)
    settings_ref = db().collection("settings").document("general")

    def perform(transaction):
        day = snapshot_data(get_ref(transaction, day_ref))
        settings = snapshot_data(get_ref(transaction, settings_ref))
        staff = snapshot_data(get_ref(transaction, staff_ref))
        old = snapshot_data(get_ref(transaction, order_ref))
        if not day.get("shop") or day["shop"] != data.get("shop") or day.get("revision") != data.get("shopRevision"):
            raise ApiError("今日店家已變更或尚未開放，請重新整理", 409)
        if data.get("date") != date or settings.get("revision", "") != data.get("quotaRevision", ""):
            raise ApiError("日期或扣打已變更，請重新整理並確認訂單", 409)
        if not staff or not staff.get("enabled"):
            raise ApiError("訂購人不存在或已停用", 400)
        if old:
            previous = {field: old[field] for field in ("drink", "size", "sugar", "ice", "topping", "totalPrice")}
            if not data.get("overwrite") or data.get("expectedOrder") != previous:
                raise ApiError("此人的訂單已變更，請重新確認後再覆寫", 409)
        date_guard(date)
        order = fields | {"name": staff["name"], "staffId": staff_id, "totalPrice": price,
                          "shop": day["shop"], "updatedAt": now()}
        transaction.set(order_ref, order)
        transaction.set(day_ref, {"orderMutation": uuid.uuid4().hex}, merge=True)
        return {"success": True}
    return run_transaction(perform)
