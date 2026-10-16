// ==========================================
// 1. 全域變數設定
// ==========================================
// 👇 請確保這是您的試算表連結
var SHEET_URL = 'https://docs.google.com/spreadsheets/d/1-LEFt60a9LWRPD91QCK6CaqkKSeH6U7IoJL8KPoGAoI/edit?usp=drivesdk';

// 👇 請填入你的 Gemini API Key
var GEMINI_API_KEY = ''; 


// ==========================================
// 2. 接收前端 GET 請求 (讀取試算表資料)
// ==========================================
function doGet(e) {
  // CORS 處理，確保外部網站可以讀取
  var headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type"
  };

  var action = e.parameter.action;
  var result = {};

  try {
    if (action === 'getStaffNames') {
      result.data = getStaffNames();
    } else if (action === 'getCurrentShop') {
      result.data = getCurrentShop();
    } else if (action === 'getOrderData') {
      result.data = getOrderData();
    } else {
      result.error = "未知的 GET 請求: " + action;
    }
  } catch(err) {
    result.error = err.toString();
  }

  return ContentService.createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}


// ==========================================
// 3. 接收前端 POST 請求 (寫入資料 & 呼叫 AI)
// ==========================================
function doPost(e) {
  var result = {};
  
  try {
    // 解析前端傳來的資料包
    var payload = JSON.parse(e.postData.contents);
    var action = payload.action;

    if (action === 'addOrder') {
      result.data = addOrder(payload.orderData);
    } else if (action === 'getAiRecommendation') {
      result.data = fetchAiRecommendationFromBackend(payload.shopName, payload.menuText, payload.requestType);
    } else {
       result.error = "未知的 POST 請求: " + action;
    }
  } catch(err) {
    result.error = err.toString();
  }

  return ContentService.createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}

// ==========================================
// 4. 核心邏輯 (與你原本提供的 code.txt 相同)
// ==========================================

function getStaffNames() {
  var sheet = SpreadsheetApp.openByUrl(SHEET_URL).getSheets()[0];
  var lastRow = sheet.getLastRow(); 
  if (lastRow < 3) return [];
  var names = sheet.getRange(3, 1, lastRow - 2, 1).getValues();
  var cleanNames = [];
  for (var i = 0; i < names.length; i++) {
    var currentName = names[i][0].toString().trim();
    if (currentName !== "") cleanNames.push(currentName);
  }
  return cleanNames;
}

function getCurrentShop() {
  try {
    var sheet = SpreadsheetApp.openByUrl(SHEET_URL).getSheets()[0];
    var title = sheet.getRange("A1").getValue().toString().trim();
    var currentShop = sheet.getRange("B1").getValue().toString().trim();
    var quotaLabel = sheet.getRange("C1").getValue().toString().trim(); 
    var quotaValue = sheet.getRange("D1").getValue(); 
    return { title: title, shop: currentShop, quotaLabel: quotaLabel, quotaValue: quotaValue };
  } catch (e) {
    throw new Error("試算表讀取失敗: " + e.toString());
  }
}

function getOrderData() {
  var sheet = SpreadsheetApp.openByUrl(SHEET_URL).getSheets()[0];
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 2 || lastCol < 1) return []; 
  return sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
}

function addOrder(data) {
  var sheet = SpreadsheetApp.openByUrl(SHEET_URL).getSheets()[0];
  var lastRow = sheet.getLastRow();
  var rowIndex = -1;
  if (lastRow >= 3) {
    var namesRange = sheet.getRange(3, 1, lastRow - 2, 1).getValues();
    for (var i = 0; i < namesRange.length; i++) {
      if (namesRange[i][0].toString().trim() === data.name.trim()) {
        rowIndex = i + 3;
        break;
      }
    }
  }
  
  var orderRow = [data.drink, data.size, data.sugar, data.ice, data.topping, data.totalPrice, data.diff];
  if (rowIndex !== -1) {
    sheet.getRange(rowIndex, 2, 1, 7).setValues([orderRow]);
  } else {
    var newRow = [data.name].concat(orderRow);
    sheet.appendRow(newRow);
  }
  
  return "成功"; 
} 

