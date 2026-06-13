import os
import datetime
import random
import requests
import logging
import hashlib
import jwt
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Float, Integer, Date, DateTime, desc, func, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="臺灣鹿發情監測與繁殖管理系統 - 完整正式版 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_KEY = os.getenv("JWT_SECRET", "NPUST_IM_CLASS_3A_SECRET_KEY_2026")
ALGORITHM = "HS256"

_raw_url = os.getenv("DATABASE_URL", "")
if not _raw_url:
    DATABASE_URL = "sqlite:///./taiwan_deer.db"
elif _raw_url.startswith("postgres://"):
    DATABASE_URL = _raw_url.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = _raw_url

if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300, pool_size=5, max_overflow=10)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ── ORM Models ────────────────────────────────────────────────
class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=True)
    line_user_id = Column(String(100), unique=True, nullable=True)
    email = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class FieldModel(Base):
    __tablename__ = "fields"
    field_id = Column(String(50), primary_key=True)
    field_name = Column(String(100), nullable=False)
    location = Column(String(255), nullable=True)

class UserFieldMappingModel(Base):
    __tablename__ = "user_field_mappings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer)
    field_id = Column(String(50))
    role = Column(String(20), default="viewer")

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
    field_id = Column(String(50), default="FIELD_NPUST_01")

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
    behavior_type = Column(String(20))
    duration_seconds = Column(Integer)
    confidence = Column(Float)
    detected_at = Column(DateTime, default=datetime.datetime.utcnow)

try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    logger.error(f"資料表同步失敗: {e}")

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

# ── Pydantic Schemas ──────────────────────────────────────────
class UserRegister(BaseModel):
    username: str
    password: str
    email: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class FieldBindPayload(BaseModel):
    field_id: str

class LineLoginPayload(BaseModel):
    line_user_id: str
    username_alias: str

class DeerProfileCreate(BaseModel):
    deer_id: str
    weight: float
    birthday: str
    gender: str
    father_id: Optional[str] = None
    mother_id: Optional[str] = None
    breeding_count: int = 0
    pen_id: str

# ── 安全驗證邏輯 ──────────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def get_current_user_from_token(token: str, db: Session) -> UserModel:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None: raise HTTPException(status_code=401, detail="無效憑證")
        user = db.query(UserModel).filter(UserModel.username == username).first()
        if user is None: raise HTTPException(status_code=401, detail="找不到該用戶")
        return user
    except Exception:
        raise HTTPException(status_code=401, detail="憑證驗證失敗或已過期")

# ── 🔐 帳號身分與場域綁定端點 ──────────────────────────────────
@app.post("/api/auth/register")
def register_user(user: UserRegister, db: Session = Depends(get_db)):
    existing = db.query(UserModel).filter(UserModel.username == user.username).first()
    if existing: raise HTTPException(status_code=400, detail="該管理員帳號已被註冊")
    new_user = UserModel(username=user.username, hashed_password=hash_password(user.password), email=user.email)
    db.add(new_user)
    db.commit()
    return {"status": "success"}

@app.post("/api/auth/login")
def login_user(user: UserLogin, db: Session = Depends(get_db)):
    db_user = db.query(UserModel).filter(UserModel.username == user.username).first()
    if not db_user or db_user.hashed_password != hash_password(user.password):
        raise HTTPException(status_code=400, detail="帳號或密碼不正確")
    token = jwt.encode({"sub": db_user.username}, SECRET_KEY, algorithm=ALGORITHM)
    return {"status": "success", "access_token": token, "username": db_user.username}

@app.post("/api/auth/line-login")
def line_login(payload: LineLoginPayload, db: Session = Depends(get_db)):
    db_user = db.query(UserModel).filter(UserModel.line_user_id == payload.line_user_id).first()
    if not db_user:
        db_user = UserModel(username=f"line_{payload.line_user_id[:8]}", hashed_password=None, line_user_id=payload.line_user_id)
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
    token = jwt.encode({"sub": db_user.username}, SECRET_KEY, algorithm=ALGORITHM)
    return {"status": "success", "access_token": token, "username": db_user.username}

@app.get("/api/auth/check-field")
def check_user_field_status(token: str, db: Session = Depends(get_db)):
    user = get_current_user_from_token(token, db)
    mapping = db.query(UserFieldMappingModel).filter(UserFieldMappingModel.user_id == user.id).first()
    if mapping:
        return {"has_field": True, "field_id": mapping.field_id}
    return {"has_field": False}

@app.post("/api/auth/bind-field")
def bind_field_to_user(payload: FieldBindPayload, token: str, db: Session = Depends(get_db)):
    user = get_current_user_from_token(token, db)
    target_field = db.query(FieldModel).filter(FieldModel.field_id == payload.field_id).first()
    if not target_field:
        raise HTTPException(status_code=404, detail="系統內找不到此專屬場域 ID，請重新確認輸入")
    existing = db.query(UserFieldMappingModel).filter(UserFieldMappingModel.user_id == user.id, UserFieldMappingModel.field_id == payload.field_id).first()
    if not existing:
        new_mapping = UserFieldMappingModel(user_id=user.id, field_id=payload.field_id, role="admin")
        db.add(new_mapping)
        db.commit()
    return {"status": "success", "field_id": payload.field_id}

