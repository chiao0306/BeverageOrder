// 後端已改為 Cloud Run，部署後請將此處換成實際服務網址（結尾不要加斜線）
const GAS_API_URL = "https://beverageorder-1075878684960.asia-east1.run.app";
// 用於將特殊字元轉換為安全格式（防止 XSS 攻擊）

function escapeHtml(unsafe) {
    return (unsafe || "").toString()
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}

// ==========================================
// 1. 與後端溝通的共用函數
// ==========================================
async function apiGet(action) {
    const res = await fetch(`${GAS_API_URL}?action=${action}`);
    const json = await res.json();
    if (json.error) throw new Error(json.error);
    return json.data;
}

async function apiPost(action, payload) {
    const res = await fetch(GAS_API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: JSON.stringify({ action: action, ...payload })
    });
    const json = await res.json();
    if (json.error) throw new Error(json.error);
    return json.data;
}

// ==========================================
// 2. 全域變數
// ==========================================
let activeShop = ""; let shopTitle = ""; let quotaLabel = ""; let quotaValue = 0;
let currentShopName = ""; let currentDrinkObj = null; let isCompactView = true;
let originalSummaryData = []; let isSortedByItem = false;
let raffleTimeout = null; let isRaffleActive = false;

// ==========================================
// 3. 網頁載入時啟動
// ==========================================
window.onload = async function() {
    try {
        // 取得店家與扣打資訊
        const shopInfo = await apiGet('getCurrentShop');
        shopTitle = shopInfo.title; activeShop = shopInfo.shop;
        quotaLabel = shopInfo.quotaLabel || "扣打"; quotaValue = parseFloat(shopInfo.quotaValue) || 0;
        document.getElementById("shopInfoDisplay").innerText = `店家: ${activeShop}`;
        updateToggleButtonText(); 
        renderDirectory(); 

        // 取得人員名單
        const names = await apiGet('getStaffNames');
        const nameSelect = document.getElementById("userName");
        nameSelect.innerHTML = '<option value="" disabled selected>請選擇你的名字</option>';
        names.forEach(name => nameSelect.innerHTML += `<option value="${name}">${name}</option>`);
    } catch (e) {
        document.getElementById("directoryLoading").innerText = "連線失敗，請重新整理頁面。";
        console.error(e);
    }
};

// ==========================================
// 4. 點餐與 UI 邏輯
// ==========================================
function updateToppingList() {
    const toppingSelect = document.getElementById("userTopping");
    const sizeSelect = document.getElementById("userSize");
    const shopData = allShopsDatabase[currentShopName];
    if (!toppingSelect || !sizeSelect || !shopData) return;

    const currentSelected = toppingSelect.value;
    const sizeName = sizeSelect.value;
    toppingSelect.innerHTML = '<option value="無" data-price="0">無配料 (+$0)</option>';
    
    if (shopData.toppings) {
        shopData.toppings.forEach(t => {
            let displayPrice = t.price;
            if (currentShopName === "50嵐") {
                const pureTeas = ["茉莉綠茶", "阿薩姆紅茶", "四季春青茶", "黃金烏龍"];
                if (pureTeas.includes(currentDrinkObj.name)) {
                    displayPrice = (sizeName.includes("M") || sizeName.includes("中")) ? 5 : 10;
                } else { displayPrice = 0; }
            }
            const option = document.createElement("option");
            option.value = t.name;
            option.setAttribute("data-price", displayPrice);
            option.innerText = `${t.name} (+$${displayPrice})`;
            toppingSelect.appendChild(option);
        });
    }
    toppingSelect.value = currentSelected || "無";
    if (!toppingSelect.value) toppingSelect.value = "無";
}

function updateQuotaInfo() {
    const quotaArea = document.getElementById("quotaDisplayArea");
    const sizeSelect = document.getElementById("userSize");
    const toppingSelect = document.getElementById("userTopping");
    if (!sizeSelect || !toppingSelect) return;

    const drinkPrice = parseFloat(sizeSelect.options[sizeSelect.selectedIndex]?.getAttribute("data-price") || 0);
    const toppingPrice = parseFloat(toppingSelect.options[toppingSelect.selectedIndex]?.getAttribute("data-price") || 0);
    const totalPrice = drinkPrice + toppingPrice;
    
    if (quotaValue <= 0) {
        quotaArea.innerHTML = `💰 當前總金額：$${totalPrice}`;
    } else {
        const diff = totalPrice - quotaValue;
        quotaArea.innerHTML = `💰 總計 $${totalPrice} | ${quotaLabel} $${quotaValue}<br><span style="color:#E67E22">目前差額：$${diff > 0 ? diff : 0}</span>`;
    }
}

function renderDirectory() {
    document.getElementById("directoryLoading").style.display = "none";
    document.getElementById("directoryTitle").style.display = "block";
    
    // 取得剛才在 HTML 新增的容器與分隔線
    const openContainer = document.getElementById("openShopContainer");
    const closedContainer = document.getElementById("closedShopContainer");
    const closedDivider = document.getElementById("closedShopDivider");
    
    // 清空舊內容
    openContainer.innerHTML = "";
    closedContainer.innerHTML = "";
    
    let hasClosedShops = false;
    
    for (const shopName in allShopsDatabase) {
        const btn = document.createElement("button"); 
        btn.className = "shop-btn"; // 保持你原本的按鈕樣式
        
        if (shopName === activeShop) {
            // 開放的店家放進 openContainer
            btn.style.color = allShopsDatabase[shopName].color;
            btn.style.borderColor = allShopsDatabase[shopName].color;
            btn.innerText = shopName;
            btn.onclick = () => loadShopMenu(shopName);
            openContainer.appendChild(btn); 
        } else { 
            // 未開放的店家放進 closedContainer
            btn.classList.add("disabled"); 
            btn.innerText = shopName; // 已經把 (未開放) 拿掉
            // 讓未開放按鈕寬度填滿自己所在的欄位，看起來會更整齊
            btn.style.width = "100%"; 
            btn.style.margin = "0"; 
            closedContainer.appendChild(btn); 
            hasClosedShops = true;
        }
    }

    // 顯示開放店家容器
    openContainer.style.display = "flex";
    
    // 判斷是否有未開放店家，來決定是否顯示分隔線與未開放區塊
    if (hasClosedShops) {
        closedDivider.style.display = "block";
        // 把未開放區塊的排版改為 Grid (網格)，並設定為兩欄
        closedContainer.style.display = "grid";
        closedContainer.style.gridTemplateColumns = "1fr 1fr"; 
        closedContainer.style.gap = "10px"; // 按鈕之間的間距
    } else {
        closedDivider.style.display = "none";
        closedContainer.style.display = "none";
    }
}

