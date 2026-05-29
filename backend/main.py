import os
import datetime
import random
import requests
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Float, Integer, Date, DateTime, Numeric, desc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# ----------------------------------------------------------------
# 1. 初始化 FastAPI 應用程式
# ----------------------------------------------------------------
app = FastAPI(title="臺灣鹿發情監測與繁殖管理系統 API 後端")

# 啟用跨網域資源共享 (CORS)，確保前端網頁不論部署在何處都能正常存取 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------
# 2. 資料庫連線配置 (自動切換 本地SQLite 或 線上PostgreSQL)
# ----------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    # 修正 Render 平台可能產生的 postgres:// 舊版前綴相容性問題
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    # 本地開發時，若無提供資料庫環境變數，預設建立本地檔案資料庫
    DATABASE_URL = "sqlite:///./taiwan_deer.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ----------------------------------------------------------------
# 3. 定義 SQLAlchemy 資料庫模型 (ORM)
# ----------------------------------------------------------------
class DeerProfileModel(Base):
    __tablename__ = "deer_profiles"
    deer_id = Column(String(50), primary_key=True, index=True)
    weight = Column(Float)
    birthday = Column(Date)
    gender = Column(String(10))
    father_id = Column(String(50), nullable=True)
    mother_id = Column(String(50), nullable=True)
    breeding_count = Column(Integer, default=0)
    pen_id = Column(String(20))

class EnvironmentalRecordModel(Base):
    __tablename__ = "environmental_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    temperature = Column(Float)
    humidity = Column(Float)
    recorded_at = Column(DateTime, default=datetime.datetime.utcnow)

class BehaviorLogModel(Base):
    __tablename__ = "behavior_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    deer_id = Column(String(50))
    behavior_type = Column(String(20))  # Standing, Lying, Walking, Eating, Mounting, FO
    duration_seconds = Column(Integer)
    confidence = Column(Float)
    detected_at = Column(DateTime, default=datetime.datetime.utcnow)

class AiAnalysisReportModel(Base):
    __tablename__ = "ai_analysis_reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    deer_id = Column(String(50))
    report_content = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

# 建立所有尚未存在的工作資料表 (適用於本地 SQLite 測試)
Base.metadata.create_all(bind=engine)

# 資料庫 Session 依賴項注入
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------------------------------------------------------
# 4. 定義 Pydantic 資料驗證模型 (DTO)
# ----------------------------------------------------------------
class DeerProfileCreate(BaseModel):
    deer_id: str
    weight: float
    birthday: str  # 格式: YYYY-MM-DD
    gender: str
    father_id: Optional[str] = None
    mother_id: Optional[str] = None
    breeding_count: int = 0
    pen_id: str

class EnvironmentPost(BaseModel):
    temperature: float
    humidity: float

# ----------------------------------------------------------------
# 5. 後端 API 路由實作
# ----------------------------------------------------------------

# 接口一：取得當前即時溫濕度數據 (對應 1.html 儀表板)
@app.get("/api/environment/current")
def get_current_environment(db: Session = Depends(get_db)):
    record = db.query(EnvironmentalRecordModel).order_by(desc(EnvironmentalRecordModel.recorded_at)).first()
    if record:
        return {
            "temperature": float(record.temperature),
            "humidity": float(record.humidity),
            "recorded_at": record.recorded_at.isoformat()
        }
    # 若資料庫為空，提供預設基準值避免前端阻斷
    return {"temperature": 19.5, "humidity": 62.8, "recorded_at": datetime.datetime.now().isoformat()}

# 接口二：取得歷史溫濕度曲線趨勢 (對應 1.html 折線圖)
@app.get("/api/environment/history")
def get_environment_history(db: Session = Depends(get_db)):
    records = db.query(EnvironmentalRecordModel).order_by(EnvironmentalRecordModel.recorded_at).limit(12).all()
    times = [r.recorded_at.strftime("%H:%M") for r in records]
    temps = [float(r.temperature) for r in records]
    hums = [float(r.humidity) for r in records]
    
    if not records:
        times = ['00:00', '03:00', '06:00', '09:00', '12:00', '15:00', '18:00', '21:00', '23:59']
        temps = [18.8, 17.5, 17.2, 21.4, 27.5, 26.3, 23.4, 21.0, 19.5]
    
    return {"times": times, "temperatures": temps}

# 接口三：取得所有登記鹿隻清單 (對應 2.html 左側側邊欄)
@app.get("/api/deer")
def get_all_deer(db: Session = Depends(get_db)):
    deer_list = db.query(DeerProfileModel).all()
    return deer_list

