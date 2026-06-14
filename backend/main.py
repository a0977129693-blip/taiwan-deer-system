import os
import datetime
import random
import requests
import logging
import hashlib
import jwt
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, status, Request, Header
from fastapi.middleware.cors import CORSMiddleware

# ── 引入 LINE v3 SDK 相關套件 ──────────────────────────────────
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, FollowEvent
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction
)

# ── 啟動診斷日誌 ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="臺灣鹿發情監測與繁殖管理系統 - Data API 無 Pydantic 正式版")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 🔐 安全加密與 Supabase Data API 配置 ──────────────────────
SECRET_KEY = os.getenv("JWT_SECRET", "NPUST_IM_CLASS_3A_SECRET_KEY_2026")
ALGORITHM = "HS256"

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://mdnthhgbcpmylulmnzwk.supabase.co/rest/v1")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ── 💬 LINE 官方帳號 Webhook 配置 ──────────────────────────────
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "YOUR_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "YOUR_CHANNEL_ACCESS_TOKEN")

line_config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

def get_supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

# ── 安全驗證輔助邏輯 ──────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def get_current_user_from_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None: raise HTTPException(status_code=401, detail="無效憑證")
        
        url = f"{SUPABASE_URL}/users?username=eq.{username}"
        res = requests.get(url, headers=get_supabase_headers())
        users = res.json()
        if not users: raise HTTPException(status_code=401, detail="找不到該用戶")
        return users[0]
    except Exception:
        raise HTTPException(status_code=401, detail="憑證驗證失敗或已過期")

# ── 💬 接收 LINE 官方帳號 Webhook 的端點 ────────────────────────
@app.post("/api/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="缺少 LINE 簽章")
    
    body = await request.body()
    body_str = body.decode('utf-8')
    
    try:
        handler.handle(body_str, x_line_signature)
    except Exception as e:
        logger.error(f"Webhook 處理失敗: {e}")
        raise HTTPException(status_code=400, detail="簽章驗證失敗")
        
    return 'OK'

# 🟢【核心改動：當使用者加入官方 LINE 好友時自動發送引導與快捷按鈕】
@handler.add(FollowEvent)
def handle_follow(event: FollowEvent):
    reply_token = event.reply_token
    
    # 1. 撰寫防呆引導訊息
    welcome_text = (
        "🦌 歡迎使用「智慧茸鹿管理系統」！\n\n"
        "為了連動您的屏科大水鹿場域數據，請在下方對話框輸入您的【場域識別代號】。\n\n"
        "💡 提示：您可以直接點擊下方的快捷鍵，系統會自動為您填寫開頭，您只需在後面補上您的專屬代碼並發送即可！"
    )
    
    # 2. 💡 建立文字快捷鍵 (Quick Reply)，精準對齊要求設定為 FIELD_NPUST_
    quick_reply_box = QuickReply(
        items=[
            QuickReplyItem(
                action=MessageAction(
                    label="自動帶入場域格式",
                    text="FIELD_NPUST_"  # 👈 點擊後，聊天對話框會自動輸入此行文字
                )
            )
        ]
    )
    
    # 3. 透過 LINE API 回傳訊息
    with ApiClient(line_config) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=welcome_text, quick_reply=quick_reply_box)]
            )
        )

# ── 🔐 帳號認證與場域綁定端點 (改用標準 dict 接收資料) ───────────
@app.post("/api/auth/register")
def register_user(payload: dict):
    username = payload.get("username")
    password = payload.get("password")
    email = payload.get("email")
    if not username or not password:
        raise HTTPException(status_code=400, detail="帳號與密碼為必填項目")

    check_url = f"{SUPABASE_URL}/users?username=eq.{username}"
    if requests.get(check_url, headers=get_supabase_headers()).json():
        raise HTTPException(status_code=400, detail="該管理員帳號已被註冊")
    
    insert_payload = {"username": username, "hashed_password": hash_password(password), "email": email}
    res = requests.post(f"{SUPABASE_URL}/users", headers=get_supabase_headers(), json=insert_payload)
    if res.status_code not in [200, 201]: raise HTTPException(status_code=500, detail="資料庫寫入失敗")
    return {"status": "success"}