// ==========================================
// 替換這段：載入店家菜單的函數
// ==========================================
function loadShopMenu(shopName) {
    currentShopName = shopName;
    const shopData = allShopsDatabase[shopName];
    document.getElementById("appHeader").innerText = shopName;
    document.getElementById("appHeader").style.background = shopData.color;
    
    const menuContainer = document.getElementById("menuContainer");
    menuContainer.innerHTML = "";
    for (const [category, drinks] of Object.entries(shopData.menu)) {
        const titleDiv = document.createElement("div"); titleDiv.className = "category-title";
        titleDiv.style.borderLeftColor = shopData.color; 
        
        // ✨ 魔法 1：讓「分類標題」的括號自動換行並變小
        titleDiv.innerHTML = category.replace(/ \((.*?)\)/, '<br><span style="font-size: 14px; font-weight: normal; color: #888;">($1)</span>');
        
        menuContainer.appendChild(titleDiv);
        const gridDiv = document.createElement("div"); gridDiv.className = "drink-grid";
        drinks.forEach(drink => {
            const btn = document.createElement("button"); btn.className = "drink-btn";
            
            // ✨ 魔法 2：讓「飲料按鈕」的括號自動換行並變小
            btn.innerHTML = drink.name.replace(/ \((.*?)\)/, '<br><span style="font-size: 14px; font-weight: normal; color: #757575;">($1)</span>');
            
            btn.onclick = () => selectDrink(drink); 
            gridDiv.appendChild(btn);
        });
        menuContainer.appendChild(gridDiv);
    }

    document.getElementById("raffleBtn").style.backgroundColor = shopData.color;

    showPage("page-menu");
}

function startDrinkRaffle() {
    if (isRaffleActive) return;

    const drinkButtons = Array.from(document.querySelectorAll("#menuContainer .drink-btn"));
    if (!drinkButtons.length) return;

    isRaffleActive = true;
    const overlay = document.getElementById("raffleOverlay");
    const message = document.getElementById("raffleMessage");
    const duration = 4000;
    const startTime = performance.now();
    let selectedButton = null;

    overlay.hidden = false;
    message.innerText = "🎲 正在抽籤\n4...";

    function jumpToDrink() {
        const elapsed = performance.now() - startTime;
        if (elapsed >= duration) {
            message.innerText = `🎉 抽中「${selectedButton.innerText.replace(/\s+/g, " ").trim()}」！點擊畫面結束閃爍`;
            raffleTimeout = null;
            return;
        }

        // 同步更新倒數秒數
        message.innerText = `🎲 正在抽籤\n${Math.ceil((duration - elapsed) / 1000)}...`;

        if (selectedButton) selectedButton.classList.remove("raffle-highlight");
        let nextIndex = Math.floor(Math.random() * drinkButtons.length);
        if (drinkButtons.length > 1 && drinkButtons[nextIndex] === selectedButton) {
            nextIndex = (nextIndex + 1) % drinkButtons.length;
        }
        selectedButton = drinkButtons[nextIndex];
        selectedButton.classList.add("raffle-highlight");
        selectedButton.scrollIntoView({ behavior: "smooth", block: "center" });

        // 固定每 0.4 秒跳一次
        const delay = Math.min(400, duration - elapsed);
        raffleTimeout = window.setTimeout(jumpToDrink, delay);
    }

    jumpToDrink();
}

function stopDrinkRaffle() {
    if (!isRaffleActive) return;

    window.clearTimeout(raffleTimeout);
    raffleTimeout = null;
    isRaffleActive = false;
    document.querySelectorAll("#menuContainer .raffle-highlight").forEach(button => {
        button.classList.remove("raffle-highlight");
    });
    document.getElementById("raffleOverlay").hidden = true;
}

// ==========================================
// 替換這段：點擊飲料進入點餐畫面的函數
// ==========================================
function selectDrink(drinkObj) {
    currentDrinkObj = drinkObj;
    const shopData = allShopsDatabase[currentShopName];
    
    // ✨ 魔法 3：點進去之後，最上面的飲料名稱也套用漂亮的換行排版
    document.getElementById("selectedDrinkName").innerHTML = drinkObj.name.replace(/ \((.*?)\)/, '<br><span style="font-size: 16px; font-weight: normal; color: #757575;">($1)</span>');
    document.getElementById("selectedDrinkName").style.color = shopData.color;
    
    const sizeSelect = document.getElementById("userSize"); 
    sizeSelect.innerHTML = "";
    
    for(let size in drinkObj.prices) {
        let isSelected = (size.includes("L") || size.includes("大杯")) ? "selected" : "";
        sizeSelect.innerHTML += `<option value="${size}" data-price="${drinkObj.prices[size]}" ${isSelected}>${size} ($${drinkObj.prices[size]})</option>`;
    }
    
    updateToppingList();
    document.getElementById("selectedDrinkPrice").innerText = "請選擇規格與配料";
    updateQuotaInfo();
    
    showPage("page-order");
    window.scrollTo(0, 0);
}

// ==========================================
// 5. 送出訂單與查看總表 (透過 API)
// ==========================================
// 在 app.js 的最上方 (變數宣告區)，多加上這兩個用來暫存訂單的變數：
let pendingOrderData = null;

// ==========================================
// 替換區：全新的送出邏輯 (分成攔截、關閉、確定傳送三步驟)
// ==========================================

// 1. 攔截訂單，顯示確認小視窗
function submitOrder() {
    const name = document.getElementById("userName").value;
    if (!name) { alert("請選擇名字！"); return; }
    
    const sizeSelect = document.getElementById("userSize");
    const toppingSelect = document.getElementById("userTopping");
    const drinkPrice = parseFloat(sizeSelect.options[sizeSelect.selectedIndex].getAttribute("data-price"));
    const toppingPrice = parseFloat(toppingSelect.options[toppingSelect.selectedIndex].getAttribute("data-price"));
    const totalPrice = drinkPrice + toppingPrice;
    
    let diff = quotaValue > 0 ? Math.max(0, totalPrice - quotaValue) : 0;
    
    // 暫存要送給 GAS 的訂單資料
    pendingOrderData = { 
        name: name, drink: currentDrinkObj.name, size: sizeSelect.value, 
        sugar: document.getElementById("userSugar").value, ice: document.getElementById("userIce").value, 
        topping: toppingSelect.value, totalPrice: totalPrice, diff: diff > 0 ? diff : "" 
    };

    // 漂亮地排版確認畫面的內容
    const detailsHtml = `
        👤 <b>訂購人：</b> ${pendingOrderData.name}<br>
        🥤 <b>飲品：</b> ${pendingOrderData.drink}<br>
        📏 <b>容量：</b> ${pendingOrderData.size}<br>
        🍬 <b>甜度：</b> ${pendingOrderData.sugar}<br>
        🧊 <b>冰塊：</b> ${pendingOrderData.ice}<br>
        🍯 <b>配料：</b> ${pendingOrderData.topping}<br>
        <hr style="border: 0; border-top: 1px dashed #ccc; margin: 15px 0;">
        <div class="modal-total-price">💰 <b>總金額：</b> $${pendingOrderData.totalPrice}</div>
        ${diff > 0 
            ? `<div class="modal-diff-price diff-danger">⚠️ 需補差額：$${diff}</div>` 
            : `<div class="modal-diff-price diff-safe">✅ 無須補差額</div>`}
    `;
    
    // 把文字塞進去，並把隱藏的彈窗叫出來
    document.getElementById("confirmOrderDetails").innerHTML = detailsHtml;
    document.getElementById("confirmModal").style.display = "flex";
}

