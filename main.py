from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional # 💡 Optional 도구 추가

from services.ocr_service import extract_text_from_image
from models.correction_model import correct_drug_name
from services.db_service import check_drug_interaction
from services.rag_service import generate_explanation

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.post("/analyze")
async def analyze_drug(
    image: List[UploadFile] = File(...),
    current_pill_images: Optional[List[UploadFile]] = File(None),
    current_drugs: List[str] = Form(default=[])
):

    # 1단계: OCR (일단 에러가 안 나게, 들어온 여러 장 중 첫 번째[0] 사진만 읽도록 둡니다)
    raw_text = await extract_text_from_image(image[0])

    # 2단계: sLLM 오타 교정
    corrected_name = correct_drug_name(raw_text)

    # 3단계: DB 1차 판별
    db_result = check_drug_interaction(corrected_name, current_drugs)

    # 4단계: RAG 설명
    explanation = generate_explanation(corrected_name, db_result["risk"])

    return {
        "corrected_name": corrected_name,
        "risk": db_result["risk"],
        "reason": db_result["reason"],
        "explanation": explanation
    }