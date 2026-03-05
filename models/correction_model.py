def correct_drug_name(raw_text: str) -> str:
    if "아세트" in raw_text or "일반의약품" in raw_text or "타이레놀" in raw_text:
        return "아세트아미노펜"
        
    elif "와파린" in raw_text or "혈전방지제" in raw_text:
        return "와파린"
        
    elif "판콜" in raw_text:
        return "판콜에스"
        
    else:
        return raw_text[:10] + "..."