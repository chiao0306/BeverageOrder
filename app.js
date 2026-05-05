// 你的 GAS 網址已經填入
const GAS_API_URL = "https://script.google.com/macros/s/AKfycbx7M-noe6BwZheMiaHBCbgt8oyBnd_YH17xA3Dwb0L98HgV9f96Z-8nOKW0ubxDek6q/exec";
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

    // AI 卡片 UI 處理
    const aiPanel = document.getElementById("aiPanel");
    const aiResult = document.getElementById("aiRecommendationResult");
    const aiMainBtn = document.getElementById("aiMainBtn");
    
    if(aiPanel) aiPanel.style.display = "none";
    if(aiResult) aiResult.style.display = "none";
    if(aiMainBtn) {
        aiMainBtn.style.backgroundColor = shopData.color;
        aiMainBtn.style.color = "#FFF";
        aiMainBtn.style.border = "none";
    }
    if(aiResult) {
        aiResult.style.borderLeftColor = shopData.color;
        aiResult.style.background = "#FAFAFA";
        aiResult.style.boxShadow = "0 2px 8px rgba(0,0,0,0.06)";
        aiResult.style.border = "1px solid #EEE";
        aiResult.style.borderLeft = `6px solid ${shopData.color}`;
    }

    showPage("page-menu");
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
let pendingAlertMsg = "";

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
    
    // 暫存成功後的提示訊息
    pendingAlertMsg = "🎉 訂單送出成功！";
    if (diff > 0) pendingAlertMsg += `\n\n(${totalPrice})-(${quotaValue})=(${diff})\n記得補上 ${diff} 元至股務基金，感謝!`;

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
        await apiPost('addOrder', { orderData: pendingOrderData });
        alert(pendingAlertMsg); 
        closeConfirmModal(); // 傳送成功後關閉視窗
        finalSubmitBtn.disabled = false;
        finalSubmitBtn.innerText = "✅ 確認訂購";
        goToDirectory(); // 回到首頁
    } catch(e) {
        alert("傳送失敗：" + e.message);
        finalSubmitBtn.disabled = false;
        finalSubmitBtn.innerText = "✅ 確認訂購";
    }
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
function showPage(id) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    window.scrollTo(0,0);
}
function goToDirectory() { showPage("page-directory"); document.getElementById("appHeader").innerText = "手銲股訂飲料"; document.getElementById("appHeader").style.background = "#333"; }
function goToMenu() { showPage("page-menu"); }
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
function toggleAiPanel() {
    const panel = document.getElementById("aiPanel");
    panel.style.display = (panel.style.display === "none") ? "block" : "none";
}

// ==========================================
// 7. AI 推薦功能 (透過 API)
// ==========================================
async function getAiRecommendation(type) {
    const resultDiv = document.getElementById("aiRecommendationResult");
    const shopData = allShopsDatabase[currentShopName];
    
    resultDiv.style.display = "block";
    resultDiv.innerHTML = `
        <div style="font-size: 16px; color: #666; text-align: center; padding: 10px 0;">
            ⏳ AI 正在為您通盤分析<strong>「${currentShopName}」</strong><br>
            <span style="font-size: 14px; color: #999;">(請稍候...)</span>
        </div>
    `;

    let menuString = "";
    for (const [category, drinks] of Object.entries(shopData.menu)) {
        menuString += `【${category}】: ` + drinks.map(d => d.name).join(", ") + "\n";
    }

    try {
        const response = await apiPost('getAiRecommendation', {
            shopName: currentShopName,
            menuText: menuString,
            requestType: type
        });
        
        resultDiv.innerHTML = `
            <div style="font-size: 18px; font-weight: bold; color: ${shopData.color}; margin-bottom: 12px; border-bottom: 2px dashed ${shopData.color}; padding-bottom: 8px;">
                🤖 飲料達人 AI (${type}推薦)
            </div>
            <div style="font-size: 16px; line-height: 1.8; color: #333; letter-spacing: 0.5px;">
                ${response}
            </div>
        `;
    } catch(e) {
        resultDiv.innerHTML = `<div style="color: #E74C3C; font-size: 16px; text-align: center; font-weight: bold;">❌ 呼叫 AI 失敗，請稍後再試！</div>`;
        console.error(e);
    }
}