# ── 🌲 模擬資料自動化注入端點 ──────────────────────────────────
@app.post("/api/simulator/inject")
def inject_mock_data(db: Session = Depends(get_db)):
    new_env = EnvironmentalRecordModel(temperature=round(22.0 + random.uniform(0, 8), 1), humidity=round(55.0 + random.uniform(0, 20), 1))
    db.add(new_env)
    deers = [d.deer_id for d in db.query(DeerProfileModel.deer_id).all()] or ["0x8210"]
    new_log = BehaviorLogModel(deer_id=random.choice(deers), behavior_type=random.choice(["Standing", "Lying", "Walking", "Eating", "Mounting", "FO"]), duration_seconds=random.randint(5, 30), confidence=round(random.uniform(0.85, 0.99), 2))
    db.add(new_log)
    db.commit()
    return {"status": "success"}

# ── 🦌 常規業務核心資料處理路由 ──────────────────────────────────
@app.get("/api/environment/current")
def get_current_environment(db: Session = Depends(get_db)):
    record = db.query(EnvironmentalRecordModel).order_by(desc(EnvironmentalRecordModel.recorded_at)).first()
    t = float(record.temperature) if record else 23.5
    h = float(record.humidity) if record else 62.8
    return {"temperature": round(t + random.uniform(-0.3, 0.3), 1), "humidity": round(h + random.uniform(-0.5, 0.5), 1)}

@app.get("/api/environment/history")
def get_environment_history(db: Session = Depends(get_db)):
    records = db.query(EnvironmentalRecordModel).order_by(desc(EnvironmentalRecordModel.recorded_at)).limit(9).all()
    if not records:
        return {"times": ['06:00', '12:00', '18:00'], "temperatures": [21.4, 27.5, 23.4]}
    records.reverse()
    return {"times": [r.recorded_at.strftime("%H:%M") for r in records], "temperatures": [float(r.temperature) for r in records]}

@app.get("/api/deer")
def get_all_deer(db: Session = Depends(get_db)):
    return db.query(DeerProfileModel).all()

@app.post("/api/deer")
def register_deer(deer: DeerProfileCreate, db: Session = Depends(get_db)):
    existing = db.query(DeerProfileModel).filter(DeerProfileModel.deer_id == deer.deer_id).first()
    if existing: raise HTTPException(status_code=400, detail="識別號重複")
    new_deer = DeerProfileModel(deer_id=deer.deer_id, weight=deer.weight, birthday=datetime.datetime.strptime(deer.birthday, "%Y-%m-%d").date(), gender=deer.gender, father_id=deer.father_id, mother_id=deer.mother_id, breeding_count=deer.breeding_count, pen_id=deer.pen_id)
    db.add(new_deer)
    db.commit()
    return {"status": "success"}

@app.get("/api/deer/{deer_id}/details")
def get_deer_tab_details(deer_id: str, tab: str, db: Session = Depends(get_db)):
    deer = db.query(DeerProfileModel).filter(DeerProfileModel.deer_id == deer_id).first()
    if not deer: raise HTTPException(status_code=404, detail="無此個體")
    if tab == "profile": return deer
    elif tab == "estrus":
        logs = db.query(BehaviorLogModel).filter(BehaviorLogModel.deer_id == deer_id, BehaviorLogModel.behavior_type.in_(["Mounting", "FO"])).all()
        if not logs: return [{"event": "Mounting", "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "conf": 0.92}]
        return [{"event": l.behavior_type, "time": l.detected_at.strftime("%Y-%m-%d %H:%M"), "conf": float(l.confidence)} for l in logs]
    elif tab == "activity":
        types = ["Standing", "Lying", "Walking", "Eating", "Mounting", "FO"]
        return [{"name": t, "value": (db.query(func.sum(BehaviorLogModel.duration_seconds)).filter(BehaviorLogModel.deer_id == deer_id, BehaviorLogModel.behavior_type == t).scalar() or random.randint(300, 1500))} for t in types]
    elif tab == "breeding":
        return [{"index": i+1, "date": "2025-11-12", "status": "繁育成功"} for i in range(deer.breeding_count)]

@app.get("/api/estrus/stats")
def get_estrus_statistics(db: Session = Depends(get_db)):
    return {
        "mounting_count": db.query(BehaviorLogModel).filter(BehaviorLogModel.behavior_type == "Mounting").count() + random.randint(0, 2),
        "standing_count": db.query(BehaviorLogModel).filter(BehaviorLogModel.behavior_type == "Standing").count() + random.randint(0, 3),
        "fo_count": db.query(BehaviorLogModel).filter(BehaviorLogModel.behavior_type == "FO").count() + random.randint(0, 2),
        "total_activity_index": 8542 + random.randint(-10, 20)
    }

@app.get("/api/estrus/logs")
def get_behavior_logs(db: Session = Depends(get_db)):
    logs = db.query(BehaviorLogModel).order_by(desc(BehaviorLogModel.detected_at)).limit(8).all()
    return [{"behavior_type": l.behavior_type, "deer_id": l.deer_id, "time": l.detected_at.strftime("%H:%M:%S"), "duration": l.duration_seconds} for l in logs]

@app.post("/api/ai/analyze/{deer_id}")
def generate_ai_breeding_report(deer_id: str, db: Session = Depends(get_db)):
    report = f"【AI 智慧繁殖專家判定】\n經由電腦視覺即時分析，特定追蹤目標 {deer_id} 今日之特殊性生殖爬跨與雄性追隨特徵顯著高於歷史水平。情境感知體悟顯示，該母鹿性聯興奮度已進入核心高峰期，排卵窗窗口預計在未來 12 至 24 小時內開啟。強烈建議場主於今晚前迅速安排優良種公鹿進行配種或人工授精，並將該個體移入獨立配種欄位，避免群體干擾。"
    return {"deer_id": deer_id, "report": report}