// 2. 按下「重選」時，關閉視窗
function closeConfirmModal() {
    document.getElementById("confirmModal").style.display = "none";
}

// 3. 按下「確認訂購」時，真正呼叫 API 送出資料
async function executeSubmitOrder() {
    if (!pendingOrderData) return;

    const finalSubmitBtn = document.getElementById("finalSubmitBtn");
    finalSubmitBtn.disabled = true;
    finalSubmitBtn.innerText = "⏳ 傳送中...";
    
    try {
        // 送出前先查詢試算表，若此人已有訂購飲料，需再次確認是否重新訂購
        const existing = await findExistingOrder(pendingOrderData.name);
        if (existing) {
            showReorderModal(existing);
            finalSubmitBtn.disabled = false;
            finalSubmitBtn.innerText = "✅ 確認訂購";
            return;
        }
        await doSubmitOrder();
    } catch(e) {
        alert("傳送失敗：" + e.message);
        finalSubmitBtn.disabled = false;
        finalSubmitBtn.innerText = "✅ 確認訂購";
    }
}

// 在試算表資料中尋找該姓名已訂購的飲料（飲料欄位非空才算已訂購）
async function findExistingOrder(name) {
    const data = await apiGet('getOrderData');
    for (let i = 1; i < data.length; i++) {
        const row = data[i];
        if ((row[0] || "").toString().trim() === name.trim() && (row[1] || "").toString().trim() !== "") {
            return row;
        }
    }
    return null;
}

function showReorderModal(row) {
    const cell = v => escapeHtml((v === undefined || v === null || v === "") ? "-" : v);
    document.getElementById("reorderDetails").innerHTML = `
        <div style="margin-bottom:10px;">你已經訂購過飲料了：</div>
        🥤 <b>飲品：</b> ${cell(row[1])}<br>
        📏 <b>容量：</b> ${cell(row[2])}<br>
        🍬 <b>甜度：</b> ${cell(row[3])}<br>
        🧊 <b>冰塊：</b> ${cell(row[4])}<br>
        🍯 <b>配料：</b> ${cell(row[5])}<br>
        <div style="margin-top:10px;">確定要重新訂購並覆蓋嗎？</div>`;
    document.getElementById("reorderModal").style.display = "flex";
}

function closeReorderModal() {
    document.getElementById("reorderModal").style.display = "none";
}

async function confirmReorder() {
    closeReorderModal();
    const finalSubmitBtn = document.getElementById("finalSubmitBtn");
    finalSubmitBtn.disabled = true;
    finalSubmitBtn.innerText = "⏳ 傳送中...";
    await doSubmitOrder();
}

// 真正送出訂單
async function doSubmitOrder() {
    const finalSubmitBtn = document.getElementById("finalSubmitBtn");
    try {
        await apiPost('addOrder', { orderData: pendingOrderData });
        showOrderSuccess();
        closeConfirmModal();
        finalSubmitBtn.disabled = false;
        finalSubmitBtn.innerText = "✅ 確認訂購";
    } catch(e) {
        alert("傳送失敗：" + e.message);
        finalSubmitBtn.disabled = false;
        finalSubmitBtn.innerText = "✅ 確認訂購";
    }
}

function showOrderSuccess() {
    const order = pendingOrderData;
    document.getElementById("successOrderDetails").innerHTML = `
        👤 <b>訂購人：</b> ${escapeHtml(order.name)}<br>
        🥤 <b>飲品：</b> ${escapeHtml(order.drink)}<br>
        📏 <b>容量：</b> ${escapeHtml(order.size)}<br>
        🍬 <b>甜度：</b> ${escapeHtml(order.sugar)}<br>
        🧊 <b>冰塊：</b> ${escapeHtml(order.ice)}<br>
        🍯 <b>配料：</b> ${escapeHtml(order.topping)}<br>
        <div class="success-total-price">💰 總金額：$${order.totalPrice}</div>
    `;

    const diffNotice = document.getElementById("successDiffNotice");
    if (order.diff) {
        diffNotice.innerText = `⚠️ 請補上 ${order.diff} 元至股務基金，感謝！`;
        diffNotice.style.display = "block";
    } else {
        diffNotice.style.display = "none";
    }

    showPage("page-success");
    updateHeaderForPage("page-success");
    pendingOrderData = null;
}

async function loadSummary() {
    showPage("page-summary");
    document.getElementById("appHeader").innerText = "訂購單總表";
    document.getElementById("appHeader").style.background = "#00796B";
    document.getElementById("summaryLoading").style.display = "block";
    document.getElementById("summaryTableWrapper").style.display = "none";
    
    try {
        const data = await apiGet('getOrderData');
        renderSummaryTable(data);
    } catch(e) {
        document.getElementById("summaryLoading").innerText = "資料載入失敗。";
        console.error(e);
    }
}

function renderSummaryTable(data) {
    document.getElementById("summaryLoading").style.display = "none";
    originalSummaryData = data; 
    isSortedByItem = false;     
    updateSortButtonText();
    drawTable(data);            
}

function drawTable(data) {
    const wrapper = document.getElementById("summaryTableWrapper");
    wrapper.style.display = "block";
    
    if (!data || data.length === 0) { 
        wrapper.innerHTML = "目前沒資料"; 
        return; 
    }
    
    let html = "<table><thead><tr>";
    
    // 🛡️ 防護 1：連表頭（通常是試算表第一列）也送進 escapeHtml 進行消毒
    data[0].forEach(h => html += `<th>${escapeHtml(h)}</th>`);
    html += "</tr></thead><tbody>";
    
    for (let i = 1; i < data.length; i++) {
        if (data[i].join("").trim() === "") continue;
        html += "<tr>";
        
        // 🛡️ 防護 2：將每一格的內容先通過 escapeHtml 檢查，如果是空的則補上 "-"
        data[i].forEach(cell => {
            let safeText = escapeHtml(cell || "-");
            html += `<td>${safeText}</td>`;
        });
        
        html += "</tr>";
    }
    wrapper.innerHTML = html + "</tbody></table>";
    
// ✅ 穩定版：每次畫完表格，直接依照變數狀態強硬套用/移除樣式
    const wrapperElement = document.getElementById("summaryTableWrapper");
    if (isCompactView) {
        wrapperElement.classList.add("compact-view");
    } else {
        wrapperElement.classList.remove("compact-view");
    }
    updateToggleButtonText();
}

