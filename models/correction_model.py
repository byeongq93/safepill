import re

def _extract_first_ingredient(text: str) -> str:
    """성분표(유효성분) 사진일 때, 첫 성분명을 하나 뽑아내기."""
    if not text:
        return ""
        
    # '유효성분' 또는 '유요성분'(OCR 오타) 주변 자르기
    start = text.find("유효성분")
    if start == -1:
        start = text.find("유요성분") # ✅ OCR 오타 완벽 대응
        
    if start != -1:
        cut = text[start : start + 300]
    else:
        cut = text[:300]

    # 예: 아세트아미노펜(KP), 구아이페네신(KP) 등
    m = re.search(r"([가-힣A-Za-z]{2,})\s*\((?:KP|JP|USP|EP|BP|CP)\)", cut)
    if m:
        return m.group(1)

    # 괄호 없는 KP 표기(아세트아미노펜KP 같은 OCR 케이스)
    m2 = re.search(r"([가-힣A-Za-z]{2,})\s*(?:KP|JP|USP|EP|BP|CP)", cut)
    if m2:
        return m2.group(1)

    return ""


def correct_drug_name(raw_text: str) -> str:
    """아주 간단한 규칙 기반 교정(중간 결과)."""
    if not raw_text:
        return ""

    # 제품명 키워드(보이면 우선)
    if "판콜" in raw_text:
        return "판콜"
    if "타이레놀" in raw_text:
        return "타이레놀"
    if "와파린" in raw_text or "혈전" in raw_text:
        return "와파린"

    # 성분표 사진일 가능성 (오타 포함)
    if "유효성분" in raw_text or "유요성분" in raw_text or "일반의약품" in raw_text:
        first_ing = _extract_first_ingredient(raw_text)
        if first_ing:
            return first_ing

    # 🚨 기존의 위험했던 "12글자 자르기" 완전 삭제! 
    # 차라리 빈칸을 보내서 DB 매칭기(extract_tokens)가 전체 문장을 분석하게 만듦
    return ""