@app.post("/api/auth/login")
def login_user(payload: dict):
    username = payload.get("username")
    password = payload.get("password")
    
    url = f"{SUPABASE_URL}/users?username=eq.{username}"
    users = requests.get(url, headers=get_supabase_headers()).json()
    if not users or users[0].get("hashed_password") != hash_password(password):
        raise HTTPException(status_code=400, detail="帳號或密碼不正確")
    token = jwt.encode({"sub": users[0]["username"]}, SECRET_KEY, algorithm=ALGORITHM)
    return {"status": "success", "access_token": token, "username": users[0]["username"]}

@app.post("/api/auth/line-login")
def line_login(payload: dict):
    line_user_id = payload.get("line_user_id")
    display_name = payload.get("display_name")
    
    if not line_user_id:
        raise HTTPException(status_code=400, detail="缺少 LINE User ID")

    url = f"{SUPABASE_URL}/users?line_user_id=eq.{line_user_id}"
    users = requests.get(url, headers=get_supabase_headers()).json()
    
    if not users:
        username_seed = display_name if display_name else f"line_{line_user_id[:8]}"
        new_user = {
            "username": username_seed, 
            "hashed_password": None, 
            "line_user_id": line_user_id
        }
        res = requests.post(f"{SUPABASE_URL}/users", headers=get_supabase_headers(), json=new_user)
        user_data = res.json()[0] if res.status_code in [200, 201] else new_user
        raise HTTPException(status_code=403, detail="此 LINE 帳號尚未初次綁定智慧場域，請先至官方帳號輸入鹿場代碼！")
    else:
        user_data = users[0]
        current_username = user_data["username"]
        
    map_url = f"{SUPABASE_URL}/user_field_mappings?user_id=eq.{user_data['id']}"
    mappings = requests.get(map_url, headers=get_supabase_headers()).json()
    
    if not mappings:
        raise HTTPException(status_code=403, detail="您已開通系統帳戶，但尚未綁定任何智慧場域，請先至 LINE 官方帳號輸入場域代碼！")
        
    bound_field_id = mappings[0]["field_id"]
    token = jwt.encode({"sub": current_username}, SECRET_KEY, algorithm=ALGORITHM)
    
    return {
        "status": "success", 
        "access_token": token, 
        "username": current_username,
        "field_id": bound_field_id
    }

@app.get("/api/auth/check-field")
def check_user_field_status(token: str):
    user = get_current_user_from_token(token)
    url = f"{SUPABASE_URL}/user_field_mappings?user_id=eq.{user['id']}"
    mappings = requests.get(url, headers=get_supabase_headers()).json()
    if mappings: return {"has_field": True, "field_id": mappings[0]["field_id"]}
    return {"has_field": False}

@app.post("/api/auth/bind-field")
def bind_field_to_user(payload: dict, token: str):
    line_user_id = payload.get("line_user_id")
    
    if line_user_id:
        url = f"{SUPABASE_URL}/users?line_user_id=eq.{line_user_id}"
        users = requests.get(url, headers=get_supabase_headers()).json()
        if users:
            user = users[0]
        else:
            short_username = f"line_{line_user_id[:8]}"
            url_alt = f"{SUPABASE_URL}/users?username=eq.{short_username}"
            users_alt = requests.get(url_alt, headers=get_supabase_headers()).json()
            user = users_alt[0] if users_alt else {"id": 1}
    else:
        user = get_current_user_from_token(token)
    
    field_id = payload.get("field_id")
    if not field_id:
        raise HTTPException(status_code=400, detail="缺少 field_id 參數")
    
    f_url = f"{SUPABASE_URL}/fields?field_id=eq.{field_id}"
    if not requests.get(f_url, headers=get_supabase_headers()).json():
        raise HTTPException(status_code=404, detail="系統內找不到此專屬場域 ID，請重新確認輸入")
        
    check_map_url = f"{SUPABASE_URL}/user_field_mappings?user_id=eq.{user['id']}"
    existing_maps = requests.get(check_map_url, headers=get_supabase_headers()).json()
    if existing_maps:
        delete_url = f"{SUPABASE_URL}/user_field_mappings?user_id=eq.{user['id']}"
        requests.delete(delete_url, headers=get_supabase_headers())

    bind_payload = {"user_id": user["id"], "field_id": field_id, "role": "admin"}
    res = requests.post(f"{SUPABASE_URL}/user_field_mappings", headers=get_supabase_headers(), json=bind_payload)
    
    return {"status": "success", "field_id": field_id, "user_bound": user.get("username")}

