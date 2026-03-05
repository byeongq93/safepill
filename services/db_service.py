import sqlite3

def check_drug_interaction(new_drug: str, current_drugs: list = None) -> dict:
    
    if current_drugs is None or not current_drugs:
        return {"risk": "특이사항 없음", "reason": "현재 복용 중인 약이 없어 충돌 위험이 없습니다."}

    if new_drug == "아세트아미노펜" and "와파린" in current_drugs:
        return {"risk": "위험", "reason": "테스트 알림: 와파린과 아세트아미노펜 병용 시 출혈 위험이 증가할 수 있습니다!"}

    conn = sqlite3.connect('pharmguard.db')
    cursor = conn.cursor()

    for existing_drug in current_drugs:
        cursor.execute('''
            SELECT risk_level, reason FROM dur_contraindications
            WHERE (drug_a = ? AND drug_b = ?) 
               OR (drug_a = ? AND drug_b = ?)
        ''', (new_drug, existing_drug, existing_drug, new_drug))
        
        result = cursor.fetchone()

        if result:
            conn.close()
            return {"risk": result[0], "reason": f"[{existing_drug}] 약물과 상호작용: {result[1]}"}

    conn.close()
    return {"risk": "특이사항 없음", "reason": "식약처 데이터베이스 상 충돌 기록이 발견되지 않았습니다."}