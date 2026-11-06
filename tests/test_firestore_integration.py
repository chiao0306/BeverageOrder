import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import requests
from werkzeug.security import generate_password_hash

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "beverage-order-test")
import firestore_backend as store
import main
import setup_firestore


@unittest.skipUnless(os.environ.get("FIRESTORE_EMULATOR_HOST") == "127.0.0.1:8787",
                     "需要本機 Firestore Emulator")
class FirestoreIntegrationTest(unittest.TestCase):
    def setUp(self):
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        response = requests.delete(
            f"http://127.0.0.1:8787/emulator/v1/projects/{project}/databases/(default)/documents",
            timeout=10,
        )
        response.raise_for_status()
        store._client = None
        self.client = main.app.test_client()
        store.account_ref("admin").set({
            "username": "admin", "role": "admin", "enabled": True,
            "passwordHash": generate_password_hash("admin-password"), "sessionVersion": 0,
        })
        self.admin = self.post("login", {"username": "admin", "password": "admin-password"})["token"]
        self.post("createAccount", {"username": "operator"}, self.admin)
        self.operator = self.post("login", {"username": "operator", "password": "operator"})["token"]
        self.staff_id = self.post("manageStaff", {"name": "測試人員", "enabled": True}, self.admin)["id"]
        self.post("setQuota", {"label": "補助", "value": 60}, self.admin)

    def post_response(self, action, payload, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post("/", json={"action": action, **payload}, headers=headers)

    def post(self, action, payload, token=None):
        response = self.post_response(action, payload, token)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotIn("error", response.get_json())
        return response.get_json()["data"]

    def get(self, action, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = self.client.get("/", query_string={"action": action}, headers=headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["data"]

    def assign_and_open(self):
        self.post("setAssignee", {"username": "operator"}, self.admin)
        self.post("setShop", {"shop": store.shop_names()[0]}, self.operator)
        return self.get("getCurrentShop")

    def order(self, info, **extra):
        return {
            "staffId": self.staff_id, "drink": "測試飲料", "size": "大杯", "sugar": "無糖",
            "ice": "去冰", "topping": "無", "totalPrice": 75, "shop": info["shop"],
            "date": info["date"], "shopRevision": info["shopRevision"], "quotaRevision": info["quotaRevision"],
            **extra,
        }

    def test_auth_assignment_and_revocation(self):
        self.assertEqual(self.post_response("setShop", {"shop": store.shop_names()[0]}, self.operator).status_code, 403)
        self.assertEqual(self.post_response("setQuota", {"label": "扣打", "value": 1}, self.operator).status_code, 403)
        self.assertEqual(self.post_response("setAssignee", {"username": "operator"}).status_code, 401)
        self.assign_and_open()
        self.post("setAssignee", {"username": ""}, self.admin)
        self.assertEqual(self.post_response("setShop", {"shop": ""}, self.operator).status_code, 403)
        self.post("updateAccount", {"username": "operator", "operation": "reset"}, self.admin)
        self.assertEqual(self.post_response("setShop", {"shop": ""}, self.operator).status_code, 401)

    def test_orders_quota_and_safe_switch(self):
        info = self.assign_and_open()
        order = self.order(info)
        self.post("addOrder", {"orderData": order})
        rows = self.get("getOrderData")
        self.assertEqual(rows[1][0], "測試人員")
        self.assertEqual(rows[1][7], 15)
        self.assertEqual(self.post_response("setShop", {"shop": store.shop_names()[1]}, self.admin).status_code, 409)
        self.assertEqual(self.post_response("addOrder", {"orderData": order}).status_code, 409)
        previous = {k: order[k] for k in ("drink", "size", "sugar", "ice", "topping", "totalPrice")}
        replacement = self.order(info, overwrite=True, expectedOrder=previous, totalPrice=90)
        self.post("addOrder", {"orderData": replacement})
        self.assertEqual(self.post_response("addOrder", {"orderData": replacement}).status_code, 409)
        self.post("setQuota", {"label": "補助", "value": 70}, self.admin)
        self.assertEqual(self.get("getOrderData")[1][7], 20)
        self.assertEqual(self.post_response("addOrder", {"orderData": order}).status_code, 409)

    def test_day_rollover(self):
        info = self.assign_and_open()
        self.post("addOrder", {"orderData": self.order(info)})
        with patch.object(store, "today", return_value="2099-01-01"):
            self.assertEqual(self.get("getCurrentShop")["shop"], "")
            self.assertEqual(self.get("getCurrentShop")["quotaValue"], 60)
            self.assertEqual(self.get("getOrderData")[1][1], "")
            self.assertEqual(self.post_response("setShop", {"shop": store.shop_names()[0]}, self.operator).status_code, 403)
        self.assertEqual(self.get("getOrderData")[1][1], "測試飲料")

    def test_password_change_and_login_lock(self):
        self.post("changePassword", {"oldPassword": "operator", "newPassword": "new-password"}, self.operator)
        self.assertEqual(self.post_response("setShop", {"shop": ""}, self.operator).status_code, 401)
        for _ in range(store.MAX_FAILURES):
            self.assertEqual(self.post_response("login", {"username": "operator", "password": "bad"}).status_code, 401)
        self.assertEqual(self.post_response("login", {"username": "operator", "password": "new-password"}).status_code, 429)
        self.post("updateAccount", {"username": "operator", "operation": "reset"}, self.admin)
        self.post("login", {"username": "operator", "password": "operator"})

    def test_staff_duplicate_rename_disable_and_logout(self):
        self.assertEqual(self.post_response("manageStaff", {"name": "測試人員", "enabled": True}, self.admin).status_code, 409)
        info = self.assign_and_open()
        self.post("addOrder", {"orderData": self.order(info)})
        self.post("manageStaff", {"id": self.staff_id, "name": "更名人員", "enabled": True}, self.admin)
        self.assertEqual(self.get("getOrderData")[1][0], "更名人員")
        self.post("manageStaff", {"id": self.staff_id, "name": "更名人員", "enabled": False}, self.admin)
        self.assertEqual(self.get("getStaffNames"), [])
        self.assertEqual(self.post_response("addOrder", {"orderData": self.order(info)}).status_code, 400)
        self.post("logout", {}, self.admin)
        self.assertEqual(self.post_response("setQuota", {"label": "扣打", "value": 10}, self.admin).status_code, 401)

    def test_expired_session_and_invalid_shop(self):
        self.assertEqual(self.post_response("setShop", {"shop": "不存在的店家"}, self.admin).status_code, 400)
        store.session_ref(self.operator).update({"expiresAt": store.now()})
        self.assertEqual(self.post_response("setShop", {"shop": ""}, self.operator).status_code, 401)
        self.assertEqual(self.client.options("/").headers["Access-Control-Allow-Headers"], "Content-Type, Authorization")

    def test_menu_files_match_frontend(self):
        root = Path(__file__).resolve().parent.parent
        html = (root / "index.html").read_text(encoding="utf-8")
        referenced = set(re.findall(r'src="menu/(shop_[^"?]+\.js)', html))
        existing = {p.name for p in (root / "menu").glob("shop_*.js")}
        self.assertEqual(referenced, existing)
        self.assertEqual(len(referenced), len(store.shop_names()))

    def test_import_is_atomic_and_refuses_repeat(self):
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        response = requests.delete(
            f"http://127.0.0.1:8787/emulator/v1/projects/{project}/databases/(default)/documents",
            timeout=10,
        )
        response.raise_for_status()
        with patch.object(setup_firestore, "read_existing_api", return_value=(["甲", "乙"], "扣打", 60)):
            setup_firestore.import_data(True, "api")
            self.assertEqual(len(store.list_staff()), 2)
            with self.assertRaises(RuntimeError):
                setup_firestore.import_data(True, "api")
            self.assertEqual(len(store.list_staff()), 2)

    def test_audit_entries_do_not_include_password(self):
        entries = [item.to_dict() for item in store.db().collection("audit").stream()]
        self.assertTrue(any(entry["action"] == "create_account" for entry in entries))
        self.assertTrue(any(entry["action"] == "manage_staff" for entry in entries))
        self.assertNotIn("admin-password", str(entries))
        self.assertNotIn("passwordHash", str(entries))

    def test_concurrent_shop_change_and_order(self):
        info = self.assign_and_open()
        order = self.order(info)

        def place():
            try:
                store.add_order(order)
                return "ordered"
            except store.ApiError:
                return "rejected"

        def switch():
            try:
                store.set_shop(self.admin, store.shop_names()[1])
                return "switched"
            except store.ApiError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda fn: fn(), [place, switch]))
        self.assertIn(results, (["ordered", "rejected"], ["rejected", "switched"]))


if __name__ == "__main__":
    unittest.main()