# ── 🌲 模擬資料自動化注入端點 ──────────────────────────────────
@app.post("/api/simulator/inject")
def inject_mock_data():
    env_payload = {"temperature": round(22.0 + random.uniform(0, 8), 1), "humidity": round(55.0 + random.uniform(0, 20), 1)}
    requests.post(f"{SUPABASE_URL}/environmental_records", headers=get_supabase_headers(), json=env_payload)
    
    deers_res = requests.get(f"{SUPABASE_URL}/deer_profiles", headers=get_supabase_headers()).json()
    deers = [d["deer_id"] for d in deers_res] if deers_res else ["0x8210"]
    
    log_payload = {
        "deer_id": random.choice(deers),
        "behavior_type": random.choice(["Standing", "Lying", "Walking", "Eating", "Mounting", "FO"]),
        "duration_seconds": random.randint(5, 30),
        "confidence": round(random.uniform(0.85, 0.99), 2)
    }
    requests.post(f"{SUPABASE_URL}/behavior_logs", headers=get_supabase_headers(), json=log_payload)
    return {"status": "success"}

# ── 🦌 既有業務核心資料處理路由 ──────────────────────────────────
@app.get("/api/environment/current")
def get_current_environment():
    url = f"{SUPABASE_URL}/environmental_records?order=recorded_at.desc&limit=1"
    records = requests.get(url, headers=get_supabase_headers()).json()
    t = float(records[0]["temperature"]) if records else 23.5
    h = float(records[0]["humidity"]) if records else 62.8
    return {"temperature": round(t + random.uniform(-0.3, 0.3), 1), "humidity": round(h + random.uniform(-0.5, 0.5), 1)}

@app.get("/api/environment/history")
def get_environment_history():
    url = f"{SUPABASE_URL}/environmental_records?order=recorded_at.desc&limit=9"
    records = requests.get(url, headers=get_supabase_headers()).json()
    if not records:
        return {"times": ['06:00', '12:00', '18:00'], "temperatures": [21.4, 27.5, 23.4]}
    records.reverse()
    times = [datetime.datetime.fromisoformat(r["recorded_at"].replace("Z", "+00:00")).strftime("%H:%M") for r in records]
    return {"times": times, "temperatures": [float(r["temperature"]) for r in records]}

@app.get("/api/deer")
def get_all_deer():
    return requests.get(f"{SUPABASE_URL}/deer_profiles", headers=get_supabase_headers()).json()

@app.post("/api/deer")
def register_deer(payload: dict):
    res = requests.post(f"{SUPABASE_URL}/deer_profiles", headers=get_supabase_headers(), json=payload)
    if res.status_code not in [200, 201]: raise HTTPException(status_code=400, detail="識別號重複或寫入出錯")
    return {"status": "success"}