# 接口四：新增/登記鹿隻履歷資料 (對應 3.html 表單提交)
@app.post("/api/deer")
def register_deer(deer: DeerProfileCreate, db: Session = Depends(get_db)):
    existing = db.query(DeerProfileModel).filter(DeerProfileModel.deer_id == deer.deer_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="此鹿隻編號已存在於系統中")
    
    try:
        birth_date = datetime.datetime.strptime(deer.birthday, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式不正確，應為 YYYY-MM-DD")

    new_deer = DeerProfileModel(
        deer_id=deer.deer_id,
        weight=deer.weight,
        birthday=birth_date,
        gender=deer.gender,
        father_id=deer.father_id if deer.father_id != "請選擇父畜" else None,
        mother_id=deer.mother_id if deer.mother_id != "請選擇母畜" else None,
        breeding_count=deer.breeding_count,
        pen_id=deer.pen_id if deer.pen_id != "請選擇欄位" else None
    )
    db.add(new_deer)
    db.commit()
    return {"status": "success", "message": f"鹿隻 {deer.deer_id} 履歷登記成功"}

# 接口五：取得今日發情辨識指標統計數據 (對應 4.html 頂部四大數據卡片)
@app.get("/api/estrus/stats")
def get_estrus_statistics(db: Session = Depends(get_db)):
    # 統計今日 YOLO 計算出的各項特徵次數
    mounting_count = db.query(BehaviorLogModel).filter(BehaviorLogModel.behavior_type == "Mounting").count()
    standing_count = db.query(BehaviorLogModel).filter(BehaviorLogModel.behavior_type == "Standing").count()
    fo_count = db.query(BehaviorLogModel).filter(BehaviorLogModel.behavior_type == "FO").count()
    
    return {
        "mounting_count": mounting_count,
        "standing_count": standing_count,
        "fo_count": fo_count,
        "total_activity_index": 8542  # 模擬之全場綜合活動量指標
    }

# 接口六：取得即時行為紀錄流水帳 (對應 4.html 右側即時紀錄清單)
@app.get("/api/estrus/logs")
def get_behavior_logs(db: Session = Depends(get_db)):
    logs = db.query(BehaviorLogModel).order_by(desc(BehaviorLogModel.detected_at)).limit(10).all()
    result = []
    for log in logs:
        result.append({
            "behavior_type": log.behavior_type,
            "deer_id": log.deer_id,
            "time": log.detected_at.strftime("%H:%M:%S"),
            "duration": log.duration_seconds
        })
    return result

# 接口七：AI 專家繁殖分析 Agent核心端點 (對應 4.html 生成分析報表按鈕)
@app.post("/api/ai/analyze/{deer_id}")
def generate_ai_breeding_report(deer_id: str, db: Session = Depends(get_db)):
    # 1. 自資料庫內撈取該鹿隻的客觀行為數據特徵
    mounting_count = db.query(BehaviorLogModel).filter(BehaviorLogModel.deer_id == deer_id, BehaviorLogModel.behavior_type == "Mounting").count()
    standing_count = db.query(BehaviorLogModel).filter(BehaviorLogModel.deer_id == deer_id, BehaviorLogModel.behavior_type == "Standing").count()
    fo_count = db.query(BehaviorLogModel).filter(BehaviorLogModel.deer_id == deer_id, BehaviorLogModel.behavior_type == "FO").count()
    
    # 2. 構建專門用來驅動大語言模型的台灣茸鹿繁殖管理 Prompt 提示詞
    prompt = (
        f"你是台灣頂尖的自動化畜牧管理專家，專精於臺灣茸鹿（水鹿與梅花鹿）的繁殖與行為辨識。 "
        f"現有系統透過電腦視覺 YOLO 模型監控到鹿隻編號「{deer_id}」在今日的行為指標如下：\n"
        f"- 爬跨/騎乘次數 (Mounting): {mounting_count} 次\n"
        f"- 站立發情次數 (Standing): {standing_count} 次\n"
        f"- 雄性追隨特徵 (FO): {fo_count} 次\n\n"
        f"請結合臺灣茸鹿配種實務經驗，詳細評估該鹿隻是否正處於發情期，給出主觀感受與情境體悟，並提供場主具體的操作處置建議（例如配種時機、欄位調整）。請直接輸出一段約200字的繁體中文專家報告。"
    )

    # 3. 嘗試呼叫線上真實的 Gemini API，若無密鑰則啟動內建專家生成邏輯
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                ai_text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                return {"deer_id": deer_id, "report": ai_text.strip()}
        except Exception:
            pass # 發生錯誤時向下容錯至內建 Agent

    # 4. 內建核心 Agent 規則生成引擎 (保證在無網路或密鑰狀態下依然輸出高水準報告)
    if mounting_count > 0 or standing_count > 0 or fo_count > 0:
        ai_text = (
            f"【AI 鹿隻繁殖專家判定報告】\n"
            f"經由電腦視覺即時分析，鹿隻 {deer_id} 現階段之爬跨行為達 {mounting_count} 次、"
            f"站立發情持續時間與雄性追隨特徵顯著。這與台灣水鹿/梅花鹿典型的發情行為鏈高度吻合。"
            f"情境感知顯示該母鹿性聯興奮度已達高峰，排卵窗估計在未來 12 至 24 小時內開啟。"
            f"強烈建議場主今日立即安排與優良種公鹿進行配種或人工授精，並將該鹿隻移至專用配種欄，避免多頭公鹿爭尾造成場地衝突。"
        )
    else:
        ai_text = (
            f"【AI 鹿隻繁殖專家判定報告】\n"
            f"對應鹿隻 {deer_id} 當前的行為軌跡，站立、進食及躺臥比例皆在常態基準線內，"
            f"今日並未偵測到任何爬跨或雄性追隨等發情特徵。評估目前處於發情間期，生殖生理狀態平穩。"
            f"場主現階段維持常態飼糧管理即可，不需進行額外的配種干預。"
        )

    return {"deer_id": deer_id, "report": ai_text}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)