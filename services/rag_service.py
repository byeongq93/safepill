def generate_explanation(drug_name, risk_result):

    if risk_result == "위험":
        explanation = f"{drug_name}은 특정 약물과 함께 복용 시 근육통 또는 위장 출혈 위험이 증가할 수 있습니다."
    else:
        explanation = f"{drug_name}은 일반적인 복용 기준에서 특별한 상호작용 위험이 확인되지 않았습니다."

    disclaimer = "※ 본 정보는 참고용이며, 정확한 복약 상담은 전문가와 상의하세요."

    return explanation + "\n" + disclaimer