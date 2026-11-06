const SESSION_KEY = "beverage_management_session";
const message = document.getElementById("message");

async function requestApi(action, payload) {
    const token = sessionStorage.getItem(SESSION_KEY);
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const options = { headers };
    let url = GAS_API_URL;
    if (payload === undefined) {
        url += `?action=${encodeURIComponent(action)}`;
    } else {
        options.method = "POST";
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify({ action, ...payload });
    }
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok || data.error) {
        if (response.status === 401 && action !== "login") {
            sessionStorage.removeItem(SESSION_KEY);
        }
        throw new Error(data.error || `伺服器回應錯誤 (${response.status})`);
    }
    return data.data;
}

function showError(error) {
    message.textContent = error.message || "操作失敗，請稍後再試";
}

function formAction(id, callback) {
    document.getElementById(id).addEventListener("submit", async event => {
        event.preventDefault();
        const button = event.submitter;
        button.disabled = true;
        message.textContent = "";
        try {
            await callback();
        } catch (error) {
            showError(error);
        } finally {
            button.disabled = false;
        }
    });
}

const loginForm = document.getElementById("loginForm");
if (loginForm) {
    formAction("loginForm", async () => {
        const result = await requestApi("login", {
            username: document.getElementById("username").value,
            password: document.getElementById("password").value
        });
        sessionStorage.setItem(SESSION_KEY, result.token);
        location.href = "admin.html";
    });
} else {
    startManagement().catch(showError);
}

async function startManagement() {
    if (!sessionStorage.getItem(SESSION_KEY)) {
        location.href = "login.html";
        return;
    }
    await refreshManagement();

    formAction("shopForm", async () => {
        await requestApi("setShop", { shop: document.getElementById("shop").value });
        await refreshManagement();
        message.textContent = "店家設定已更新。";
    });
    formAction("assigneeForm", async () => {
        await requestApi("setAssignee", { username: document.getElementById("assignee").value });
        await refreshManagement();
        message.textContent = "今日指派已更新。";
    });
    formAction("quotaForm", async () => {
        await requestApi("setQuota", {
            label: document.getElementById("quotaLabel").value,
            value: Number(document.getElementById("quotaValue").value)
        });
        await refreshManagement();
        message.textContent = "扣打設定已更新。";
    });
    formAction("accountForm", async () => {
        await requestApi("createAccount", { username: document.getElementById("newAccount").value });
        document.getElementById("accountForm").reset();
        await refreshManagement();
        message.textContent = "帳號已新增。";
    });
    formAction("staffForm", async () => {
        await requestApi("manageStaff", { name: document.getElementById("staffName").value, enabled: true });
        document.getElementById("staffForm").reset();
        await refreshManagement();
        message.textContent = "人員已新增。";
    });
    formAction("passwordForm", async () => {
        await requestApi("changePassword", {
            oldPassword: document.getElementById("oldPassword").value,
            newPassword: document.getElementById("newPassword").value
        });
        sessionStorage.removeItem(SESSION_KEY);
        alert("密碼已修改，請重新登入。");
        location.href = "login.html";
    });
    document.getElementById("logout").addEventListener("click", async () => {
        try {
            await requestApi("logout", {});
            sessionStorage.removeItem(SESSION_KEY);
            location.href = "login.html";
        } catch (error) {
            showError(error);
        }
    });
}

function addOption(select, value, label) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
}

function rowButton(row, label, callback) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", async () => {
        button.disabled = true;
        message.textContent = "";
        try {
            await callback();
        } catch (error) {
            showError(error);
        } finally {
            button.disabled = false;
        }
    });
    row.appendChild(button);
}

async function refreshManagement() {
    const state = await requestApi("managementState");
    const admin = state.role === "admin";
    const canManageShop = admin || state.assignee === state.username;
    document.getElementById("identity").textContent =
        `${state.username}｜${admin ? "主管理員" : "次級人員"}｜${state.date}（台灣時間）`;
    document.getElementById("dailyStatus").textContent =
        `今日負責人：${state.assignee || "尚未指派"}；開放店家：${state.shop || "尚未開放"}` +
        (canManageShop ? "" : "。您尚未被指派，無法管理店家。");
    const shop = document.getElementById("shop");
    shop.replaceChildren();
    addOption(shop, "", "關閉訂購");
    state.shops.forEach(name => addOption(shop, name, name));
    shop.value = state.shop;
    shop.disabled = !canManageShop;
    document.querySelector("#shopForm button").disabled = !canManageShop;
    document.getElementById("adminSections").hidden = !admin;
    if (admin) {
        const [accounts, staff] = await Promise.all([requestApi("listAccounts"), requestApi("listStaff")]);
        const assignee = document.getElementById("assignee");
        assignee.replaceChildren();
        addOption(assignee, "", "不指派");
        accounts.filter(a => a.role === "operator" && a.enabled)
            .forEach(a => addOption(assignee, a.username, a.username));
        if (state.assignee && !Array.from(assignee.options).some(option => option.value === state.assignee)) {
            addOption(assignee, state.assignee, `${state.assignee}（已停用）`);
        }
        assignee.value = state.assignee;
        document.getElementById("quotaLabel").value = state.quotaLabel;
        document.getElementById("quotaValue").value = state.quotaValue;
        renderAccounts(accounts);
        renderStaff(staff);
    }
    document.getElementById("management").hidden = false;
}

function renderAccounts(accounts) {
    const list = document.getElementById("accountList");
    list.replaceChildren();
    accounts.filter(a => a.role === "operator").forEach(account => {
        const row = document.createElement("div");
        row.className = "manage-row";
        const title = document.createElement("div");
        title.textContent = `${account.username}（${account.enabled ? "啟用" : "停用"}）`;
        row.appendChild(title);
        rowButton(row, account.enabled ? "停用" : "啟用", async () => {
            await requestApi("updateAccount", { username: account.username, operation: account.enabled ? "disable" : "enable" });
            await refreshManagement();
        });
        rowButton(row, "重設密碼", async () => {
            if (!confirm(`確定將 ${account.username} 的密碼重設為工號？`)) return;
            await requestApi("updateAccount", { username: account.username, operation: "reset" });
            message.textContent = "密碼已重設，該帳號的登入已撤銷。";
        });
        list.appendChild(row);
    });
}

function renderStaff(staff) {
    const list = document.getElementById("staffList");
    list.replaceChildren();
    staff.forEach(person => {
        const row = document.createElement("div");
        row.className = "manage-row";
        const title = document.createElement("div");
        title.textContent = `${person.name}（${person.enabled ? "啟用" : "停用"}）`;
        row.appendChild(title);
        rowButton(row, "修改姓名", async () => {
            const name = prompt("請輸入新的姓名", person.name);
            if (name === null) return;
            await requestApi("manageStaff", { id: person.id, name, enabled: person.enabled });
            await refreshManagement();
        });
        rowButton(row, person.enabled ? "停用" : "啟用", async () => {
            await requestApi("manageStaff", { id: person.id, name: person.name, enabled: !person.enabled });
            await refreshManagement();
        });
        list.appendChild(row);
    });
}
