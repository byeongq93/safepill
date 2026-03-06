import os
import sqlite3
import re
from difflib import SequenceMatcher
from typing import List, Dict, Tuple
from pathlib import Path

# ✅ DB 경로를 항상 "프로젝트 폴더 기준"으로 고정 (실행 위치에 따라 빈 DB 생성되는 문제 방지)
BASE_DIR = Path(__file__).resolve().parents[1]
_ENV_DB = os.getenv("SAFEPILL_DB_PATH")
if _ENV_DB:
    DB_PATH = str((BASE_DIR / _ENV_DB).resolve()) if not os.path.isabs(_ENV_DB) else _ENV_DB
else:
    # 진짜 데이터(safepill.db)가 있으면 우선 사용
    DB_PATH = str((BASE_DIR / "safepill.db").resolve()) if (BASE_DIR / "safepill.db").exists() else str((BASE_DIR / "pharmguard.db").resolve())

# ---- 1) 텍스트 정리(정규화) ----
_norm_re_units = re.compile(r"(mg|g|ml|mcg|㎎|㎖|정|캡슐|환|포|병)\b", re.IGNORECASE)
_norm_re_brackets = re.compile(r"\(.*?\)|\[.*?\]|\{.*?\}")  # 괄호 안 제거
_norm_re_nonword = re.compile(r"[^0-9a-zA-Z가-힣]+")

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = str(s)
    s = _norm_re_brackets.sub(" ", s)
    s = _norm_re_units.sub(" ", s)
    s = _norm_re_nonword.sub("", s)  # 공백/특수문자 제거
    return s.strip()

def extract_tokens(raw_text: str) -> List[str]:
    """OCR 결과 전체 문장에서 '약 이름 후보' 토큰만 뽑아냅니다."""
    if not raw_text:
        return []
    tokens = re.findall(r"[0-9a-zA-Z가-힣]{2,}", raw_text)
    tokens = sorted(set(tokens), key=len, reverse=True)
    return tokens[:50]

# ---- 2) DB 표준 약이름 목록 캐시 ----
_DRUG_CACHE = {
    "loaded": False,
    "norm_to_name": {},
    "firstchar_map": {}
}

def _load_drug_name_cache(force: bool = False) -> None:
    if _DRUG_CACHE["loaded"] and not force:
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT drug_a FROM dur_contraindications
        UNION
        SELECT DISTINCT drug_b FROM dur_contraindications
    """)
    names = [r[0] for r in cur.fetchall() if r and r[0]]
    conn.close()

    norm_to_name = {}
    firstchar_map = {}

    for name in names:
        n = normalize_text(name)
        if not n:
            continue
        # 같은 정규화 결과가 여러 개면 '더 긴 원본'을 표준으로 둠
        if n not in norm_to_name or len(name) > len(norm_to_name[n]):
            norm_to_name[n] = name

        fc = n[0]
        firstchar_map.setdefault(fc, []).append(name)

    _DRUG_CACHE["norm_to_name"] = norm_to_name
    _DRUG_CACHE["firstchar_map"] = firstchar_map
    _DRUG_CACHE["loaded"] = True

# ---- 3) 입력 텍스트 -> DB 표준 약이름 변환 ----
def resolve_drug_name(raw_text: str) -> Dict:
    """
    return:
      resolved_name: "자동 적용" 가능한 표준 약이름 (정확/고신뢰만)
      suggested_name: 가장 유사한 후보(저신뢰 포함) — UI에서 사용자 선택용
      confidence: 0~1 (높을수록 확신)
      candidates: 후보 3개
      note: 매칭 방식 설명
      auto_applied: bool (True면 resolved_name을 그대로 써도 안전)
      needs_confirm: bool (True면 후보 선택 UI를 띄우는 게 안전)
    """
    _load_drug_name_cache()

    raw_norm = normalize_text(raw_text)
    if not raw_norm:
        return {"resolved_name": "", "confidence": 0.0, "candidates": [], "note": "empty"}

    norm_to_name = _DRUG_CACHE["norm_to_name"]

    # 1) 토큰 기반 정확 매칭
    tokens = extract_tokens(raw_text)
    for tok in tokens:
        tnorm = normalize_text(tok)
        if tnorm in norm_to_name:
            name = norm_to_name[tnorm]
            return {
                "resolved_name": name,
                "suggested_name": name,
                "confidence": 1.0,
                "candidates": [name],
                "note": "exact(token)",
                "auto_applied": True,
                "needs_confirm": False,
            }

    # 2) 유사 매칭(첫 글자 그룹에서만 비교)
    best: Tuple[str, float] = ("", 0.0)
    scored: List[Tuple[str, float]] = []

    for tok in tokens:
        tnorm = normalize_text(tok)
        if len(tnorm) < 2:
            continue
        fc = tnorm[0]
        candidates = _DRUG_CACHE["firstchar_map"].get(fc, [])
        if not candidates:
            continue

        for cand in candidates[:5000]:
            cnorm = normalize_text(cand)
            if abs(len(cnorm) - len(tnorm)) > 8:
                continue
            score = SequenceMatcher(None, tnorm, cnorm).ratio()
            scored.append((cand, score))
            if score > best[1]:
                best = (cand, score)

    scored.sort(key=lambda x: x[1], reverse=True)
    top3 = [x[0] for x in scored[:3]]

    score = round(best[1], 3)
    best_name = best[0] if best[0] else ""

    # ✅ 핵심 정책
    # - 0.90 이상만 "자동 확정"(resolved_name)으로 적용
    # - 0.80~0.90은 후보를 보여주고 사용자가 선택(needs_confirm)
    # - 0.80 미만은 사실상 매칭 실패(no_good_match) → 절대 다른 약으로 바꿔치기 금지
    if score >= 0.90:
        return {
            "resolved_name": best_name,
            "suggested_name": best_name,
            "confidence": score,
            "candidates": top3,
            "note": "fuzzy(auto>=0.90)",
            "auto_applied": True,
            "needs_confirm": False,
        }

    if score >= 0.80:
        return {
            "resolved_name": "",  # 자동 확정 X
            "suggested_name": best_name,
            "confidence": score,
            "candidates": top3,
            "note": "fuzzy(needs_confirm 0.80~0.90)",
            "auto_applied": False,
            "needs_confirm": True,
        }

    return {
        "resolved_name": "",  # 자동 확정 X
        "suggested_name": best_name,
        "confidence": score,
        "candidates": top3,
        "note": "no_good_match",
        "auto_applied": False,
        "needs_confirm": True,
    }

# ---- 4) 병용금기 조회 (DB 정확일치) ----
def check_drug_interaction(new_drug: str, current_drugs: list = None) -> dict:
    if current_drugs is None or not current_drugs:
        return {"risk": "특이사항 없음", "reason": "현재 복용 중인 약이 없어 충돌 위험이 없습니다."}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for existing_drug in current_drugs:
        cursor.execute("""
            SELECT risk_level, reason FROM dur_contraindications
            WHERE (drug_a = ? AND drug_b = ?)
               OR (drug_a = ? AND drug_b = ?)
        """, (new_drug, existing_drug, existing_drug, new_drug))

        result = cursor.fetchone()
        if result:
            conn.close()
            return {"risk": result[0], "reason": f"[{existing_drug}] 약물과 상호작용: {result[1]}"}

    conn.close()
    return {"risk": "특이사항 없음", "reason": "식약처 데이터베이스 상 충돌 기록이 발견되지 않았습니다."}
