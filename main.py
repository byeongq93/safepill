from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel
import sqlite3
from pathlib import Path
import os
import re

from services.ocr_service import extract_text_from_image
from models.correction_model import correct_drug_name
from services.db_service import check_drug_interaction, resolve_drug_name
from services.rag_service import generate_explanation

# --- DB 경로 고정 (실행 위치가 달라도 동일 DB를 사용하도록) ---
BASE_DIR = Path(__file__).resolve().parent

_ENV_DB = os.getenv("SAFEPILL_DB_PATH")
if _ENV_DB:
    DB_PATH = str((BASE_DIR / _ENV_DB).resolve()) if not os.path.isabs(_ENV_DB) else _ENV_DB
else:
    # 진짜 데이터(safepill.db)가 있으면 우선 사용
    DB_PATH = str((BASE_DIR / "safepill.db").resolve()) if (BASE_DIR / "safepill.db").exists() else str((BASE_DIR / "pharmguard.db").resolve())

STATIC_DIR_PATH = BASE_DIR / "static"
STATIC_DIR_PATH.mkdir(parents=True, exist_ok=True)
STATIC_DIR = str(STATIC_DIR_PATH)

def _connect():
    return sqlite3.connect(DB_PATH)

def ensure_user_tables():
    """users / user_medicines 테이블이 없으면 생성 (dur_contraindications는 건드리지 않음)"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT UNIQUE NOT NULL,
            pin TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_medicines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT NOT NULL,
            medicine_name TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

ensure_user_tables()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 실행 위치가 달라도 static 폴더를 정확히 찾게 경로 고정
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.on_event("startup")
def on_startup():
    ensure_user_tables()
    print("✅ Using DB:", DB_PATH)
    print("✅ users/user_medicines ready")


def extract_active_ingredients(raw_text: str) -> List[str]:
    """OCR 텍스트에서 '유효성분' 리스트를 간단히 추출.

    성분표 사진(판콜처럼)에서 제품명이 안 보이는 경우가 많아서,
    최소한 성분들은 사용자에게 보여주기 위한 용도.
    """
    if not raw_text:
        return []

    text = raw_text
    idx = text.find("유효성분")
    window = text[idx : idx + 900] if idx != -1 else text[:900]

    pat1 = re.compile(r"([가-힣A-Za-z]{2,})\s*\((?:KP|JP|USP|EP|BP|CP)\)")
    pat2 = re.compile(r"([가-힣A-Za-z]{2,})\s*(?:KP|JP|USP|EP|BP|CP)")

    found = [m.group(1) for m in pat1.finditer(window)]
    if len(found) < 2:
        found += [m.group(1) for m in pat2.finditer(window)]

    ban = {"유효성분", "일반의약품", "정보", "효능", "효과", "용법", "용량", "주의사항", "성인", "1회", "1병"}
    uniq = []
    for x in found:
        x = x.strip()
        if not x or x in ban:
            continue
        if x not in uniq:
            uniq.append(x)
    return uniq[:10]

# 💡 [새로 추가된 데이터 통신 규약]
class LoginRequest(BaseModel):
    nickname: str
    pin: str

class MedRequest(BaseModel):
    nickname: str
    medicine_name: str


class AnalyzeSelectRequest(BaseModel):
    selected_name: str
    current_drugs: List[str] = []

# ----------------------------------------------------
# 🔐 1. 로그인 & 자동 회원가입 API
# ----------------------------------------------------
@app.post("/login")
def login_or_signup(req: LoginRequest):
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("SELECT pin FROM users WHERE nickname = ?", (req.nickname,))
    user = cursor.fetchone()

    if user:  # 닉네임이 이미 DB에 존재함
        if user[0] != req.pin:
            conn.close()
            return {
                "status": "fail",
                "message": (
                    f"앗! '{req.nickname}'(은)는 이미 다른 분이 사용 중이거나 PIN 번호가 틀렸습니다.\\n"
                    "신규 가입이시라면 '최은철2'처럼 다른 닉네임을 입력해주세요!"
                )
            }
        conn.close()
        return {"status": "success", "message": f"환영합니다, {req.nickname}님!"}

    # 처음 온 유저라면 자동 회원가입
    cursor.execute("INSERT INTO users (nickname, pin) VALUES (?, ?)", (req.nickname, req.pin))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"신규 가입 완료! 환영합니다, {req.nickname}님!"}

# ----------------------------------------------------
# 💊 2. 내 약통 저장/조회/삭제 API
# ----------------------------------------------------
@app.get("/meds/{nickname}")
def get_meds(nickname: str):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT medicine_name FROM user_medicines WHERE nickname = ?", (nickname,))
    meds = [row[0] for row in cursor.fetchall()]
    conn.close()
    return {"meds": meds}

@app.post("/add_med")
def add_med(req: MedRequest):
    # ✅ 사용자가 입력한 약 이름을 DB 표준 이름으로 변환 (정규화→정확→유사 매칭)
    resolved = resolve_drug_name(req.medicine_name)
    # 0.90 이상(또는 정확매칭)만 자동 확정
    canon_name = resolved.get("resolved_name") or req.medicine_name

    conn = _connect()
    cursor = conn.cursor()

    # 중복 저장 방지
    cursor.execute(
        "SELECT id FROM user_medicines WHERE nickname = ? AND medicine_name = ?",
        (req.nickname, canon_name)
    )
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO user_medicines (nickname, medicine_name) VALUES (?, ?)",
            (req.nickname, canon_name)
        )
        conn.commit()

    conn.close()

    return {
        "status": "success",
        "saved_name": canon_name,
        "match_confidence": resolved.get("confidence", 0),
        "match_candidates": resolved.get("candidates", []),
        "match_note": resolved.get("note", "")
    }

@app.post("/delete_med")
def delete_med(req: MedRequest):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM user_medicines WHERE nickname = ? AND medicine_name = ?",
        (req.nickname, req.medicine_name)
    )
    conn.commit()
    conn.close()
    return {"status": "success"}

# ----------------------------------------------------
# 🚀 3. 분석 API
# ----------------------------------------------------
@app.post("/analyze")
async def analyze_drug(
    image: List[UploadFile] = File(...),
    current_pill_images: Optional[List[UploadFile]] = File(None),
    current_drugs: List[str] = Form(default=[])
):
    # 1) OCR
    raw_text = await extract_text_from_image(image[0])

    # (추가) 성분표에서 유효성분 추출
    active_ingredients = extract_active_ingredients(raw_text)

    # 2) (선택) 규칙 기반 1차 교정
    rule_corrected = correct_drug_name(raw_text)

    # 3) DB 표준 약품명으로 매칭 (정규화→정확→유사)
    resolved = resolve_drug_name(rule_corrected or raw_text)
    auto_applied = bool(resolved.get("auto_applied"))
    suggested_name = resolved.get("suggested_name") or ""
    corrected_name = resolved.get("resolved_name") if auto_applied else (rule_corrected or "")

    # 4) 현재 복용 약도 표준명으로 맞춰서 조회
    canon_current_drugs = []
    for d in current_drugs:
        r = resolve_drug_name(d)
        canon_current_drugs.append(r.get("resolved_name") or d)

    # 5) 병용금기 조회 (DB는 정확일치 기반)
    if corrected_name:
        db_result = check_drug_interaction(corrected_name, canon_current_drugs)
    else:
        db_result = {"risk": "특이사항 없음", "reason": "약품명이 확정되지 않아(매칭 신뢰도 낮음) 후보 선택이 필요합니다."}

    risk_level = db_result.get("risk", "특이사항 없음")
    reason_text = db_result.get("reason", "")

    # 🚀 [추가됨] OpenAPI 2차 검증 (로컬 DB에서 안전하다고 나왔고, 이름이 확정됐을 때만!)
    if risk_level == "특이사항 없음" and corrected_name:
        try:
            from services.api_service import check_dur_api # api 모듈 임포트
            api_result = check_dur_api(corrected_name)
            if api_result and api_result.get("status") == "danger":
                for warning in api_result.get("warnings", []):
                    mix_drug = warning.get("mix_drug", "")
                    for current_drug in canon_current_drugs:
                        if current_drug in mix_drug or mix_drug in current_drug:
                            risk_level = "위험"
                            reason_text = f"🚨 [식약처 실시간 API 경고] 내 약통의 [{current_drug}] 성분과 충돌!\n사유: {warning.get('warning_text')}"
                            break
                    if risk_level == "위험": break
        except Exception as e:
            print(f"⚠️ OpenAPI 통신 에러: {e}")

    # 6) 설명 생성
    explanation = generate_explanation(corrected_name or (suggested_name or rule_corrected or ""), risk_level, reason_text)

    return {
        "ocr_text": raw_text,
        "rule_corrected": rule_corrected,
        "corrected_name": corrected_name or (suggested_name or rule_corrected or ""),
        "risk": risk_level,
        "reason": reason_text,
        "explanation": explanation,
        "match_confidence": resolved.get("confidence", 0),
        "match_candidates": resolved.get("candidates", []),
        "match_note": resolved.get("note", ""),
        "auto_applied": auto_applied,
        "needs_confirm": bool(resolved.get("needs_confirm")),
        "suggested_name": suggested_name,
        "active_ingredients": active_ingredients,
    }


@app.post("/analyze_select")
def analyze_select(req: AnalyzeSelectRequest):
    """사용자가 후보 약품명을 선택했을 때, 그 이름으로 다시 위험도를 계산."""

    selected = (req.selected_name or "").strip()
    if not selected:
        return {"status": "fail", "message": "selected_name is empty"}

    canon_current_drugs = []
    for d in (req.current_drugs or []):
        r = resolve_drug_name(d)
        canon_current_drugs.append(r.get("resolved_name") or d)

    db_result = check_drug_interaction(selected, canon_current_drugs)
    risk_level = db_result.get("risk", "특이사항 없음")
    reason_text = db_result.get("reason", "")

    # 🚀 [추가됨] 유저가 직접 선택했을 때도 OpenAPI 2차 검증 돌리기!
    if risk_level == "특이사항 없음":
        try:
            from services.api_service import check_dur_api
            api_result = check_dur_api(selected)
            if api_result and api_result.get("status") == "danger":
                for warning in api_result.get("warnings", []):
                    mix_drug = warning.get("mix_drug", "")
                    for current_drug in canon_current_drugs:
                        if current_drug in mix_drug or mix_drug in current_drug:
                            risk_level = "위험"
                            reason_text = f"🚨 [식약처 실시간 API 경고] 내 약통의 [{current_drug}] 성분과 충돌!\n사유: {warning.get('warning_text')}"
                            break
                    if risk_level == "위험": break
        except Exception as e:
            print(f"⚠️ OpenAPI 통신 에러: {e}")

    explanation = generate_explanation(selected, risk_level, reason_text)

    return {
        "status": "success",
        "corrected_name": selected,
        "risk": risk_level,
        "reason": reason_text,
        "explanation": explanation,
        "match_note": "user_selected",
    }