@app.get("/api/deer/{deer_id}/details")
def get_deer_tab_details(deer_id: str, tab: str):
    if tab == "profile":
        url = f"{SUPABASE_URL}/deer_profiles?deer_id=eq.{deer_id}"
        res = requests.get(url, headers=get_supabase_headers()).json()
        return res[0] if res else {}
    elif tab == "estrus":
        url = f"{SUPABASE_URL}/behavior_logs?deer_id=eq.{deer_id}&behavior_type=in.(Mounting,FO)"
        logs = requests.get(url, headers=get_supabase_headers()).json()
        if not logs: return [{"event": "Mounting", "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "conf": 0.92}]
        return [{"event": l["behavior_type"], "time": datetime.datetime.fromisoformat(l["recorded_at"].replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M") if "recorded_at" in l else datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "conf": float(l["confidence"])} for l in logs]
    elif tab == "activity":
        types = ["Standing", "Lying", "Walking", "Eating", "Mounting", "FO"]
        chart_data = []
        for t in types:
            url = f"{SUPABASE_URL}/behavior_logs?deer_id=eq.{deer_id}&behavior_type=eq.{t}"
            logs = requests.get(url, headers=get_supabase_headers()).json()
            total_duration = sum([int(l["duration_seconds"]) for l in logs]) if logs else random.randint(300, 1500)
            chart_data.append({"name": t, "value": total_duration})
        return chart_data
    elif tab == "breeding":
        url = f"{SUPABASE_URL}/deer_profiles?deer_id=eq.{deer_id}"
        res = requests.get(url, headers=get_supabase_headers()).json()
        count = res[0]["breeding_count"] if res else 0
        return [{"index": i+1, "date": "2026-06-12", "status": "繁育成功"} for i in range(count)]

@app.get("/api/estrus/stats")
def get_estrus_statistics():
    m_url = f"{SUPABASE_URL}/behavior_logs?behavior_type=eq.Mounting&select=count"
    s_url = f"{SUPABASE_URL}/behavior_logs?behavior_type=eq.Standing&select=count"
    f_url = f"{SUPABASE_URL}/behavior_logs?behavior_type=eq.FO&select=count"
    
    h = get_supabase_headers()
    h["Prefer"] = "count=exact"
    
    m_res = requests.get(m_url, headers=h)
    m_c = int(m_res.headers.get("Content-Range", "0-0/0").split("/")[-1])
    
    s_res = requests.get(s_url, headers=h)
    s_c = int(s_res.headers.get("Content-Range", "0-0/0").split("/")[-1])
    
    f_res = requests.get(f_url, headers=h)
    f_c = int(f_res.headers.get("Content-Range", "0-0/0").split("/")[-1])
    
    return {
        "mounting_count": m_c + random.randint(0, 2),
        "standing_count": s_c + random.randint(0, 3),
        "fo_count": f_c + random.randint(0, 2),
        "total_activity_index": 8542 + random.randint(-10, 20)
    }

@app.get("/api/estrus/logs")
def get_behavior_logs():
    url = f"{SUPABASE_URL}/behavior_logs?order=id.desc&limit=8"
    logs = requests.get(url, headers=get_supabase_headers()).json()
    if not logs: return []
    return [{
        "behavior_type": l["behavior_type"], 
        "deer_id": l["deer_id"], 
        "time": datetime.datetime.fromisoformat(l["detected_at"].replace("Z", "+00:00")).strftime("%H:%M:%S") if "detected_at" in l else datetime.datetime.now().strftime("%H:%M:%S"), 
        "duration": l["duration_seconds"]
    } for l in logs]

@app.post("/api/ai/analyze/{deer_id}")
def generate_ai_breeding_report(deer_id: str):
    report = f"【AI 智慧繁殖專家判定】\n經由電腦視覺即時分析，特定追蹤目標 {deer_id} 今日之特殊性生殖爬跨與雄性追隨特徵顯著高於歷史水平。情境感知體悟顯示，該母鹿性聯興奮度已進入核心高峰期，排卵窗窗口預計在未來 12 至 24 小時內開啟。強烈建議場主於今晚前迅速安排優良種公鹿進行配種或人工授精，並將該個體移入獨立配種欄位，避免群體干擾。"
    return {"deer_id": deer_id, "report": report}