function toggleSortView() {
    if (!originalSummaryData || originalSummaryData.length <= 2) return; 
    
    isSortedByItem = !isSortedByItem;
    updateSortButtonText();

    if (isSortedByItem) {
        let header = originalSummaryData[0];
        let lastRowIndex = originalSummaryData.length - 1;
        let lastRow = originalSummaryData[lastRowIndex]; 
        let dataRows = [...originalSummaryData].slice(1, lastRowIndex);
        
        dataRows.sort((a, b) => {
            for (let i = 1; i < header.length; i++) {
                let valA = String(a[i] || "");
                let valB = String(b[i] || "");
                let cmp = valA.localeCompare(valB, 'zh-Hant');
                if (cmp !== 0) return cmp; 
            }
            return 0; 
        });
        
        drawTable([header, ...dataRows, lastRow]); 
    } else {
        drawTable(originalSummaryData); 
    }
}

function updateSortButtonText() {
    let btn = document.getElementById("toggleSortBtn");
    if (btn) btn.innerText = isSortedByItem ? "🔄 恢復預設排序" : "📑 依品項排序";
}

// ==========================================
// 6. UI 切換工具
// ==========================================
function showPage(id, updateHistory = true) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    if (updateHistory) {
        window.history.pushState({ pageId: id }, "", window.location.href);
    }
    window.scrollTo(0,0);
}
function updateHeaderForPage(pageId) {
    const header = document.getElementById("appHeader");
    if (pageId === "page-directory") {
        header.innerText = "手銲股訂飲料";
        header.style.background = "#333";
    } else if (pageId === "page-summary") {
        header.innerText = "訂購單總表";
        header.style.background = "#00796B";
    } else if (pageId === "page-success") {
        header.innerText = "訂購完成";
        header.style.background = "#27AE60";
    } else if (pageId === "page-ai-call") {
        header.innerText = "📞 AI 電話訂飲料";
        header.style.background = "#2563EB";
        return;
        header.style.background = "#27AE60";
    } else if (pageId === "page-menu" || pageId === "page-order") {
        const shopData = allShopsDatabase[currentShopName];
        if (shopData) {
            header.innerText = currentShopName;
            header.style.background = shopData.color;
        }
    }
}
function goToDirectory() {
    showPage("page-directory");
    updateHeaderForPage("page-directory");
}
function goToMenu() {
    if (window.history.state?.pageId === "page-order") {
        window.history.back();
    } else {
        showPage("page-menu");
        updateHeaderForPage("page-menu");
    }
}
window.history.replaceState({ ...(window.history.state || {}), pageId: "page-directory" }, "", window.location.href);
window.addEventListener("popstate", event => {
    const pageId = event.state?.pageId;
    if (!pageId || !document.getElementById(pageId)) return;
    showPage(pageId, false);
    updateHeaderForPage(pageId);
});
function toggleTableView() { 
    isCompactView = !isCompactView; 
    const wrapperElement = document.getElementById("summaryTableWrapper");
    if (isCompactView) {
        wrapperElement.classList.add("compact-view");
    } else {
        wrapperElement.classList.remove("compact-view");
    }
    updateToggleButtonText(); 
}
function updateToggleButtonText() { document.getElementById("toggleViewBtn").innerText = isCompactView ? "↔️ 放大檢視" : "🔍 縮小檢視"; }


// ==========================================
// 7. 📞 AI 電話訂購頁面邏輯
// ==========================================
let aiAggregatedItems = [];
let aiEstimatedTotalPrice = 0;
let aiTotalCups = 0;

async function openAiCallPage() {
    showPage("page-ai-call");
    updateHeaderForPage("page-ai-call");

    loadSavedPickupInfo();

    const shopInput = document.getElementById("aiShopName");
    if (shopInput && !shopInput.value.trim() && activeShop) {
        shopInput.value = activeShop;
    }

    await refreshAiOrderList();
}

async function refreshAiOrderList() {
    const loadingEl = document.getElementById("aiOrderLoading");
    const statsEl = document.getElementById("aiOrderSummaryStats");
    const listEl = document.getElementById("aiAggregatedList");

    loadingEl.style.display = "block";
    loadingEl.innerText = "正在彙整目前訂單... ⏳";
    statsEl.style.display = "none";
    listEl.style.display = "none";

    try {
        const data = await apiGet("getOrderData");
        aggregateOrderItems(data);
        renderAiOrderList();
        updateAiScriptPreview();
    } catch (e) {
        loadingEl.innerText = "訂單讀取失敗：" + e.message;
        console.error(e);
    }
}

function aggregateOrderItems(rows) {
    aiAggregatedItems = [];
    aiEstimatedTotalPrice = 0;
    aiTotalCups = 0;

    if (!rows || rows.length <= 1) return;

    const map = new Map();

    // 第 0 列為表頭 [姓名, 飲品, 容量, 甜度, 冰塊, 配料, 總金額, 差額]
    for (let i = 1; i < rows.length; i++) {
        const row = rows[i];
        const personName = String(row[0] || "").trim();
        const drink = String(row[1] || "").trim();
        const size = String(row[2] || "").trim();
        const sugar = String(row[3] || "").trim();
        const ice = String(row[4] || "").trim();
        const topping = String(row[5] || "").trim();
        const price = parseFloat(row[6]);

        // 過濾空白列或試算表最底部的「總計/合計」列
        if (!drink) continue;
        if (personName.includes("總計") || personName.includes("合計") || drink.includes("總計")) continue;
        if (!size && !sugar && !ice) continue;

        const cleanTopping = (!topping || topping === "無" || topping === "-") ? "無配料" : `加${topping}`;
        const key = `${drink}|${size}|${sugar}|${ice}|${cleanTopping}`;

        if (!map.has(key)) {
            map.set(key, {
                drink,
                size,
                sugar,
                ice,
                topping: cleanTopping,
                count: 0,
                subtotal: 0,
                orderers: []
            });
        }

        const item = map.get(key);
        item.count += 1;
        if (!isNaN(price)) {
            item.subtotal += price;
            aiEstimatedTotalPrice += price;
        }
        if (personName) item.orderers.push(personName);
        aiTotalCups += 1;
    }

    aiAggregatedItems = Array.from(map.values()).sort((a, b) => {
        const cmp = a.drink.localeCompare(b.drink, "zh-Hant");
        return cmp !== 0 ? cmp : b.count - a.count;
    });
}

function renderAiOrderList() {
    const loadingEl = document.getElementById("aiOrderLoading");
    const statsEl = document.getElementById("aiOrderSummaryStats");
    const listEl = document.getElementById("aiAggregatedList");

    loadingEl.style.display = "none";
    statsEl.style.display = "flex";
    listEl.style.display = "block";

    statsEl.innerHTML = `
        <span>🥤 總杯數：${aiTotalCups} 杯</span>
        <span>💰 試算表預估金額：$${aiEstimatedTotalPrice}</span>
    `;

    if (aiAggregatedItems.length === 0) {
        listEl.innerHTML = `<div style="text-align:center; color:#888; padding: 12px;">目前尚無已點飲料資料</div>`;
        return;
    }

    listEl.innerHTML = aiAggregatedItems.map(item => {
        const toppingBadge = item.topping === "無配料" ? "" : ` / ${escapeHtml(item.topping)}`;
        const specText = `${escapeHtml(item.size)} / ${escapeHtml(item.sugar)} / ${escapeHtml(item.ice)}${toppingBadge}`;
        return `
            <div class="ai-drink-item">
                <div>
                    <div style="font-weight: bold; color: #111827;">${escapeHtml(item.drink)}</div>
                    <div style="font-size: 13px; color: #4b5563;">${specText} <span style="color:#9ca3af;">(${escapeHtml(item.orderers.join("、"))})</span></div>
                </div>
                <span class="ai-drink-count">x ${item.count} 杯</span>
            </div>
        `;
    }).join("");
}

