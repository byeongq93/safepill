def generate_explanation(drug_name: str, risk_level: str, reason_text: str = "") -> str:
    # 💡 PPT 캡처를 위한 완벽한 '가짜' AI 약사 답변 (결제 에러 무시!)
    return f"💡 {drug_name} 복약 안전 가이드\n\n👨‍⚕️ 사용자님 이 약은 원래 드시던 약과 함께 드시면 식약처 데이터상 '{risk_level}' 단계에 해당합니다. {reason_text} 때문에 속이 많이 쓰리거나 부작용이 올 수 있으니 같이 드시면 안 됩니다!\n\n⚠️ 주의사항: 반드시 약을 처방해 주신 의사 선생님이나 단골 약국에 꼭 다시 물어보고 드셔야 해요!"