"""初始化主管理員，或將試算表的人員及扣打一次性匯入 Firestore。"""
import argparse
import os
import uuid

import google.auth
import gspread
import requests
from dotenv import load_dotenv
from google.cloud import firestore
from gspread.utils import ValueRenderOption
from werkzeug.security import generate_password_hash

import firestore_backend as store


def bootstrap_admin():
    name = store.username(input("請輸入主管理員工號："))
    password_hash = generate_password_hash(name)
    ref = store.account_ref(name)
    marker = store.db().collection("system").document("bootstrap")

    def perform(transaction):
        if marker.get(transaction=transaction).exists or ref.get(transaction=transaction).exists:
            raise RuntimeError("主管理員已初始化或帳號已存在，不會覆寫")
        transaction.create(ref, {"username": name, "role": "admin", "enabled": True,
                                 "passwordHash": password_hash, "sessionVersion": 0})
        transaction.set(store.db().collection("login_limits").document(store.key(name)),
                        {"failures": 0, "lockedUntil": None})
        transaction.create(marker, {"initialized": True, "createdAt": store.now()})
    store.run_transaction(perform)
    print("主管理員已建立。初始密碼與工號相同，請儘速登入並修改密碼。")


def reset_admin_password():
    name = store.username(input("請輸入要重設的主管理員工號："))
    ref = store.account_ref(name)
    password_hash = generate_password_hash(name)

    def perform(transaction):
        account = store.snapshot_data(ref.get(transaction=transaction))
        if not account or account.get("role") != "admin":
            raise RuntimeError("找不到主管理員帳號")
        transaction.update(ref, {"passwordHash": password_hash,
                                 "sessionVersion": account.get("sessionVersion", 0) + 1})
        transaction.set(store.db().collection("login_limits").document(store.key(name)),
                        {"failures": 0, "lockedUntil": None})
        store.audit(transaction, name, "admin_password_recovery")
    store.run_transaction(perform)
    print("主管理員密碼已重設為工號，既有 session 已撤銷；請儘速登入修改。")


def validate_import(names, label, value):
    label = store.text(str(label).strip() or "扣打", "扣打名稱", 30)
    try:
        quota = float(value or 0)
    except (TypeError, ValueError) as error:
        raise RuntimeError("扣打金額無法轉換為數字") from error
    if not 0 <= quota <= 100000:
        raise RuntimeError("扣打金額超出允許範圍")
    names = [name.strip() for name in names if isinstance(name, str) and name.strip()]
    names = [name for name in names if name not in ("總計", "合計")]
    if len(names) != len({name.casefold() for name in names}):
        raise RuntimeError("來源含重複姓名，請先區分同名人員再匯入")
    for name in names:
        store.text(name, "姓名", 50)
    if len(names) > 200:
        raise RuntimeError("人員超過單次匯入上限，請先調整匯入工具")
    return names, label, quota


def read_sheet():
    load_dotenv()
    url = os.environ.get("SHEET_URL", "")
    if not url:
        raise RuntimeError("請先以環境變數 SHEET_URL 指定原試算表")
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly",
              "https://www.googleapis.com/auth/drive.readonly"]
    credentials, _ = google.auth.default(scopes=scopes)
    sheet = gspread.authorize(credentials).open_by_url(url).get_worksheet(0)
    cells = sheet.get("C1:D1", value_render_option=ValueRenderOption.unformatted)
    row = (cells[0] if cells else []) + ["", ""]
    return validate_import(sheet.col_values(1)[2:], row[0], row[1])


def read_existing_api():
    url = os.environ.get("OLD_API_URL", "").strip()
    if not url.startswith("https://"):
        raise RuntimeError("請以 OLD_API_URL 指定目前正式服務的 HTTPS 網址")

    def get(action):
        response = requests.get(url, params={"action": action}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(f"舊服務讀取失敗：{payload['error']}")
        return payload["data"]

    names = get("getStaffNames")
    settings = get("getCurrentShop")
    if not isinstance(names, list) or not isinstance(settings, dict):
        raise RuntimeError("舊服務回應格式不正確")
    return validate_import(names, settings.get("quotaLabel", ""), settings.get("quotaValue", 0))


def import_data(apply, source):
    names, label, quota = read_sheet() if source == "sheet" else read_existing_api()
    print(f"預覽：{len(names)} 位人員，扣打名稱「{label}」，扣打金額 {quota:g}。")
    print("不匯入訂單，也不開放店家；不會修改原試算表。")
    if not apply:
        print("這是預覽；確認後加入 --apply 才會寫入。")
        return
    marker = store.db().collection("system").document("sheet_import")
    settings = store.db().collection("settings").document("general")
    entries = [(name, uuid.uuid4().hex) for name in names]

    def perform(transaction):
        if marker.get(transaction=transaction).exists or settings.get(transaction=transaction).exists:
            raise RuntimeError("已匯入或已有設定，不會覆寫")
        if list(store.db().collection("staff").limit(1).stream(transaction=transaction)):
            raise RuntimeError("已有訂購人員，不會合併或覆寫")
        for name, _ in entries:
            index = store.db().collection("staff_names").document(store.key(name.casefold()))
            if index.get(transaction=transaction).exists:
                raise RuntimeError("人員姓名索引已存在，不會覆寫")
        transaction.create(settings, {"quotaLabel": label, "quotaValue": quota, "revision": uuid.uuid4().hex})
        for name, staff_id in entries:
            transaction.create(store.db().collection("staff").document(staff_id), {"name": name, "enabled": True})
            transaction.create(store.db().collection("staff_names").document(store.key(name.casefold())),
                               {"staffId": staff_id})
        transaction.create(marker, {"importedAt": store.now(), "staffCount": len(names)})
    store.run_transaction(perform)
    print("匯入完成；今日仍為關店狀態。")


def main():
    parser = argparse.ArgumentParser(description="初始化 Firestore")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bootstrap-admin", action="store_true")
    group.add_argument("--reset-admin-password", action="store_true")
    group.add_argument("--import-sheet", action="store_true")
    group.add_argument("--import-api", action="store_true")
    parser.add_argument("--apply", action="store_true", help="確認將匯入資料寫入 Firestore")
    args = parser.parse_args()
    if args.bootstrap_admin:
        bootstrap_admin()
    elif args.reset_admin_password:
        reset_admin_password()
    else:
        import_data(args.apply, "sheet" if args.import_sheet else "api")


if __name__ == "__main__":
    main()