function buildSpokenDrinkLines() {
    if (aiAggregatedItems.length === 0) {
        return ["（目前尚無飲料明細，請先點餐或重新整理）"];
    }
    return aiAggregatedItems.map((item, idx) => {
        const cleanDrinkName = item.drink.replace(/\s*\(.*?\)/g, "");
        const cleanSize = item.size.includes("L") || item.size.includes("大") ? "大杯"
                        : item.size.includes("M") || item.size.includes("中") ? "中杯"
                        : item.size;
        const toppingPart = item.topping === "無配料" ? "" : `、${item.topping}`;
        return `${idx + 1}. ${cleanDrinkName}，${cleanSize}，${item.sugar}${item.ice}${toppingPart}，${item.count} 杯`;
    });
}

function updateAiScriptPreview() {
    const shopName = document.getElementById("aiShopName")?.value.trim() || activeShop || "店家";
    const callerPhone = document.getElementById("aiCallerPhone")?.value.trim() || "（未填電話）";
    const callerName = document.getElementById("aiCallerName")?.value.trim() || "我們";
    const voiceSelect = document.getElementById("aiVoiceSelect");
    const voiceText = voiceSelect ? voiceSelect.options[voiceSelect.selectedIndex]?.text : "Aoede（女聲）";
    const reqPrice = document.getElementById("reqAskPrice")?.checked;
    const reqBag = document.getElementById("reqNeedBag")?.checked;
    const reqTime = document.getElementById("reqAskPickupTime")?.checked;
    const pickupTimeInput = document.getElementById("aiPickupTime")?.value.trim() || "20分鐘後";
    const customNotes = document.getElementById("aiCustomNotes")?.value.trim() || "";

    const spokenLines = buildSpokenDrinkLines();
    const bagPart = reqBag ? "我們需要買塑膠袋裝起來，" : "";
    const notesPart = customNotes ? `另外補充需求：${customNotes}。` : "";

    const scriptText = [
        `【AI 撥通「${shopName}」後的對話目標】（音色：${voiceText}）`,
        `🗣️ 開場白：「你好，我要電話訂飲料自取，總共 ${aiTotalCups} 杯，我一個品項一個品項慢慢唸給您聽好嗎？」`,
        `   👉 ⚠️【AI 停下來等待】：等店員回答「好，請說」或「可以」後，才開始唸第 1 個品項`,
        ``,
        `📋 依序報單（一次念 1 個品項，等店員出聲才念下一個；同品項多杯時會說「再來是... X杯」，絕不說「下一杯X杯」）：`,
        ...spokenLines.map(line => `   ${line}`),
        ``,
        `💬 結尾確認與留資料（從容互動，絕不急躁）：`,
        `   1. 【問總金額】：AI 說：「以上總共 ${aiTotalCups} 杯。${notesPart}${bagPart}請問這樣總共多少錢？」（預估約 $${aiEstimatedTotalPrice} 元）`,
        `      👉 ⚠️【AI 停下來等待】：等店員出聲回答具體總金額`,
        `   2. 【告知時間、留資料並詢問確認】：店員報完金額後，AI 說：「好的，那我們大概預計【${reqTime ? pickupTimeInput : "待會"}】過去拿，取餐人稱呼是 ${callerName}，聯絡電話是 ${callerPhone}。請問內容這樣都清楚、有需要再確認哪一杯嗎？」`,
        `      👉 ⚠️【AI 停下來等待】：等店員回答「都清楚了」或核對完畢`,
        `   3. 【禮貌道謝道別】：店員確認無誤後，AI 說：「好的，非常感謝你，那我們待會過去拿，謝謝拜拜！」`
    ].join("\n");

    const previewEl = document.getElementById("aiScriptPreview");
    if (previewEl) previewEl.innerText = scriptText;
}

function savePickupInfo() {
    const data = {
        shopName: document.getElementById("aiShopName")?.value || "",
        shopPhone: document.getElementById("aiShopPhone")?.value || "",
        callerPhone: document.getElementById("aiCallerPhone")?.value || "",
        callerName: document.getElementById("aiCallerName")?.value || "",
        voiceName: document.getElementById("aiVoiceSelect")?.value || "Aoede",
        pickupExpectTime: document.getElementById("aiPickupTime")?.value || "20分鐘後",
        totalAmount: document.getElementById("pickupTotalAmount")?.value || "",
        pickupTime: document.getElementById("pickupTime")?.value || "",
        bagInfo: document.getElementById("pickupBagInfo")?.value || "",
        orderNo: document.getElementById("pickupOrderNo")?.value || ""
    };
    try {
        localStorage.setItem("beverage_ai_pickup_info", JSON.stringify(data));
        const badge = document.getElementById("pickupSavedBadge");
        if (badge) {
            badge.style.display = "inline-block";
            setTimeout(() => { badge.style.display = "none"; }, 1500);
        }
    } catch (e) {
        console.warn("無法儲存至 localStorage:", e);
    }
}

function loadSavedPickupInfo() {
    try {
        const raw = localStorage.getItem("beverage_ai_pickup_info");
        if (!raw) return;
        const data = JSON.parse(raw);
        if (data.shopName && !document.getElementById("aiShopName").value) {
            document.getElementById("aiShopName").value = data.shopName;
        }
        if (data.shopPhone) document.getElementById("aiShopPhone").value = data.shopPhone;
        if (data.callerPhone) document.getElementById("aiCallerPhone").value = data.callerPhone;
        if (data.callerName) document.getElementById("aiCallerName").value = data.callerName;
        if (data.totalAmount) document.getElementById("pickupTotalAmount").value = data.totalAmount;
        if (data.pickupTime) document.getElementById("pickupTime").value = data.pickupTime;
        if (data.bagInfo) document.getElementById("pickupBagInfo").value = data.bagInfo;
        if (data.orderNo) document.getElementById("pickupOrderNo").value = data.orderNo;
        if (data.voiceName && document.getElementById("aiVoiceSelect")) {
            document.getElementById("aiVoiceSelect").value = data.voiceName;
        }
        if (data.pickupExpectTime && document.getElementById("aiPickupTime")) {
            document.getElementById("aiPickupTime").value = data.pickupExpectTime;
        }
    } catch (e) {
        console.warn("讀取儲存的取餐資訊失敗:", e);
    }
}

