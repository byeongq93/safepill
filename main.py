from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, List, Optional
from pydantic import BaseModel
import sqlite3
from pathlib import Path
import os
import re
import json
from difflib import SequenceMatcher

from services.ocr_service import extract_text_from_image
from models.correction_model import correct_drug_name
from services.db_service import check_drug_interaction, resolve_drug_name, lookup_catalog_by_name, lookup_catalog_by_candidates
from services.rag_service import build_patient_guidance, generate_explanation

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
    """users / user_medicines 테이블이 없으면 생성 및 필요한 컬럼 보강"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT UNIQUE NOT NULL,
            pin TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_medicines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT NOT NULL,
            medicine_name TEXT NOT NULL,
            active_ingredients TEXT DEFAULT '',
            source_type TEXT DEFAULT 'manual',
            ocr_text TEXT DEFAULT ''
        )
        """
    )

    cursor.execute("PRAGMA table_info(user_medicines)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    if "active_ingredients" not in existing_columns:
        cursor.execute("ALTER TABLE user_medicines ADD COLUMN active_ingredients TEXT DEFAULT ''")
    if "source_type" not in existing_columns:
        cursor.execute("ALTER TABLE user_medicines ADD COLUMN source_type TEXT DEFAULT 'manual'")
    if "ocr_text" not in existing_columns:
        cursor.execute("ALTER TABLE user_medicines ADD COLUMN ocr_text TEXT DEFAULT ''")

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



INDEX_HTML_PATH = BASE_DIR / "index_v2.html"


@app.get("/")
def serve_index():
    return FileResponse(INDEX_HTML_PATH)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.on_event("startup")
def on_startup():
    ensure_user_tables()
    print("✅ Using DB:", DB_PATH)
    print("✅ users/user_medicines ready")


class LoginRequest(BaseModel):
    nickname: str
    pin: str


class MedRequest(BaseModel):
    nickname: str
    medicine_name: str


class MedDetailRequest(BaseModel):
    nickname: str
    medicine_name: Optional[str] = None
    active_ingredients: List[str] = []
    source_type: str = "manual"
    ocr_text: str = ""


class AnalyzeSelectRequest(BaseModel):
    selected_name: str
    current_drugs: List[str] = []
    new_active_ingredients: List[str] = []
    current_active_ingredients: List[str] = []
    selected_current_labels: List[str] = []


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_ingredient_key(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^0-9a-zA-Z가-힣]+", "", value)
    return value


_INGREDIENT_STOPWORDS = {
    "유효성분",
    "유요성분",
    "일반의약품",
    "일반안전상비의약품",
    "정보",
    "효능",
    "효과",
    "용법",
    "용량",
    "주의사항",
    "성인",
    "상비약",
    "복용",
    "증상",
    "원료약품",
    "분량",
}

_INGREDIENT_CANONICAL_NAMES = [
    "아세트아미노펜",
    "이부프로펜",
    "덱시부프로펜",
    "나프록센",
    "아스피린",
    "디클로페낙",
    "케토프로펜",
    "와파린",
    "구아이페네신",
    "페닐레프린염산염",
    "클로르페니라민말레산염",
    "슈도에페드린염산염",
    "덱스트로메토르판브롬화수소산염수화물",
    "덱스트로메토르판브롬화수소산염",
    "암브록솔염산염",
    "브롬헥신염산염",
    "트리프롤리딘염산염",
    "세티리진염산염",
    "레보세티리진염산염",
    "로라타딘",
    "펙소페나딘염산염",
    "에페리손염산염",
    "카페인무수물",
    "펜톡시베린시트르산염",
    "슈도에페드린염산염",
    "디펜히드라민염산염",
    "DL-메틸에페드린염산염",
    "dl-메틸에페드린염산염",
    "리보플라빈포스페이트나트륨",
    "시메티콘",
    "인산알루미늄겔",
    "수산화마그네슘",
    "이소프로필안티피린",
    "파마브롬",
    "비타민C",
    "아스코르브산",
]

_INGREDIENT_ALIAS_MAP = {
    "아세트아미노펜": "아세트아미노펜",
    "아세트아미노데": "아세트아미노펜",
    "아세트아미노완": "아세트아미노펜",
    "아세트아미노옌": "아세트아미노펜",
    "아세트아미노팬": "아세트아미노펜",
    "아세트아미노덴": "아세트아미노펜",
    "아제트아미노웨": "아세트아미노펜",
    "아제트아미노펜": "아세트아미노펜",
    "아세트아미노페": "아세트아미노펜",
    "아세트아미노페니": "아세트아미노펜",
    "아세트아미노펜정": "아세트아미노펜",
    "acetaminophen": "아세트아미노펜",
    "paracetamol": "아세트아미노펜",
    "구아이페네신": "구아이페네신",
    "구아이페니신": "구아이페네신",
    "구아에페네신": "구아이페네신",
    "페닐레프린염산염": "페닐레프린염산염",
    "페닐레프린염산없": "페닐레프린염산염",
    "페날레프린염산염": "페닐레프린염산염",
    "폐날레프린염산없": "페닐레프린염산염",
    "클로르페니라민말레산염": "클로르페니라민말레산염",
    "클로르페니라민말레산없": "클로르페니라민말레산염",
    "클로로페니라민말레산염": "클로르페니라민말레산염",
    "클로로페니라민말레산없": "클로르페니라민말레산염",
    "킬로로떼니라민말레산없": "클로르페니라민말레산염",
    "킬로로페니라민말레산염": "클로르페니라민말레산염",
    "와파린": "와파린",
    "warfarin": "와파린",
    "이부프로펜": "이부프로펜",
    "ibuprofen": "이부프로펜",
    "덱시부프로펜": "덱시부프로펜",
    "dexibuprofen": "덱시부프로펜",
    "나프록센": "나프록센",
    "naproxen": "나프록센",
    "아스피린": "아스피린",
    "aspirin": "아스피린",
    "카페인무수물": "카페인무수물",
    "펜톡시베린시트르산염": "펜톡시베린시트르산염",
    "펜톡시베린시트르산없": "펜톡시베린시트르산염",
    "슈도에페드린염산염": "슈도에페드린염산염",
    "슈도에페드린염산없": "슈도에페드린염산염",
    "디펜히드라민염산염": "디펜히드라민염산염",
    "디펜히드라민염산없": "디펜히드라민염산염",
    "dl메틸에페드린염산염": "DL-메틸에페드린염산염",
    "dl메틸에페드린염산없": "DL-메틸에페드린염산염",
    "리보플라빈포스페이트나트륨": "리보플라빈포스페이트나트륨",
    "시메티콘": "시메티콘",
    "인산알루미늄겔": "인산알루미늄겔",
    "수산화마그네슘": "수산화마그네슘",
    "이소프로필안티피린": "이소프로필안티피린",
    "파마브롬": "파마브롬",
    "아세트아미노펜과립": "아세트아미노펜",
    "아세트아미노펜미분화": "아세트아미노펜",
}

_INGREDIENT_CANONICAL_KEYS = {
    _normalize_ingredient_key(name): name for name in _INGREDIENT_CANONICAL_NAMES
}
for alias, canonical in list(_INGREDIENT_ALIAS_MAP.items()):
    _INGREDIENT_ALIAS_MAP[_normalize_ingredient_key(alias)] = canonical


def _clean_ingredient_fragment(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\[.*?\]|\(.*?\)|\{.*?\}", " ", text)
    text = re.sub(r"\b(?:KP|JP|USP|EP|BP|CP)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\d+(?:\.\d+)?\s*(?:mg|g|ml|mL|mcg|㎎|㎖|%)", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[^0-9a-zA-Z가-힣]+", " ", text)
    return " ".join(text.split()).strip()


def _canonicalize_ingredient_name(value: str) -> str:
    cleaned = _clean_ingredient_fragment(value)
    if not cleaned:
        return ""

    stop_keys = {_normalize_ingredient_key(x) for x in _INGREDIENT_STOPWORDS}
    key = _normalize_ingredient_key(cleaned)
    if not key or key in stop_keys:
        return ""

    direct = _INGREDIENT_ALIAS_MAP.get(key) or _INGREDIENT_CANONICAL_KEYS.get(key)
    if direct:
        return direct

    best_name = ""
    best_score = 0.0
    for candidate_key, candidate_name in _INGREDIENT_CANONICAL_KEYS.items():
        if not candidate_key:
            continue
        if key and candidate_key and key[0] != candidate_key[0]:
            continue
        if abs(len(candidate_key) - len(key)) > 5:
            continue
        score = SequenceMatcher(None, key, candidate_key).ratio()
        if score > best_score:
            best_score = score
            best_name = candidate_name

    if best_score >= 0.74:
        return best_name

    if len(cleaned) < 3:
        return ""
    return cleaned


def _extract_canonical_ingredients_from_fragment(value: str) -> List[str]:
    cleaned = _clean_ingredient_fragment(value)
    if not cleaned:
        return []

    hits: List[str] = []
    seen = set()

    def add_item(item: str):
        item = _canonicalize_ingredient_name(item)
        key = _normalize_ingredient_key(item)
        if not item or not key or key in seen:
            return
        seen.add(key)
        hits.append(item)

    # 전체 문구를 먼저 정규화
    add_item(cleaned)

    # OCR 잔여 문구 안에 알려진 성분 별칭이 끼어 있으면 포함 매칭으로 정리
    normalized = _normalize_ingredient_key(cleaned)
    alias_items = sorted(
        [(k, v) for k, v in _INGREDIENT_ALIAS_MAP.items() if k and len(k) >= 4],
        key=lambda x: len(x[0]),
        reverse=True,
    )
    for alias_key, canonical in alias_items:
        if alias_key in normalized:
            add_item(canonical)

    # 토큰 단위로도 다시 정규화
    for token in re.findall(r"[0-9A-Za-z가-힣]{3,40}", cleaned):
        add_item(token)

    return hits


def _looks_like_ingredient_token(value: str) -> bool:
    token = str(value or "").strip()
    if not token or " " in token:
        return False
    key = _normalize_ingredient_key(token)
    if not key or len(key) > 28:
        return False
    if key in _INGREDIENT_CANONICAL_KEYS:
        return True
    if re.search(r"(염산염|말레산염|시트르산염|브롬화수소산염수화물|브롬화수소산염|수화물|무수물|나트륨|칼륨|칼슘|겔|과립)$", token):
        return True
    return False


def _split_ingredient_text(value: str) -> List[str]:
    if not value:
        return []
    parts = re.split(r"[,\n/|·]+", str(value))
    cleaned = []
    seen = set()
    for part in parts:
        item = _canonicalize_ingredient_name(part)
        key = _normalize_ingredient_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return cleaned


def _merge_ingredient_lists(groups: List[List[str]]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for group in groups or []:
        for item in group or []:
            for text in _extract_canonical_ingredients_from_fragment(item):
                key = _normalize_ingredient_key(text)
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(text)
    return merged


def _join_ingredients_text(items: List[str]) -> str:
    return ", ".join(_merge_ingredient_lists([items]))


_OCR_NORMALIZE_PHRASES = {
    "유 효 성 분": "유효성분",
    "유 요 성 분": "유요성분",
    "원 료 약 품": "원료약품",
    "의 약 품": "의약품",
    "일 반 의 약 품": "일반의약품",
    "일 반 안 전 상 비 의 약 품": "일반안전상비의약품",
}


def _compact_single_syllable_runs(text: str) -> str:
    lines = []
    for raw_line in str(text or "").replace("\\r", "\\n").split("\\n"):
        tokens = raw_line.split()
        if not tokens:
            lines.append("")
            continue

        rebuilt = []
        buffer = []

        def flush_buffer():
            nonlocal buffer
            if len(buffer) >= 3:
                rebuilt.append("".join(buffer))
            else:
                rebuilt.extend(buffer)
            buffer = []

        for token in tokens:
            if re.fullmatch(r"[가-힣]", token):
                buffer.append(token)
                continue
            flush_buffer()
            rebuilt.append(token)
        flush_buffer()
        lines.append(" ".join(rebuilt).strip())
    return "\\n".join(lines).strip()


def _normalize_ocr_text_variants(raw_text: str) -> List[str]:
    base = str(raw_text or "").replace("\\r", "\\n").strip()
    if not base:
        return []

    variants = [base]
    compact = _compact_single_syllable_runs(base)
    if compact and compact not in variants:
        variants.append(compact)

    phrase_fixed = compact
    for before, after in _OCR_NORMALIZE_PHRASES.items():
        phrase_fixed = phrase_fixed.replace(before, after)
    if phrase_fixed and phrase_fixed not in variants:
        variants.append(phrase_fixed)

    no_space_hints = phrase_fixed
    no_space_hints = re.sub(r"(?<=유효)\s+(?=성분)", "", no_space_hints)
    no_space_hints = re.sub(r"(?<=원료)\s+(?=약품)", "", no_space_hints)
    if no_space_hints and no_space_hints not in variants:
        variants.append(no_space_hints)

    return _dedupe_keep_order([v for v in variants if str(v).strip()])


PRODUCT_INGREDIENT_HINTS = {
    "타이레놀": ["아세트아미노펜"],
    "타이레놀정": ["아세트아미노펜"],
    "어린이타이레놀": ["아세트아미노펜"],
    "수바스트": ["로수바스타틴"],
    "수바스트정": ["로수바스타틴"],
    "판콜": ["아세트아미노펜", "구아이페네신", "페닐레프린염산염", "클로르페니라민말레산염"],
    "판피린": ["아세트아미노펜", "카페인무수물"],
    "게보린": ["아세트아미노펜", "카페인무수물"],
    "이지엔": ["이부프로펜"],
    "부루펜": ["이부프로펜"],
    "애드빌": ["이부프로펜"],
    "탁센": ["나프록센"],
    "와파린": ["와파린"],
}


_PRODUCT_LINE_STOPWORDS = {
    "유효성분", "유요성분", "원료약품", "성분", "분량", "효능", "효과", "용법", "용량", "권장", "유지",
    "초회용량", "증량", "주의", "경고", "저장", "복용", "증상", "식이요법", "보조제", "상비약", "RFID",
    "제조", "전문", "일반의약품", "상담", "약사", "의사", "환자", "참조",
}


def _clean_product_candidate_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\([^\)]*\)", " ", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:mg|g|mcg|mL|ml|㎎|%)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[^0-9A-Za-z가-힣]+", " ", text)
    return " ".join(text.split()).strip()


def _looks_like_product_candidate(value: str) -> bool:
    text = _clean_product_candidate_text(value)
    if not text:
        return False
    key = _normalize_ingredient_key(text)
    if not key or len(key) < 2 or len(key) > 24:
        return False
    if any(word in text for word in _PRODUCT_LINE_STOPWORDS):
        return False
    if re.search(r"(1일|1회|최대|초회|권장|유지|증량)", text):
        return False
    if len(re.findall(r"[가-힣]", text)) < 2:
        return False
    if len(re.findall(r"\d", text)) > 4:
        return False
    return True


def _extract_product_name_candidates_from_text(raw_text: str) -> List[str]:
    variants = _normalize_ocr_text_variants(raw_text)
    if not variants:
        return []

    found: List[str] = []
    for text in variants:
        lines = [" ".join(line.split()).strip() for line in str(text).split("\n")]
        lines = [line for line in lines if line]

        for line in lines:
            cleaned = _clean_product_candidate_text(line)
            if not cleaned:
                continue

            if len(cleaned) <= 20 and _looks_like_product_candidate(cleaned):
                found.append(cleaned)

            parts = cleaned.split()
            for chunk in parts[:2]:
                if len(chunk) <= 16 and _looks_like_product_candidate(chunk):
                    found.append(chunk)

            for match in re.finditer(r"([가-힣A-Za-z]{2,20}(?:정|캡슐|연질캡슐|시럽|현탁액|과립|액|주)?)", cleaned):
                token = match.group(1)
                if _looks_like_product_candidate(token):
                    found.append(token)

        normalized_text = _normalize_ingredient_key(text)
        if normalized_text:
            for product_name in PRODUCT_INGREDIENT_HINTS.keys():
                product_key = _normalize_ingredient_key(product_name)
                if product_key and product_key in normalized_text:
                    found.append(product_name)

    return _dedupe_keep_order(found)


def _resolved_rank(resolved: Dict) -> tuple:
    if not resolved:
        return (0, 0.0, 0)
    name = str(resolved.get("resolved_name") or resolved.get("suggested_name") or "")
    auto = 1 if resolved.get("auto_applied") and name else 0
    confidence = float(resolved.get("confidence") or 0.0)
    return (auto, confidence, len(name))


def _text_similarity(left: str, right: str) -> float:
    left_key = _normalize_ingredient_key(left)
    right_key = _normalize_ingredient_key(right)
    if not left_key or not right_key:
        return 0.0
    return SequenceMatcher(None, left_key, right_key).ratio()


def _filter_match_candidates(candidates: List[str], raw_text: str, product_name_candidates: List[str], rule_corrected: str = "") -> List[str]:
    ordered = _dedupe_keep_order([str(item or "").strip() for item in candidates or [] if str(item or "").strip()])
    if not ordered:
        return []

    support_tokens = _dedupe_keep_order([
        *(product_name_candidates or []),
        rule_corrected,
        *re.findall(r"[0-9A-Za-z가-힣]{2,20}", str(raw_text or "")),
    ])
    support_keys = {_normalize_ingredient_key(item) for item in support_tokens if _normalize_ingredient_key(item)}

    filtered: List[str] = []
    for cand in ordered:
        cand_key = _normalize_ingredient_key(cand)
        if not cand_key:
            continue
        supported = False
        if cand_key in support_keys:
            supported = True
        elif any(token and (cand_key in _normalize_ingredient_key(token) or _normalize_ingredient_key(token) in cand_key) for token in support_tokens):
            supported = True
        else:
            best = max((_text_similarity(cand, token) for token in support_tokens if str(token or "").strip()), default=0.0)
            if best >= 0.72:
                supported = True
        if supported:
            if _normalize_ingredient_key(cand) == _normalize_ingredient_key("와파린") and not _has_explicit_warfarin_evidence(raw_text, rule_corrected, cand, *product_name_candidates):
                continue
            filtered.append(cand)

    if filtered:
        return _dedupe_keep_order(filtered)[:5]

    return []


def _catalog_name_is_safe(
    catalog_hit: Dict,
    resolved: Dict,
    observed_ingredients: List[str],
    raw_text: str,
    product_name_candidates: List[str],
    rule_corrected: str = "",
) -> bool:
    display_name = str(catalog_hit.get("display_name") or "").strip()
    if not display_name:
        return False

    mode = str(catalog_hit.get("mode") or "")
    if mode == "catalog_exact_product":
        return True

    support_tokens = _dedupe_keep_order([
        *(product_name_candidates or []),
        rule_corrected,
        str(resolved.get("resolved_name") or "").strip(),
        str(resolved.get("suggested_name") or "").strip(),
    ])
    display_key = _normalize_ingredient_key(display_name)
    support_hit = False
    for token in support_tokens:
        token_key = _normalize_ingredient_key(token)
        if not token_key or len(token_key) < 3:
            continue
        if token_key in display_key or display_key in token_key:
            support_hit = True
            break
    best_support = max((_text_similarity(display_name, token) for token in support_tokens if str(token or "").strip()), default=0.0)
    confidence = float(resolved.get("confidence") or 0.0)
    observed_count = len(_merge_ingredient_lists([observed_ingredients or []]))

    if mode == "catalog_unique":
        if resolved.get("auto_applied") and confidence >= 0.90:
            return True
        if support_hit and observed_count >= 1:
            return True
        if best_support >= 0.58 and (observed_count >= 1 or confidence >= 0.94):
            return True
        return False

    return False


def _resolve_best_from_name_candidates(raw_text: str, *preferred_names: str) -> Dict:
    candidates = _dedupe_keep_order([
        *(str(name or "").strip() for name in preferred_names if str(name or "").strip()),
        *_extract_product_name_candidates_from_text(raw_text),
    ])

    best = {
        "resolved_name": "",
        "suggested_name": "",
        "confidence": 0.0,
        "candidates": [],
        "note": "empty",
        "auto_applied": False,
        "needs_confirm": True,
        "used_seed": "",
    }

    for candidate in candidates[:12]:
        resolved = dict(resolve_drug_name(candidate))
        resolved["used_seed"] = candidate
        if _resolved_rank(resolved) > _resolved_rank(best):
            best = resolved

    if candidates and not best.get("used_seed"):
        best["used_seed"] = candidates[0]
    return best


def _extract_ingredient_alias_hits_from_text(raw_text: str) -> List[str]:
    text = str(raw_text or "")
    if not text.strip():
        return []

    normalized_text = _normalize_ingredient_key(text)
    if not normalized_text:
        return []

    hits: List[str] = []

    # 긴 성분명부터 확인해서 짧은 잡문 매칭을 줄인다.
    alias_items = []
    for alias_key, canonical in _INGREDIENT_ALIAS_MAP.items():
        if not alias_key or len(alias_key) < 4:
            continue
        alias_items.append((alias_key, canonical))
    alias_items.sort(key=lambda x: len(x[0]), reverse=True)

    for alias_key, canonical in alias_items:
        if alias_key in normalized_text:
            hits.append(canonical)

    # OCR이 한 글자 정도 틀린 경우를 위해 토큰 단위 퍼지 매칭을 한 번 더 수행
    tokens = re.findall(r"[A-Za-z가-힣]{4,30}", text)
    for token in tokens:
        canonical = _canonicalize_ingredient_name(token)
        key = _normalize_ingredient_key(canonical)
        if key and key in _INGREDIENT_CANONICAL_KEYS:
            hits.append(canonical)

    return _merge_ingredient_lists([hits])


def _infer_ingredients_from_product_name(*names: str) -> List[str]:
    found: List[str] = []
    hint_items = [
        (_normalize_ingredient_key(product_name), product_name, ingredients)
        for product_name, ingredients in PRODUCT_INGREDIENT_HINTS.items()
        if _normalize_ingredient_key(product_name)
    ]

    expanded_names = _dedupe_keep_order([
        *(str(name or "").strip() for name in names if str(name or "").strip()),
        *[cand for name in names for cand in _extract_product_name_candidates_from_text(str(name or ""))],
    ])

    for name in expanded_names:
        text = str(name or "").strip()
        if not text:
            continue

        for canonical in _INGREDIENT_CANONICAL_NAMES:
            if canonical and canonical in text:
                found.append(canonical)

        normalized_name = _normalize_ingredient_key(text)
        if not normalized_name:
            continue

        for product_key, _, ingredients in hint_items:
            if product_key and (product_key in normalized_name or normalized_name in product_key):
                found.extend(ingredients)

        tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", text)
        normalized_tokens = [_normalize_ingredient_key(tok) for tok in tokens if _normalize_ingredient_key(tok)]
        for token in normalized_tokens:
            for product_key, _, ingredients in hint_items:
                if len(token) >= 2 and (token in product_key or product_key in token):
                    found.extend(ingredients)

        best_ingredients = []
        best_score = 0.0
        for product_key, _, ingredients in hint_items:
            if abs(len(product_key) - len(normalized_name)) > 8:
                continue
            score = SequenceMatcher(None, normalized_name, product_key).ratio()
            if score > best_score:
                best_score = score
                best_ingredients = ingredients
        if best_score >= 0.88:
            found.extend(best_ingredients)

    return _merge_ingredient_lists([found])




def _catalog_enrich_from_candidates(raw_text: str, observed_ingredients: List[str], *name_candidates: str) -> Dict:
    candidates = _dedupe_keep_order([
        *(str(name or "").strip() for name in name_candidates if str(name or "").strip()),
        *_extract_product_name_candidates_from_text(raw_text),
    ])
    return lookup_catalog_by_candidates(candidates, observed_ingredients=observed_ingredients)


def _complete_active_ingredients(raw_text: str, current: List[str], *name_candidates: str, trusted_catalog_ingredients: Optional[List[str]] = None) -> List[str]:
    trusted = _merge_ingredient_lists([trusted_catalog_ingredients or []])
    if trusted:
        return trusted

    current_list = _merge_ingredient_lists([current or []])
    text_hits = _extract_ingredient_alias_hits_from_text(raw_text)
    product_candidates = _extract_product_name_candidates_from_text(raw_text)
    name_hits = _infer_ingredients_from_product_name(*name_candidates, *product_candidates)

    return _merge_ingredient_lists([current_list, text_hits, name_hits])




def _has_explicit_warfarin_evidence(*texts: str) -> bool:
    """와파린은 오검출 비용이 커서 '강한 증거'가 있을 때만 인정한다.
    - 명시 이름 + 함량/제형/나트륨 표기
    - 또는 서로 다른 텍스트에서 2회 이상 독립적으로 명시
    """
    cleaned_texts = [str(text or "").strip() for text in texts if str(text or "").strip()]
    if not cleaned_texts:
        return False

    explicit_count = 0
    for text in cleaned_texts:
        if not re.search(r"(와파린|warfarin|쿠마딘)", text, re.IGNORECASE):
            continue
        explicit_count += 1
        if re.search(r"(?:와파린|warfarin|쿠마딘).{0,14}(?:\d+(?:\.\d+)?\s*(?:mg|밀리그램)|나트륨|sodium|정)", text, re.IGNORECASE):
            return True
        if re.search(r"(?:\d+(?:\.\d+)?\s*(?:mg|밀리그램)|나트륨|sodium|정).{0,14}(?:와파린|warfarin|쿠마딘)", text, re.IGNORECASE):
            return True

    return explicit_count >= 2


def _remove_unverified_high_risk_ingredients(ingredients: List[str], *evidence_texts: str) -> List[str]:
    merged = _merge_ingredient_lists([ingredients or []])
    if _has_explicit_warfarin_evidence(*evidence_texts):
        return merged
    return [item for item in merged if _normalize_ingredient_key(item) != _normalize_ingredient_key("와파린")]


def _strip_warfarin_when_conflicting_context(
    ingredients: List[str],
    raw_text: str,
    catalog_ingredients: Optional[List[str]] = None,
    product_name_candidates: Optional[List[str]] = None,
    rule_corrected: str = "",
) -> List[str]:
    merged = _merge_ingredient_lists([ingredients or []])
    if not any(_normalize_ingredient_key(x) == _normalize_ingredient_key("와파린") for x in merged):
        return merged

    trusted_catalog = _merge_ingredient_lists([catalog_ingredients or []])
    if trusted_catalog and not any(_normalize_ingredient_key(x) == _normalize_ingredient_key("와파린") for x in trusted_catalog):
        return [item for item in merged if _normalize_ingredient_key(item) != _normalize_ingredient_key("와파린")]

    evidence_pool = "\n".join([str(raw_text or ""), str(rule_corrected or ""), *[str(x or "") for x in (product_name_candidates or [])]])
    has_tylenol_context = bool(re.search(r"(타이레놀|tylenol)", evidence_pool, re.IGNORECASE))
    has_acetaminophen = any(_normalize_ingredient_key(x) == _normalize_ingredient_key("아세트아미노펜") for x in merged)
    if has_tylenol_context and has_acetaminophen:
        return [item for item in merged if _normalize_ingredient_key(item) != _normalize_ingredient_key("와파린")]

    if has_acetaminophen and not _has_explicit_warfarin_evidence(raw_text):
        return [item for item in merged if _normalize_ingredient_key(item) != _normalize_ingredient_key("와파린")]

    return merged

def _make_public_product_name(analyzed: Dict) -> str:
    corrected = str(analyzed.get("corrected_name") or "").strip()
    if corrected:
        return corrected
    return ""


def _make_explanation_subject(analyzed: Dict) -> str:
    public_name = _make_public_product_name(analyzed)
    if public_name:
        return public_name

    ingredients = _merge_ingredient_lists([analyzed.get("active_ingredients", []) or []])
    if ingredients:
        if len(ingredients) == 1:
            return f"{ingredients[0]} 성분 약"
        return f"{ingredients[0]} 포함 약"

    return "이 약"


def _make_unique_medicine_name(cursor, nickname: str, medicine_name: str) -> str:
    base_name = (medicine_name or "").strip()
    if not base_name:
        return ""
    cursor.execute(
        "SELECT medicine_name FROM user_medicines WHERE nickname = ?",
        (nickname,),
    )
    existing_names = {str(row[0] or "").strip() for row in cursor.fetchall()}
    if base_name not in existing_names:
        return base_name
    idx = 2
    while True:
        candidate = f"{base_name} ({idx})"
        if candidate not in existing_names:
            return candidate
        idx += 1


def _save_user_medicine(
    nickname: str,
    medicine_name: str,
    active_ingredients: Optional[List[str]] = None,
    source_type: str = "manual",
    ocr_text: str = "",
) -> bool:
    nickname = (nickname or "").strip()
    medicine_name = (medicine_name or "").strip()
    if not nickname or not medicine_name:
        return False

    new_ingredients = _merge_ingredient_lists([active_ingredients or []])
    ingredients_text = _join_ingredients_text(new_ingredients)
    source_type = (source_type or "manual").strip() or "manual"
    ocr_text = (ocr_text or "").strip()

    conn = _connect()
    cursor = conn.cursor()
    unique_name = _make_unique_medicine_name(cursor, nickname, medicine_name)
    cursor.execute(
        """
        INSERT INTO user_medicines (nickname, medicine_name, active_ingredients, source_type, ocr_text)
        VALUES (?, ?, ?, ?, ?)
        """,
        (nickname, unique_name, ingredients_text, source_type, ocr_text),
    )
    conn.commit()
    conn.close()
    return True


def _get_user_medicine_details(nickname: str) -> List[Dict]:
    nickname = (nickname or "").strip()
    if not nickname:
        return []

    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, medicine_name, COALESCE(active_ingredients, ''), COALESCE(source_type, 'manual'), COALESCE(ocr_text, '')
        FROM user_medicines
        WHERE nickname = ?
        ORDER BY id DESC
        """,
        (nickname,),
    )
    rows = cursor.fetchall()
    conn.close()

    items = []
    for row_id, medicine_name, active_ingredients_text, source_type, ocr_text in rows:
        normalized_ingredients = _split_ingredient_text(active_ingredients_text)
        display_name = medicine_name
        if (source_type or "manual") != "manual" and str(medicine_name or "").startswith("성분 기반 등록"):
            display_name = _make_medicine_label("", normalized_ingredients)
        items.append(
            {
                "id": row_id,
                "name": display_name,
                "active_ingredients": normalized_ingredients,
                "source_type": source_type or "manual",
                "ocr_text": ocr_text or "",
            }
        )
    return items


def _build_ingredient_compare(new_ingredients: List[str], saved_current_items: List[Dict], current_review_items: List[Dict]) -> Dict:
    new_list = _merge_ingredient_lists([new_ingredients or []])
    current_sources: List[Dict] = []

    for item in saved_current_items or []:
        ingredients = _merge_ingredient_lists([item.get("active_ingredients", [])])
        if not ingredients:
            continue
        current_sources.append(
            {
                "label": item.get("name") or "현재 복용 약",
                "ingredients": ingredients,
                "source_type": item.get("source_type") or "saved",
            }
        )

    for item in current_review_items or []:
        ingredients = _merge_ingredient_lists([item.get("active_ingredients", [])])
        if not ingredients:
            continue
        current_sources.append(
            {
                "label": item.get("saved_name") or item.get("corrected_name") or item.get("file_name") or "현재 약 사진",
                "ingredients": ingredients,
                "source_type": "image",
            }
        )

    current_all = _merge_ingredient_lists([src.get("ingredients", []) for src in current_sources])
    current_key_map = {_normalize_ingredient_key(item): item for item in current_all}
    overlap = [item for item in new_list if _normalize_ingredient_key(item) in current_key_map]

    return {
        "new_active_ingredients": new_list,
        "current_active_ingredients": current_all,
        "overlap_active_ingredients": overlap,
        "current_ingredient_sources": current_sources,
        "has_basis": bool(new_list) and bool(current_all),
    }


INGREDIENT_CANONICAL_MAP = {
    "와파린": "warfarin",
    "warfarin": "warfarin",
    "warfarinsodium": "warfarin",
    "warfarinsodiumclathrate": "warfarin",
    "아세트아미노펜": "acetaminophen",
    "acetaminophen": "acetaminophen",
    "paracetamol": "acetaminophen",
    "apap": "acetaminophen",
    "이부프로펜": "ibuprofen",
    "ibuprofen": "ibuprofen",
    "덱시부프로펜": "dexibuprofen",
    "dexibuprofen": "dexibuprofen",
    "나프록센": "naproxen",
    "naproxen": "naproxen",
    "아스피린": "aspirin",
    "aspirin": "aspirin",
    "디클로페낙": "diclofenac",
    "diclofenac": "diclofenac",
    "케토프로펜": "ketoprofen",
    "ketoprofen": "ketoprofen",
}


def _canonicalize_ingredient(value: str) -> str:
    key = _normalize_ingredient_key(value)
    return INGREDIENT_CANONICAL_MAP.get(key, key)


INTERACTION_RULES = [
    {
        "name": "와파린 + 아세트아미노펜",
        "current_keys": {"warfarin"},
        "new_keys": {"acetaminophen"},
        "risk": "주의",
        "reason": "와파린 복용 중 아세트아미노펜은 반복 복용·고용량 복용 시 INR 상승과 출혈 위험을 높일 수 있어 주의가 필요합니다.",
    },
    {
        "name": "와파린 + NSAIDs",
        "current_keys": {"warfarin"},
        "new_keys": {"ibuprofen", "dexibuprofen", "naproxen", "aspirin", "diclofenac", "ketoprofen"},
        "risk": "위험",
        "reason": "와파린과 소염진통제(NSAIDs) 조합은 출혈 위험을 높일 수 있어 전문가 확인 전 병용을 피하는 편이 안전합니다.",
    },
]


RISK_PRIORITY = {"특이사항 없음": 0, "주의": 1, "위험": 2}


def _max_risk(*risks: str) -> str:
    best = "특이사항 없음"
    for risk in risks:
        if RISK_PRIORITY.get(risk, -1) > RISK_PRIORITY.get(best, -1):
            best = risk
    return best


def _make_medicine_label(medicine_name: str, active_ingredients: List[str]) -> str:
    medicine_name = (medicine_name or "").strip()
    if medicine_name:
        return medicine_name

    ingredients = _merge_ingredient_lists([active_ingredients or []])
    if ingredients:
        preview = ", ".join(ingredients[:2])
        if len(ingredients) > 2:
            preview += f" 외 {len(ingredients) - 2}개"
        return f"성분 기반 등록 ({preview})"
    return "성분 기반 등록"


def _filter_saved_current_items(saved_items: List[Dict], selected_ids: List[str]) -> List[Dict]:
    if not selected_ids:
        return saved_items

    wanted = {str(x).strip() for x in selected_ids if str(x).strip()}
    if not wanted:
        return saved_items

    return [item for item in saved_items if str(item.get("id")) in wanted]


def _find_ingredient_rule_matches(current_ingredients: List[str], new_ingredients: List[str]) -> List[Dict]:
    current_items = [(item, _canonicalize_ingredient(item)) for item in _merge_ingredient_lists([current_ingredients or []])]
    new_items = [(item, _canonicalize_ingredient(item)) for item in _merge_ingredient_lists([new_ingredients or []])]

    matches: List[Dict] = []
    seen = set()
    for current_text, current_key in current_items:
        for new_text, new_key in new_items:
            for rule in INTERACTION_RULES:
                hit = (
                    current_key in rule["current_keys"] and new_key in rule["new_keys"]
                ) or (
                    new_key in rule["current_keys"] and current_key in rule["new_keys"]
                )
                if not hit:
                    continue
                sig = (rule["name"], current_key, new_key)
                if sig in seen:
                    continue
                seen.add(sig)
                matches.append(
                    {
                        "name": rule["name"],
                        "risk": rule["risk"],
                        "reason": rule["reason"],
                        "current_ingredient": current_text,
                        "new_ingredient": new_text,
                    }
                )
    return matches


def _combine_reason_text(base_reason: str, extra_reasons: List[str]) -> str:
    parts = []
    for item in [base_reason, *(extra_reasons or [])]:
        text = str(item or "").strip()
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts)


def extract_active_ingredients(raw_text: str) -> List[str]:
    """OCR 텍스트에서 유효성분만 보수적으로 추출하되, OCR 공백 깨짐 변형도 함께 본다."""
    variants = _normalize_ocr_text_variants(raw_text)
    if not variants:
        return []

    anchor_keywords = ("유효성분", "유요성분", "원료약품", "원료 약품", "성분")
    stop_keywords = ("효능", "효과", "용법", "용량", "주의사항", "사용상의주의사항", "저장", "보관")
    found: List[str] = []

    for text in variants:
        lines = [" ".join(line.split()).strip() for line in str(text).split("\n")]
        lines = [line for line in lines if line]

        candidate_lines: List[str] = []
        for idx, line in enumerate(lines):
            if any(keyword in line for keyword in anchor_keywords):
                collected = [line]
                for next_idx in range(idx + 1, min(len(lines), idx + 5)):
                    next_line = lines[next_idx]
                    if any(stop in next_line for stop in stop_keywords):
                        break
                    if len(next_line) > 110:
                        break
                    collected.append(next_line)
                candidate_lines.extend(collected)

        if not candidate_lines:
            inline_matches = re.findall(r"(?:유효성분|원료약품|원료 약품|성분).{0,220}", text)
            candidate_lines.extend(inline_matches[:3])

        if not candidate_lines:
            candidate_lines = lines[:6]

        candidate_text = "\n".join(candidate_lines).strip()
        stop_match = re.search(r"(효능|효과|용법|용량|주의사항|사용상의주의사항)", candidate_text, flags=re.IGNORECASE)
        if stop_match:
            candidate_text = candidate_text[: stop_match.start()]

        patterns = [
            re.compile(r"([가-힣A-Za-z]{2,40})\s*\((?:KP|JP|USP|EP|BP|CP)\)", re.IGNORECASE),
            re.compile(r"([가-힣A-Za-z]{2,40})\s*(?:KP|JP|USP|EP|BP|CP)\b", re.IGNORECASE),
            re.compile(r"([가-힣A-Za-z]{2,40})\s*\d+(?:\.\d+)?\s*(?:mg|g|mcg|mL|ml|㎎)", re.IGNORECASE),
            re.compile(r"([가-힣A-Za-z]{2,40}(?:염산염|말레산염|시트르산염|브롬화수소산염수화물|브롬화수소산염|수화물|무수물|나트륨|칼륨|칼슘))", re.IGNORECASE),
        ]
        for pattern in patterns:
            for match in pattern.finditer(candidate_text):
                token = _canonicalize_ingredient_name(match.group(1))
                if token and _looks_like_ingredient_token(token):
                    found.append(token)

        found.extend(_extract_ingredient_alias_hits_from_text(candidate_text))

        for canonical in _INGREDIENT_CANONICAL_NAMES:
            if canonical in candidate_text:
                found.append(canonical)

    uniq = []
    seen = set()
    stop_keys = {_normalize_ingredient_key(x) for x in _INGREDIENT_STOPWORDS}
    for token in found:
        key = _normalize_ingredient_key(token)
        if not key or key in seen or key in stop_keys:
            continue
        seen.add(key)
        uniq.append(token)
    return uniq[:10]

def _pick_confirmed_name(
    resolved: Dict,
    active_ingredients: List[str],
    catalog_hit: Optional[Dict] = None,
    raw_text: str = "",
    product_name_candidates: Optional[List[str]] = None,
    rule_corrected: str = "",
) -> Dict:
    """확실할 때만 제품명을 자동 확정한다. 성분 매칭은 유지하되 제품명 오확정은 막는다."""
    if resolved.get("auto_applied") and resolved.get("resolved_name"):
        return {
            "resolved": resolved,
            "confirmed_name": resolved.get("resolved_name") or "",
            "used_ingredient_fallback": False,
        }

    if _catalog_name_is_safe(
        catalog_hit or {},
        resolved,
        active_ingredients,
        raw_text,
        product_name_candidates or [],
        rule_corrected=rule_corrected,
    ):
        return {
            "resolved": resolved,
            "confirmed_name": str((catalog_hit or {}).get("display_name") or "").strip(),
            "used_ingredient_fallback": False,
        }

    return {
        "resolved": resolved,
        "confirmed_name": "",
        "used_ingredient_fallback": False,
    }


def _analyze_drug_text(raw_text: str) -> Dict:
    raw_text = (raw_text or "").strip()
    text_variants = _normalize_ocr_text_variants(raw_text)
    search_text = "\n".join(text_variants) if text_variants else raw_text
    product_name_candidates = _extract_product_name_candidates_from_text(search_text)

    active_ingredients = _merge_ingredient_lists([[
        ingredient
        for variant in (text_variants or [raw_text])
        for ingredient in extract_active_ingredients(variant)
    ]])

    rule_corrected = ""
    for variant in text_variants or [raw_text]:
        rule_corrected = correct_drug_name(variant)
        if rule_corrected:
            break

    lookup_seed = rule_corrected or search_text
    resolved = resolve_drug_name(lookup_seed)
    candidate_resolved = _resolve_best_from_name_candidates(search_text, rule_corrected, lookup_seed)
    if _resolved_rank(candidate_resolved) > _resolved_rank(resolved):
        resolved = candidate_resolved

    catalog_hit = _catalog_enrich_from_candidates(
        search_text,
        active_ingredients,
        (resolved.get("resolved_name") if resolved.get("auto_applied") else "") or "",
        rule_corrected,
        *product_name_candidates,
    )

    picked = _pick_confirmed_name(
        resolved,
        active_ingredients,
        catalog_hit=catalog_hit,
        raw_text=search_text,
        product_name_candidates=product_name_candidates,
        rule_corrected=rule_corrected,
    )
    final_resolved = picked["resolved"]
    confirmed_name = picked["confirmed_name"]

    catalog_ingredients = _merge_ingredient_lists([catalog_hit.get("ingredients", []) or []])
    active_ingredients = _merge_ingredient_lists([_complete_active_ingredients(
        search_text,
        active_ingredients,
        confirmed_name,
        final_resolved.get("resolved_name") or "",
        final_resolved.get("suggested_name") or "",
        rule_corrected,
        lookup_seed,
        *(final_resolved.get("candidates") or []),
        *product_name_candidates,
        trusted_catalog_ingredients=catalog_ingredients,
    )])
    active_ingredients = _remove_unverified_high_risk_ingredients(
        active_ingredients,
        raw_text,
        search_text,
        *product_name_candidates,
        *catalog_ingredients,
    )
    active_ingredients = _strip_warfarin_when_conflicting_context(
        active_ingredients,
        search_text,
        catalog_ingredients=catalog_ingredients,
        product_name_candidates=product_name_candidates,
        rule_corrected=rule_corrected,
    )

    filtered_candidates = _filter_match_candidates(
        final_resolved.get("candidates", []),
        search_text,
        product_name_candidates,
        rule_corrected=rule_corrected,
    )
    display_name = confirmed_name or ""
    corrected_name = confirmed_name
    note_parts = [final_resolved.get("note", "")]
    if catalog_hit.get("mode") and catalog_hit.get("mode") not in {"catalog_none", "catalog_missing", "catalog_empty"}:
        note_parts.append(catalog_hit.get("mode"))
    if len(text_variants) > 1:
        note_parts.append("ocr_spacing_normalized")
    if not corrected_name and catalog_hit.get("display_name"):
        note_parts.append("catalog_name_hidden_for_safety")
    if final_resolved.get("candidates") and not filtered_candidates:
        note_parts.append("candidate_hidden_for_safety")
    match_note = ' | '.join([x for x in note_parts if x])

    analyzed = {
        "ocr_text": raw_text,
        "rule_corrected": rule_corrected,
        "corrected_name": corrected_name,
        "display_name": display_name,
        "active_ingredients": active_ingredients,
        "match_confidence": final_resolved.get("confidence", 0),
        "match_candidates": filtered_candidates,
        "match_note": match_note,
        "auto_applied": bool(corrected_name),
        "needs_confirm": not bool(corrected_name),
        "suggested_name": corrected_name or (filtered_candidates[0] if filtered_candidates else ""),
        "used_ingredient_fallback": picked["used_ingredient_fallback"],
        "catalog_mode": catalog_hit.get("mode", "catalog_none"),
    }
    analyzed["public_name"] = _make_public_product_name(analyzed)
    return analyzed


async def _analyze_uploaded_image(upload: UploadFile) -> Dict:
    raw_text = await extract_text_from_image(upload)
    result = _analyze_drug_text(raw_text)
    result["file_name"] = upload.filename or "image"
    return result


async def _resolve_current_meds_from_images(images: Optional[List[UploadFile]]) -> Dict:
    resolved_names: List[str] = []
    review_items: List[Dict] = []

    for idx, upload in enumerate(images or []):
        item = await _analyze_uploaded_image(upload)
        item["index"] = idx + 1
        if item.get("corrected_name"):
            item["status"] = "resolved"
            resolved_names.append(item["corrected_name"])
        elif item.get("match_candidates"):
            item["status"] = "needs_confirm"
        else:
            item["status"] = "unresolved"
        review_items.append(item)

    return {
        "resolved_names": _dedupe_keep_order(resolved_names),
        "items": review_items,
        "unresolved_count": sum(1 for item in review_items if item["status"] != "resolved"),
    }


def _normalize_current_drug_names(current_drugs: List[str]) -> List[str]:
    canon_current_drugs = []
    for drug in current_drugs or []:
        resolved = resolve_drug_name(drug)
        canon_current_drugs.append(resolved.get("resolved_name") or drug)
    return _dedupe_keep_order(canon_current_drugs)


# ----------------------------------------------------
# 🔐 1. 로그인 & 자동 회원가입 API
# ----------------------------------------------------
@app.post("/login")
def login_or_signup(req: LoginRequest):
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("SELECT pin FROM users WHERE nickname = ?", (req.nickname,))
    user = cursor.fetchone()

    if user:
        if user[0] != req.pin:
            conn.close()
            return {
                "status": "fail",
                "message": (
                    f"앗! '{req.nickname}'(은)는 이미 다른 분이 사용 중이거나 PIN 번호가 틀렸습니다.\\n"
                    "신규 가입이시라면 '최은철2'처럼 다른 닉네임을 입력해주세요!"
                ),
            }
        conn.close()
        return {"status": "success", "message": f"환영합니다, {req.nickname}님!"}

    cursor.execute("INSERT INTO users (nickname, pin) VALUES (?, ?)", (req.nickname, req.pin))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"신규 가입 완료! 환영합니다, {req.nickname}님!"}


# ----------------------------------------------------
# 💊 2. 내 약통 저장/조회/삭제 API
# ----------------------------------------------------
@app.get("/meds/{nickname}")
def get_meds(nickname: str):
    items = _get_user_medicine_details(nickname)
    meds = [item.get("name") for item in items if item.get("name")]
    return {"meds": meds, "items": items}


@app.post("/add_med")
def add_med(req: MedRequest):
    nickname = (req.nickname or "").strip()
    raw_name = (req.medicine_name or "").strip()
    if not nickname or not raw_name:
        return {"status": "fail", "message": "nickname or medicine_name is empty"}

    resolved = resolve_drug_name(raw_name)
    catalog_hit = lookup_catalog_by_name(raw_name, observed_ingredients=[])
    catalog_ingredients = _merge_ingredient_lists([catalog_hit.get("ingredients", []) or []])
    if not catalog_ingredients:
        catalog_ingredients = _merge_ingredient_lists([_infer_ingredients_from_product_name(raw_name)])

    save_name = (catalog_hit.get("display_name") or resolved.get("resolved_name") or raw_name).strip()
    inserted = _save_user_medicine(
        nickname,
        save_name,
        active_ingredients=catalog_ingredients,
        source_type="manual",
        ocr_text="",
    )

    return {
        "status": "success",
        "saved_name": save_name,
        "inserted": inserted,
        "active_ingredients": catalog_ingredients,
        "match_confidence": resolved.get("confidence", 0),
        "match_candidates": resolved.get("candidates", []),
        "match_note": (catalog_hit.get("mode") or resolved.get("note", "")),
    }


@app.post("/add_med_detail")
def add_med_detail(req: MedDetailRequest):
    nickname = (req.nickname or "").strip()
    active_ingredients = _merge_ingredient_lists([req.active_ingredients or []])
    display_name = _make_medicine_label(req.medicine_name or "", active_ingredients)
    if not nickname:
        return {"status": "fail", "message": "nickname is empty"}

    inserted = _save_user_medicine(
        nickname,
        display_name,
        active_ingredients=active_ingredients,
        source_type=(req.source_type or "manual").strip() or "manual",
        ocr_text=req.ocr_text or "",
    )
    return {
        "status": "success",
        "saved_name": display_name,
        "inserted": inserted,
        "active_ingredients": active_ingredients,
    }


@app.post("/add_med_images")
async def add_med_images(
    nickname: str = Form(...),
    images: List[UploadFile] = File(...),
):
    nickname = (nickname or "").strip()
    if not nickname:
        return {"status": "fail", "message": "nickname is empty"}
    if not images:
        return {"status": "fail", "message": "images are empty"}

    items: List[Dict] = []
    saved_names: List[str] = []

    for idx, upload in enumerate(images):
        analyzed = await _analyze_uploaded_image(upload)
        item = {
            "index": idx + 1,
            "file_name": analyzed.get("file_name") or f"image_{idx+1}",
            "ocr_text": analyzed.get("ocr_text", ""),
            "rule_corrected": analyzed.get("rule_corrected", ""),
            "match_confidence": analyzed.get("match_confidence", 0),
            "match_candidates": analyzed.get("match_candidates", []),
            "match_note": analyzed.get("match_note", ""),
            "active_ingredients": analyzed.get("active_ingredients", []),
            "suggested_name": analyzed.get("suggested_name", ""),
        }

        if analyzed.get("corrected_name"):
            saved_name = analyzed["corrected_name"]
            inserted = _save_user_medicine(
                nickname,
                saved_name,
                active_ingredients=analyzed.get("active_ingredients", []),
                source_type="image",
                ocr_text=analyzed.get("ocr_text", ""),
            )
            item.update(
                {
                    "status": "saved",
                    "saved_name": saved_name,
                    "inserted": inserted,
                }
            )
            saved_names.append(saved_name)
        else:
            item.update(
                {
                    "status": "needs_confirm" if analyzed.get("match_candidates") else "unresolved",
                    "saved_name": "",
                }
            )

        items.append(item)

    saved_names = _dedupe_keep_order(saved_names)
    return {
        "status": "success",
        "saved_count": len(saved_names),
        "saved_meds": saved_names,
        "needs_confirm_count": sum(1 for item in items if item["status"] == "needs_confirm"),
        "unresolved_count": sum(1 for item in items if item["status"] == "unresolved"),
        "items": items,
    }


@app.post("/delete_med")
def delete_med(req: MedRequest):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM user_medicines WHERE nickname = ? AND medicine_name = ?",
        (req.nickname, req.medicine_name),
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
    current_drugs: List[str] = Form(default=[]),
    selected_current_ids: List[str] = Form(default=[]),
    nickname: Optional[str] = Form(None),
):
    new_drug = await _analyze_uploaded_image(image[0])

    all_saved_current_items = _get_user_medicine_details(nickname or "")
    saved_current_items = _filter_saved_current_items(all_saved_current_items, selected_current_ids)
    saved_current_names = [item.get("name") for item in saved_current_items if item.get("name")]

    current_from_text = _normalize_current_drug_names(current_drugs)
    current_from_images_result = await _resolve_current_meds_from_images(current_pill_images)
    current_from_images = current_from_images_result["resolved_names"]
    current_review_items = current_from_images_result["items"]

    canon_current_drugs = _dedupe_keep_order(saved_current_names + current_from_text + current_from_images)
    ingredient_compare = _build_ingredient_compare(
        new_drug.get("active_ingredients", []),
        saved_current_items,
        current_review_items,
    )
    overlap_ingredients = ingredient_compare["overlap_active_ingredients"]
    ingredient_rule_matches = _find_ingredient_rule_matches(
        ingredient_compare["current_active_ingredients"],
        new_drug.get("active_ingredients", []),
    )

    if new_drug.get("corrected_name") and canon_current_drugs:
        db_result = check_drug_interaction(new_drug["corrected_name"], canon_current_drugs)
    elif ingredient_compare["has_basis"]:
        if overlap_ingredients:
            db_result = {
                "risk": "주의",
                "reason": f"현재 복용 약과 새 약에서 중복 유효성분이 감지되었습니다: {', '.join(overlap_ingredients)}",
            }
        else:
            db_result = {
                "risk": "특이사항 없음",
                "reason": "현재 복용 약 유효성분과 새 약 유효성분을 비교한 결과, 중복 성분은 발견되지 않았습니다.",
            }
    elif not new_drug.get("corrected_name"):
        db_result = {
            "risk": "주의",
            "reason": "새로 먹을 약의 공식 약품명 또는 유효성분을 충분히 확정하지 못해 자동 비교가 제한적입니다.",
        }
    elif current_from_images_result["unresolved_count"] > 0 and not canon_current_drugs:
        db_result = {
            "risk": "주의",
            "reason": f"현재 복용 약 사진 {current_from_images_result['unresolved_count']}장은 자동 인식이 확정되지 않아 비교에서 제외됐습니다. 약통 등록에서 후보를 누르거나 직접 이름을 입력해 저장한 뒤 다시 분석해 주세요.",
        }
    elif not canon_current_drugs:
        db_result = {
            "risk": "특이사항 없음",
            "reason": "선택된 현재 복용 약이 없어 충돌 위험이 없습니다.",
        }
    else:
        db_result = {
            "risk": "특이사항 없음",
            "reason": "식약처 데이터베이스 상 충돌 기록이 발견되지 않았습니다.",
        }

    risk_level = db_result.get("risk", "특이사항 없음")
    reason_text = db_result.get("reason", "")

    if overlap_ingredients and risk_level != "위험":
        ingredient_reason = f"현재 복용 약과 새 약에서 중복 유효성분이 감지되었습니다: {', '.join(overlap_ingredients)}"
        risk_level = _max_risk(risk_level, "주의")
        reason_text = _combine_reason_text(reason_text, [ingredient_reason])

    if ingredient_rule_matches:
        risk_level = _max_risk(risk_level, *[item.get("risk", "특이사항 없음") for item in ingredient_rule_matches])
        reason_text = _combine_reason_text(reason_text, [item.get("reason", "") for item in ingredient_rule_matches])

    # 로컬 DB에서 안전하다고 나왔고, 이름/비교 대상이 모두 확정됐을 때만 2차 검증
    if risk_level == "특이사항 없음" and new_drug.get("corrected_name") and canon_current_drugs:
        try:
            from services.api_service import check_dur_api

            api_result = check_dur_api(new_drug["corrected_name"])
            if api_result and api_result.get("status") == "danger":
                for warning in api_result.get("warnings", []):
                    mix_drug = warning.get("mix_drug", "")
                    for current_drug in canon_current_drugs:
                        if current_drug in mix_drug or mix_drug in current_drug:
                            risk_level = "위험"
                            reason_text = _combine_reason_text(
                                reason_text,
                                [f"🚨 [식약처 실시간 API 경고] [{current_drug}] 관련 병용금기 후보가 확인되었습니다. 사유: {warning.get('warning_text')}"]
                            )
                            break
                    if risk_level == "위험":
                        break
        except Exception as e:
            print(f"⚠️ OpenAPI 통신 에러: {e}")

    explanation_subject = _make_explanation_subject(new_drug)

    compare_basis = []
    if ingredient_compare["has_basis"]:
        compare_basis.append("유효성분 비교")
    if ingredient_rule_matches:
        compare_basis.append("기본 성분 주의 규칙")
    if new_drug.get("corrected_name") and canon_current_drugs:
        compare_basis.append("제품명 병용금기 DB 비교")

    guidance = build_patient_guidance(
        subject=explanation_subject,
        risk_level=risk_level,
        reason_text=reason_text,
        new_active_ingredients=_merge_ingredient_lists([new_drug.get("active_ingredients", [])]),
        current_active_ingredients=ingredient_compare["current_active_ingredients"],
        overlap_active_ingredients=overlap_ingredients,
        compare_basis=compare_basis,
        selected_current_labels=[item.get("name") for item in saved_current_items if item.get("name")],
    )

    public_name = _make_public_product_name(new_drug)

    return {
        "ocr_text": new_drug.get("ocr_text", ""),
        "rule_corrected": new_drug.get("rule_corrected", ""),
        "corrected_name": new_drug.get("corrected_name") or new_drug.get("public_name") or "",
        "public_name": public_name,
        "explanation_subject": explanation_subject,
        "risk": risk_level,
        "reason": reason_text,
        "explanation": guidance.get("explanation", ""),
        "friendly_summary": guidance.get("friendly_summary", ""),
        "explanation_lines": guidance.get("explanation_lines", []),
        "action_items": guidance.get("action_items", []),
        "ingredient_explanations": guidance.get("ingredient_explanations", []),
        "guide_mode": guidance.get("mode", "template"),
        "match_confidence": new_drug.get("match_confidence", 0),
        "match_candidates": new_drug.get("match_candidates", []),
        "match_note": new_drug.get("match_note", ""),
        "auto_applied": new_drug.get("auto_applied", False),
        "needs_confirm": new_drug.get("needs_confirm", False),
        "suggested_name": new_drug.get("suggested_name", ""),
        "active_ingredients": _merge_ingredient_lists([new_drug.get("active_ingredients", [])]),
        "current_meds_used": canon_current_drugs,
        "current_meds_from_images": current_from_images,
        "current_meds_review": current_review_items,
        "current_meds_unresolved_count": current_from_images_result["unresolved_count"],
        "current_ingredient_sources": ingredient_compare["current_ingredient_sources"],
        "current_active_ingredients": ingredient_compare["current_active_ingredients"],
        "overlap_active_ingredients": overlap_ingredients,
        "ingredient_rule_matches": ingredient_rule_matches,
        "compare_basis": compare_basis,
        "saved_current_meds": saved_current_items,
        "selected_current_items": saved_current_items,
        "selected_current_ids": [str(item.get("id")) for item in saved_current_items if item.get("id") is not None],
        "selected_current_labels": [item.get("name") for item in saved_current_items if item.get("name")],
    }


@app.post("/analyze_select")
def analyze_select(req: AnalyzeSelectRequest):
    """사용자가 후보 약품명을 선택했을 때, 그 이름으로 다시 위험도를 계산."""

    selected = (req.selected_name or "").strip()
    if not selected:
        return {"status": "fail", "message": "selected_name is empty"}

    canon_current_drugs = _normalize_current_drug_names(req.current_drugs or [])
    current_active_ingredients = _merge_ingredient_lists([req.current_active_ingredients or []])
    new_active_ingredients = _merge_ingredient_lists([req.new_active_ingredients or []])
    ingredient_compare = _build_ingredient_compare(
        new_active_ingredients,
        [{
            "name": label,
            "active_ingredients": current_active_ingredients,
            "source_type": "saved",
        } for label in (req.selected_current_labels or ["현재 복용 약"])],
        [],
    )
    overlap_ingredients = ingredient_compare["overlap_active_ingredients"]
    ingredient_rule_matches = _find_ingredient_rule_matches(current_active_ingredients, new_active_ingredients)

    if canon_current_drugs:
        db_result = check_drug_interaction(selected, canon_current_drugs)
        risk_level = db_result.get("risk", "특이사항 없음")
        reason_text = db_result.get("reason", "")
    elif ingredient_compare["has_basis"]:
        if overlap_ingredients:
            risk_level = "주의"
            reason_text = f"현재 복용 약과 새 약에서 중복 유효성분이 감지되었습니다: {', '.join(overlap_ingredients)}"
        else:
            risk_level = "특이사항 없음"
            reason_text = "현재 복용 약 유효성분과 새 약 유효성분을 비교한 결과, 중복 성분은 발견되지 않았습니다."
    else:
        risk_level = "특이사항 없음"
        reason_text = "선택된 현재 복용 약이 없어 충돌 위험이 없습니다."

    if overlap_ingredients and risk_level != "위험":
        risk_level = _max_risk(risk_level, "주의")
        reason_text = _combine_reason_text(
            reason_text,
            [f"현재 복용 약과 새 약에서 중복 유효성분이 감지되었습니다: {', '.join(overlap_ingredients)}"],
        )

    if ingredient_rule_matches:
        risk_level = _max_risk(risk_level, *[item.get("risk", "특이사항 없음") for item in ingredient_rule_matches])
        reason_text = _combine_reason_text(reason_text, [item.get("reason", "") for item in ingredient_rule_matches])

    if risk_level == "특이사항 없음" and canon_current_drugs:
        try:
            from services.api_service import check_dur_api

            api_result = check_dur_api(selected)
            if api_result and api_result.get("status") == "danger":
                for warning in api_result.get("warnings", []):
                    mix_drug = warning.get("mix_drug", "")
                    for current_drug in canon_current_drugs:
                        if current_drug in mix_drug or mix_drug in current_drug:
                            risk_level = "위험"
                            reason_text = _combine_reason_text(
                                reason_text,
                                [f"🚨 [식약처 실시간 API 경고] [{current_drug}] 관련 병용금기 후보가 확인되었습니다. 사유: {warning.get('warning_text')}"]
                            )
                            break
                    if risk_level == "위험":
                        break
        except Exception as e:
            print(f"⚠️ OpenAPI 통신 에러: {e}")

    compare_basis = [
        *(["유효성분 비교"] if ingredient_compare["has_basis"] else []),
        *(["기본 성분 주의 규칙"] if ingredient_rule_matches else []),
        *(["제품명 병용금기 DB 비교"] if canon_current_drugs else []),
    ]

    guidance = build_patient_guidance(
        subject=selected,
        risk_level=risk_level,
        reason_text=reason_text,
        new_active_ingredients=ingredient_compare["new_active_ingredients"],
        current_active_ingredients=ingredient_compare["current_active_ingredients"],
        overlap_active_ingredients=overlap_ingredients,
        compare_basis=compare_basis,
        selected_current_labels=req.selected_current_labels or [],
    )

    return {
        "status": "success",
        "corrected_name": selected,
        "public_name": selected,
        "risk": risk_level,
        "reason": reason_text,
        "explanation": guidance.get("explanation", ""),
        "friendly_summary": guidance.get("friendly_summary", ""),
        "explanation_lines": guidance.get("explanation_lines", []),
        "action_items": guidance.get("action_items", []),
        "ingredient_explanations": guidance.get("ingredient_explanations", []),
        "guide_mode": guidance.get("mode", "template"),
        "match_note": "user_selected",
        "current_meds_used": canon_current_drugs,
        "current_active_ingredients": ingredient_compare["current_active_ingredients"],
        "new_active_ingredients": ingredient_compare["new_active_ingredients"],
        "overlap_active_ingredients": overlap_ingredients,
        "ingredient_rule_matches": ingredient_rule_matches,
        "compare_basis": compare_basis,
        "selected_current_labels": req.selected_current_labels or [],
    }