function fetchAiRecommendationFromBackend(shopName, menuText, requestType) {
  const today = Utilities.formatDate(new Date(), "Asia/Taipei", "yyyy-MM-dd");
  const cacheKey = `AI_Menu_${shopName}`; 
  const props = PropertiesService.getScriptProperties();
  
  const cachedStr = props.getProperty(cacheKey);
  if (cachedStr) {
    try {
      const cacheData = JSON.parse(cachedStr);
      if (cacheData.date === today && cacheData.results && cacheData.results[requestType]) {
        return cacheData.results[requestType] + "<br><br><span style='font-size:12px; color:#888;'>(💡 秒回！此為 AI 今日特製推薦)</span>";
      }
    } catch (e) {}
  }

  const url = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key=${GEMINI_API_KEY}`;
  const companyContext = "我們是中鋼公司「手銲股」的夥伴，工作時常需電銲，眼睛跟精神容易疲勞，會想靠飲料回血。";
  
  const prompt = `
  你是一個超懂台灣手搖飲的達人。請務必使用 Google 搜尋（包含店家官網、PTT、Dcard、美食部落格等網路評價），針對「${shopName}」這家飲料店，一次性推薦 6 種不同方向的飲料給使用者。
  使用者背景：${companyContext}

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
  3. 絕對不要在前後加上 \`\`\`json 或是 \`\`\` 的標記，直接用 { 開頭，用 } 結尾。
  4. JSON 的 Key 必須完全精確是這六個字串："招牌", "純茶", "奶類", "新鮮", "熱飲", "內行"。
  5. 每個 Value 是約 80~100 字的推薦理由（語氣要專業，有禮貌），並給出建議的甜度冰塊。
  
  【目前可選菜單（請用來核對網路上推薦的飲料是否還有在賣）】：
  ${menuText}
  
  【預期輸出的 JSON 結構範例】：
  {
    "招牌": "推薦理由...",
    "純茶": "推薦理由...",
    "奶類": "推薦理由...",
    "新鮮": "推薦理由...",
    "熱飲": "推薦理由...",
    "內行": "推薦理由..."
  }
  `;

  const payload = {
    "contents": [{"parts": [{"text": prompt}]}],
    "tools": [{"googleSearch": {}}],
    "generationConfig": { "temperature": 0.8 }
  };

  const options = {
    'method': 'post',
    'contentType': 'application/json',
    'payload': JSON.stringify(payload),
    'muteHttpExceptions': true
  };

  try {
    const response = UrlFetchApp.fetch(url, options);
    const json = JSON.parse(response.getContentText());
    
    if (json.candidates && json.candidates.length > 0) {
      if (json.candidates[0].finishReason === "SAFETY") {
        return "不好意思，這家店的菜單似乎觸發了 AI 的安全過濾機制。";
      }
      
      const text = json.candidates[0].content.parts[0].text;
      let cleanText = text.trim();
      if (cleanText.startsWith("```json")) cleanText = cleanText.substring(7);
      else if (cleanText.startsWith("```")) cleanText = cleanText.substring(3);
      if (cleanText.endsWith("```")) cleanText = cleanText.substring(0, cleanText.length - 3);
      cleanText = cleanText.trim();

      let resultObj;
      try {
        resultObj = JSON.parse(cleanText);
      } catch (parseError) {
        return "【AI 格式化失敗，它講了太多廢話】：<br>" + text;
      }
      
      props.setProperty(cacheKey, JSON.stringify({
        date: today,
        results: resultObj
      }));
      
      return resultObj[requestType];

    } else if (json.error) {
      return "【API 錯誤抓到了】：" + json.error.message;
    } else {
      return "【預期外的回傳格式】：" + JSON.stringify(json);
    }
  } catch(e) {
    return "連線到 AI 時發生小意外：" + e.message;
  }
}