function copyPickupSummary() {
    const shopName = document.getElementById("aiShopName")?.value.trim() || activeShop || "飲料店";
    const shopPhone = document.getElementById("aiShopPhone")?.value.trim() || "-";
    const callerName = document.getElementById("aiCallerName")?.value.trim() || "-";
    const callerPhone = document.getElementById("aiCallerPhone")?.value.trim() || "-";
    const totalAmount = document.getElementById("pickupTotalAmount")?.value.trim() || `預估 $${aiEstimatedTotalPrice}`;
    const pickupTime = document.getElementById("pickupTime")?.value.trim() || "待確認";
    const bagInfo = document.getElementById("pickupBagInfo")?.value.trim() || "無";
    const orderNo = document.getElementById("pickupOrderNo")?.value.trim() || "-";

    const text = [
        `🥤 【手銲股取飲料通知】`,
        `🏪 店家：${shopName} (${shopPhone})`,
        `📦 總杯數：共 ${aiTotalCups} 杯`,
        `💰 總金額：${totalAmount}`,
        `🛍️ 塑膠袋：${bagInfo}`,
        `🕒 取餐時間：${pickupTime}`,
        `👤 訂購聯絡人：${callerName} (${callerPhone})`,
        `🔢 備註/取餐號：${orderNo}`
    ].join("\n");

    navigator.clipboard.writeText(text).then(() => {
        alert("✅ 已複製取餐資訊！可以直接貼到 LINE 群組囉：\n\n" + text);
    }).catch(() => {
        prompt("請手動複製以下取餐資訊：", text);
    });
}

async function simulateAiPhoneCall() {
    const statusBox = document.getElementById("aiCallStatusBox");
    const simBtn = document.getElementById("simulateAiCallBtn");
    const shopName = document.getElementById("aiShopName")?.value.trim() || activeShop || "50嵐";
    const shopPhone = document.getElementById("aiShopPhone")?.value.trim() || "07-801xxxx";
    const callerPhone = document.getElementById("aiCallerPhone")?.value.trim() || "0912-345-678";
    const callerName = document.getElementById("aiCallerName")?.value.trim() || "陳先生";
    const needBag = document.getElementById("reqNeedBag")?.checked;

    simBtn.disabled = true;
    statusBox.style.display = "block";
    statusBox.innerHTML = `📡 <b>[模擬模式]</b> 正在撥號至 <b>${escapeHtml(shopName)} (${escapeHtml(shopPhone)})</b>...`;

    const sleep = ms => new Promise(r => setTimeout(r, ms));
    await sleep(1200);
    statusBox.innerHTML += `<br>🔔 店員接聽：「喂你好，${escapeHtml(shopName)}請問要點什麼？」`;
    await sleep(1300);
    statusBox.innerHTML += `<br>🤖 AI 助理：「你好，我要訂飲料自取，總共 ${aiTotalCups} 杯。${escapeHtml(buildSpokenDrinkLines().join("；"))}。」`;
    await sleep(1500);
    const bagFee = needBag ? (aiTotalCups > 6 ? 4 : 2) : 0;
    const finalSimulatedPrice = aiEstimatedTotalPrice + bagFee;
    statusBox.innerHTML += `<br>🤖 AI 助理：「${needBag ? "我們要買塑膠袋，" : ""}請問這樣總共多少錢？幾點可以過去拿？」`;
    await sleep(1500);

    const pickupDate = new Date(Date.now() + 20 * 60000);
    const pickupTimeStr = `${String(pickupDate.getHours()).padStart(2, "0")}:${String(pickupDate.getMinutes()).padStart(2, "0")}`;

    statusBox.innerHTML += `<br>👩‍🍳 店員：「袋子幫你算 ${bagFee} 元喔，這樣總共 ${finalSimulatedPrice} 元，大概 20 分鐘後、${pickupTimeStr} 可以來拿，請問貴姓電話？」`;
    await sleep(1300);
    statusBox.innerHTML += `<br>🤖 AI 助理：「好，我姓${escapeHtml(callerName.charAt(0))}，電話是 ${escapeHtml(callerPhone)}，待會過去拿，謝謝！」`;
    await sleep(800);
    statusBox.innerHTML += `<br><span style="color:#16a34a; font-weight:bold;">✅ 通話結束！AI 已自動將取飲料資訊填入下方紀錄卡。</span>`;

    document.getElementById("pickupTotalAmount").value = `$${finalSimulatedPrice}${needBag ? ` (含塑膠袋 $${bagFee})` : ""}`;
    document.getElementById("pickupTime").value = `${pickupTimeStr} (約 20 分鐘後)`;
    document.getElementById("pickupBagInfo").value = needBag ? `已加購塑膠袋 (+$${bagFee})` : "不需塑膠袋";
    document.getElementById("pickupOrderNo").value = `留電 ${callerPhone} (${callerName})`;
    savePickupInfo();
    simBtn.disabled = false;
}

async function startAiPhoneCall() {
    const shopName = document.getElementById("aiShopName")?.value.trim();
    const shopPhone = document.getElementById("aiShopPhone")?.value.trim();
    const callerPhone = document.getElementById("aiCallerPhone")?.value.trim();
    const callerName = document.getElementById("aiCallerName")?.value.trim() || "先生/小姐";

    if (!shopName) { alert("請輸入店家名稱！"); return; }
    if (!shopPhone) { alert("請輸入店家電話！"); return; }
    if (!callerPhone) { alert("請輸入發話 / 取餐聯絡電話！"); return; }
    if (aiAggregatedItems.length === 0) {
        if (!confirm("目前尚未偵測到已點飲料明細，確定仍要撥出嗎？")) return;
    }

    const startBtn = document.getElementById("startAiCallBtn");
    const statusBox = document.getElementById("aiCallStatusBox");
    startBtn.disabled = true;
    startBtn.innerText = "⏳ 正在請求後端發起 AI 通話...";
    statusBox.style.display = "block";
    statusBox.innerHTML = "📡 正在傳送訂單與撥號指令至 Cloud Run 後端...";

    const payload = {
        shopName,
        shopPhone,
        callerPhone,
        callerName,
        voiceName: document.getElementById("aiVoiceSelect")?.value || "Aoede",
        pickupTime: document.getElementById("aiPickupTime")?.value.trim() || "20分鐘後",
        totalCups: aiTotalCups,
        estimatedPrice: aiEstimatedTotalPrice,
        items: aiAggregatedItems,
        spokenLines: buildSpokenDrinkLines(),
        requirements: {
            askPrice: document.getElementById("reqAskPrice")?.checked,
            needBag: document.getElementById("reqNeedBag")?.checked,
            askPickupTime: document.getElementById("reqAskPickupTime")?.checked,
            customNotes: document.getElementById("aiCustomNotes")?.value.trim() || ""
        }
    };

    try {
        const res = await apiPost("startAiPhoneCall", { callData: payload });
        statusBox.innerHTML = `✅ 後端已透過 Twilio + Gemini Live 發起撥號！<br>🆔 通話 ID：<code>${escapeHtml(res.callId || "N/A")}</code><br>📞 目標號碼：<b>${escapeHtml(res.e164ShopPhone || shopPhone)}</b><br>⏳ 正在即時追蹤通話狀態...`;
        if (res.callId) {
            pollAiCallStatus(res.callId);
        } else {
            startBtn.disabled = false;
            startBtn.innerText = "📞 請 AI 立即打電話訂飲料";
        }
    } catch (e) {
        statusBox.innerHTML = `⚠️ <b>無法發起真實撥號</b>：${escapeHtml(e.message)}<br>👉 請確認 Cloud Run 環境變數已設定 <code>GEMINI_API_KEY</code>、<code>TWILIO_ACCOUNT_SID</code>、<code>TWILIO_AUTH_TOKEN</code> 與 <code>TWILIO_FROM_NUMBER</code>，或先點擊上方的「🧪 模擬通話測試」預覽流程！`;
        startBtn.disabled = false;
        startBtn.innerText = "📞 請 AI 立即打電話訂飲料";
    }
}

let aiCallPollTimer = null;

async function pollAiCallStatus(callId) {
    if (aiCallPollTimer) clearTimeout(aiCallPollTimer);
    const statusBox = document.getElementById("aiCallStatusBox");
    const startBtn = document.getElementById("startAiCallBtn");

    const statusMap = {
        "queued": "⏳ 排隊撥號中 (queued)",
        "ringing": "🔔 店家電話響鈴中 (ringing)...",
        "in-progress": "🗣️ AI 正在與店員通話點餐中 (in-progress)...",
        "forwarding": "🔀 轉接中 (forwarding)...",
        "ended": "✅ 通話已結束 (ended)"
    };

    try {
        const info = await apiPost("getAiCallStatus", { callId });
        const statusText = statusMap[info.status] || info.status;

        let html = `<div><b>📡 通話狀態：</b>${escapeHtml(statusText)}</div>`;
        if (info.transcript) {
            html += `<div style="margin-top:8px; padding:8px; background:#fff; border:1px solid #e2e8f0; border-radius:6px; max-height:180px; overflow-y:auto; white-space:pre-wrap; font-size:13px;"><b>📝 即時逐字稿：</b>\n${escapeHtml(info.transcript)}</div>`;
        }
        if (info.summary) {
            html += `<div style="margin-top:8px; color:#1e40af;"><b>💡 AI 通話摘要：</b>${escapeHtml(info.summary)}</div>`;
        }
        if (info.recordingUrl) {
            html += `<div style="margin-top:8px;"><audio controls src="${escapeHtml(info.recordingUrl)}" style="width:100%; height:36px;"></audio></div>`;
        }

        statusBox.innerHTML = html;

        if (info.status === "ended") {
            startBtn.disabled = false;
            startBtn.innerText = "📞 請 AI 立即打電話訂飲料";

            const p = info.pickupInfo || {};
            if (p.totalAmount) document.getElementById("pickupTotalAmount").value = p.totalAmount;
            if (p.pickupTime) document.getElementById("pickupTime").value = p.pickupTime;
            if (p.bagInfo) document.getElementById("pickupBagInfo").value = p.bagInfo;
            if (p.orderNotes) document.getElementById("pickupOrderNo").value = p.orderNotes;
            savePickupInfo();

            statusBox.innerHTML += `<div style="margin-top:8px; color:#16a34a; font-weight:bold;">🎉 已自動從通話結果擷取取餐資訊並填入下方紀錄卡！</div>`;
            return;
        }
    } catch (err) {
        console.warn("輪詢通話狀態失敗:", err);
    }

    aiCallPollTimer = setTimeout(() => pollAiCallStatus(callId), 3000);
}


// ==========================================
// 🎙️ 網頁語音實測：用電腦麥克風直接跟 Gemini Live AI 對話（你扮演店員，免 Twilio 電話費）
// ==========================================
let vtState = null;
const VT_METER_HTML = `<div style="margin-top:6px; font-size:12px;">🎤 麥克風音量（講話時應會跳動）：<div style="height:8px; background:#e2e8f0; border-radius:4px;"><div id="vtMeter" style="height:8px; width:0; background:#16a34a; border-radius:4px;"></div></div></div>`;

function vtBuildPayload() {
    return {
        shopName: document.getElementById("aiShopName")?.value.trim() || activeShop || "飲料店",
        shopPhone: document.getElementById("aiShopPhone")?.value.trim() || "",
        callerPhone: document.getElementById("aiCallerPhone")?.value.trim() || "0912-345-678",
        callerName: document.getElementById("aiCallerName")?.value.trim() || "陳先生",
        voiceName: document.getElementById("aiVoiceSelect")?.value || "Aoede",
        pickupTime: document.getElementById("aiPickupTime")?.value.trim() || "20分鐘後",
        totalCups: aiTotalCups,
        estimatedPrice: aiEstimatedTotalPrice,
        items: aiAggregatedItems,
        spokenLines: buildSpokenDrinkLines(),
        requirements: {
            askPrice: document.getElementById("reqAskPrice")?.checked,
            needBag: document.getElementById("reqNeedBag")?.checked,
            askPickupTime: document.getElementById("reqAskPickupTime")?.checked,
            customNotes: document.getElementById("aiCustomNotes")?.value.trim() || ""
        }
    };
}

async function toggleVoiceTest() {
    if (vtState) { stopVoiceTest(); return; }

    const btn = document.getElementById("voiceTestBtn");
    const statusBox = document.getElementById("aiCallStatusBox");
    statusBox.style.display = "block";

    if (!navigator.mediaDevices?.getUserMedia) {
        statusBox.innerHTML = "⚠️ 此瀏覽器不支援麥克風（需 https 或 localhost）。";
        return;
    }

    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 }
        });
    } catch (e) {
        statusBox.innerHTML = `⚠️ 無法使用麥克風：${escapeHtml(e.message)}（請允許瀏覽器麥克風權限）`;
        return;
    }

    btn.innerText = "⏳ 連線 AI 中...";
    statusBox.innerHTML = "🔌 正在連線 Cloud Run + Gemini Live...";

    const wsUrl = GAS_API_URL.replace(/^http/, "ws").replace(/\/$/, "") + "/voice-test-stream";
    const ws = new WebSocket(wsUrl);
    const AC = window.AudioContext || window.webkitAudioContext;
    const inCtx = new AC();
    const outCtx = new AC({ sampleRate: 24000 });
    const state = { ws, stream, inCtx, outCtx, sources: new Set(), nextTime: 0, ready: false, ended: false, proc: null, src: null };
    vtState = state;

    ws.onopen = () => {
        ws.send(JSON.stringify({ type: "setup", callData: vtBuildPayload() }));
    };

    ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.type === "ready") {
            state.ready = true;
            btn.innerText = "⏹️ 結束通話（點擊掛斷）";
            statusBox.innerHTML = "🟢 <b>已連線</b>！你現在是店員，請先說：「喂你好，" + escapeHtml(vtBuildPayload().shopName) + "」，AI 就會開始點餐。<br>💡 建議戴耳機避免回音。" + VT_METER_HTML;
            vtStartMic(state);
        } else if (m.type === "audio") {
            vtPlay(state, m.data);
        } else if (m.type === "interrupted") {
            vtClearPlayback(state);
        } else if (m.type === "transcript") {
            vtRenderTranscript(m.lines);
        } else if (m.type === "analyzing") {
            btn.innerText = "⏳ 正在整理取餐明細...";
            statusBox.innerHTML = `<div><b>⏳ 通話已結束</b></div><div style="margin-top:8px; color:#1e40af;">🤖 AI 正在整理通話逐字稿並自動帶入總金額與取餐卡...</div>` +
                (m.transcript ? `<div style="margin-top:8px; padding:8px; background:#fff; border:1px solid #e2e8f0; border-radius:6px; max-height:180px; overflow-y:auto; white-space:pre-wrap; font-size:13px;"><b>📝 通話逐字稿：</b>\n${escapeHtml(m.transcript)}</div>` : "");
        } else if (m.type === "error") {
            statusBox.innerHTML = `⚠️ <b>語音實測錯誤</b>：${escapeHtml(m.message)}`;
        } else if (m.type === "ended") {
            state.ended = true;
            vtFinish(m);
        }
    };

    ws.onerror = () => {
        statusBox.innerHTML = "⚠️ 無法連線至後端 WebSocket，請確認 Cloud Run 已部署最新版本。";
    };
    ws.onclose = () => {
        if (vtState === state) vtCleanup();
    };
}

function vtStartMic(state) {
    // Chrome 可能因非同步建立而讓 AudioContext 處於 suspended，必須 resume 才會有麥克風資料
    state.inCtx.resume();
    state.outCtx.resume();
    state.src = state.inCtx.createMediaStreamSource(state.stream);
    state.proc = state.inCtx.createScriptProcessor(4096, 1, 1);
    const ratio = state.inCtx.sampleRate / 16000;
    state.proc.onaudioprocess = (e) => {
        if (state.ws.readyState !== WebSocket.OPEN || state.ended) return;
        const input = e.inputBuffer.getChannelData(0);
        const outLen = Math.floor(input.length / ratio);
        const pcm = new Int16Array(outLen);
        for (let i = 0; i < outLen; i++) {
            const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)]));
            pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        let peak = 0;
        for (let i = 0; i < input.length; i += 16) peak = Math.max(peak, Math.abs(input[i]));
        const meter = document.getElementById("vtMeter");
        if (meter) meter.style.width = Math.min(100, Math.round(peak * 300)) + "%";
        const bytes = new Uint8Array(pcm.buffer);
        let bin = "";
        for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
        state.ws.send(JSON.stringify({ type: "audio", data: btoa(bin) }));
    };
    state.src.connect(state.proc);
    state.proc.connect(state.inCtx.destination);
}

function vtPlay(state, b64) {
    const bin = atob(b64);
    const len = bin.length >> 1;
    const f32 = new Float32Array(len);
    for (let i = 0; i < len; i++) {
        let v = bin.charCodeAt(i * 2) | (bin.charCodeAt(i * 2 + 1) << 8);
        if (v >= 0x8000) v -= 0x10000;
        f32[i] = v / 0x8000;
    }
    const buf = state.outCtx.createBuffer(1, len, 24000);
    buf.copyToChannel(f32, 0);
    const node = state.outCtx.createBufferSource();
    node.buffer = buf;
    node.connect(state.outCtx.destination);
    const startAt = Math.max(state.outCtx.currentTime + 0.02, state.nextTime);
    node.start(startAt);
    state.nextTime = startAt + buf.duration;
    state.sources.add(node);
    node.onended = () => state.sources.delete(node);
}

function vtClearPlayback(state) {
    state.sources.forEach(n => { try { n.stop(); } catch (e) { /* ignore */ } });
    state.sources.clear();
    state.nextTime = 0;
}

function vtRenderTranscript(lines) {
    const statusBox = document.getElementById("aiCallStatusBox");
    statusBox.innerHTML = `<div><b>🟢 通話中（你是店員，對著麥克風說話）</b></div>` + VT_METER_HTML +
        `<div style="margin-top:8px; padding:8px; background:#fff; border:1px solid #e2e8f0; border-radius:6px; max-height:220px; overflow-y:auto; white-space:pre-wrap; font-size:13px;"><b>📝 即時逐字稿：</b>\n${escapeHtml((lines || []).join("\n"))}</div>`;
}

function stopVoiceTest() {
    const state = vtState;
    if (!state) return;
    const btn = document.getElementById("voiceTestBtn");
    btn.innerText = "⏳ 整理通話結果中...";
    btn.disabled = true;
    if (state.proc) { try { state.proc.disconnect(); } catch (e) { /* ignore */ } }
    state.stream.getTracks().forEach(t => t.stop());
    vtClearPlayback(state);
    if (state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: "end" }));
    } else {
        vtCleanup();
    }
}

function vtFinish(m) {
    const statusBox = document.getElementById("aiCallStatusBox");
    let html = `<div><b>✅ 語音實測結束</b></div>`;
    if (m.transcript) {
        html += `<div style="margin-top:8px; padding:8px; background:#fff; border:1px solid #e2e8f0; border-radius:6px; max-height:220px; overflow-y:auto; white-space:pre-wrap; font-size:13px;"><b>📝 完整逐字稿：</b>\n${escapeHtml(m.transcript)}</div>`;
    }
    if (m.summary) html += `<div style="margin-top:8px; color:#1e40af;"><b>💡 AI 通話摘要：</b>${escapeHtml(m.summary)}</div>`;
    const p = m.pickupInfo || {};
    if (p.totalAmount) document.getElementById("pickupTotalAmount").value = p.totalAmount;
    if (p.pickupTime) document.getElementById("pickupTime").value = p.pickupTime;
    if (p.bagInfo) document.getElementById("pickupBagInfo").value = p.bagInfo;
    if (p.orderNotes) document.getElementById("pickupOrderNo").value = p.orderNotes;
    savePickupInfo();
    if (p.totalAmount || p.pickupTime) html += `<div style="margin-top:8px; color:#16a34a; font-weight:bold;">🎉 已自動擷取取餐資訊並填入下方紀錄卡！</div>`;
    statusBox.innerHTML = html;
    vtCleanup();
}

function vtCleanup() {
    const state = vtState;
    vtState = null;
    if (state) {
        try { state.proc && state.proc.disconnect(); } catch (e) { /* ignore */ }
        try { state.stream.getTracks().forEach(t => t.stop()); } catch (e) { /* ignore */ }
        try { state.inCtx.close(); } catch (e) { /* ignore */ }
        try { state.outCtx.close(); } catch (e) { /* ignore */ }
        try { state.ws.close(); } catch (e) { /* ignore */ }
    }
    const btn = document.getElementById("voiceTestBtn");
    if (btn) {
        btn.disabled = false;
        btn.innerText = "🎙️ 網頁語音實測（我扮演店員，免電話費）";
    }
}
