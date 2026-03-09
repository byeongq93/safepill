from typing import Dict, List


def generate_explanation(drug_name: str, risk_level: str, reason_text: str = "") -> str:
    guidance = build_patient_guidance(
        subject=drug_name,
        risk_level=risk_level,
        reason_text=reason_text,
        new_active_ingredients=[],
        current_active_ingredients=[],
        overlap_active_ingredients=[],
        compare_basis=[],
        selected_current_labels=[],
    )
    return guidance.get("explanation", "")


def _dedupe_keep_order(items):
    seen = set()
    result = []
    for item in items or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _join_names(items, fallback="확인된 정보 없음"):
    names = _dedupe_keep_order(items)
    return ", ".join(names) if names else fallback


def _line(text: str) -> str:
    text = str(text or "").strip()
    return f"- {text}" if text else ""


def _make_action_items(risk_level: str, overlap, current_items, new_items):
    overlap = _dedupe_keep_order(overlap)
    current_items = _dedupe_keep_order(current_items)
    new_items = _dedupe_keep_order(new_items)

    actions = []
    if overlap:
        overlap_text = ", ".join(overlap)
        actions.append(f"겹치는 성분({overlap_text})이 있으면 같은 날 중복 복용을 피하기")
        actions.append("같은 증상약이라도 성분이 겹치는지 한 번 더 확인하기")
        actions.append("하루 총 복용량과 복용 간격을 임의로 늘리지 않기")
    elif risk_level == "위험":
        actions.append("실제 복약 전 반드시 약사나 의사에게 먼저 확인하기")
        actions.append("확인 전까지는 새 약을 임의로 함께 복용하지 않기")
    elif risk_level == "주의":
        actions.append("약 이름보다 유효성분이 겹치는지 먼저 확인하기")
        actions.append("증상이 비슷해도 다른 감기약·진통제를 추가로 겹쳐 먹지 않기")
    else:
        actions.append("특이사항이 없어 보여도 복용 전 성분표를 한 번 더 확인하기")

    if any("와파린" in item for item in current_items + new_items):
        actions.append("항응고제 복용 중이면 일반 감기약·진통제도 전문가와 먼저 상담하기")
    else:
        actions.append("며칠 이상 계속 먹거나 기존 질환·항응고제 복용 중이면 약사와 상담하기")

    return _dedupe_keep_order(actions)


def _fallback_guidance(subject, risk_level, reason_text, new_ing, current_ing, overlap, compare_basis, selected_current_labels):
    from services.llm_service import get_local_ingredient_explanations

    subject = (subject or "이 약").strip() or "이 약"
    new_ing = _dedupe_keep_order(new_ing)
    current_ing = _dedupe_keep_order(current_ing)
    overlap = _dedupe_keep_order(overlap)
    compare_basis = _dedupe_keep_order(compare_basis)
    selected_current_labels = _dedupe_keep_order(selected_current_labels)
    reason_text = (reason_text or "").strip()

    if overlap:
        lead = f"{subject}은(는) 바로 금지라고 단정할 단계는 아니지만, 성분 중복이나 주의 조합 가능성을 확인한 상태예요."
    elif risk_level == "위험":
        lead = f"{subject}은(는) 현재 선택한 복용 약과 함께 복용하기 전에 반드시 전문가 확인이 필요한 상태예요."
    elif risk_level == "주의":
        lead = f"{subject}은(는) 현재 복용 약과 성분이 겹치거나 주의가 필요한 조합일 수 있어요."
    else:
        lead = f"{subject}은(는) 이번 비교 기준에서는 뚜렷한 충돌이 확인되지 않았어요."

    lines = []
    if selected_current_labels:
        lines.append(_line(f"이번 결과는 {_join_names(selected_current_labels)}와의 비교를 바탕으로 나왔어요."))
    elif compare_basis:
        lines.append(_line(f"이번 결과는 {' · '.join(compare_basis)} 기준으로 확인했어요."))

    if not new_ing and overlap:
        new_ing_desc = ", ".join(overlap)
    else:
        new_ing_desc = _join_names(new_ing, "확정된 유효성분 없음")
    lines.append(_line(f"새 약에서 확인된 유효성분은 {new_ing_desc}입니다."))

    if overlap:
        lines.append(_line(f"현재 복용 약과 새 약에서 중복 유효성분이 감지되었습니다: {', '.join(overlap)}"))
    elif current_ing and new_ing:
        lines.append(_line("현재 복용 약과 새 약 유효성분을 비교했지만, 확인된 중복 성분은 없었어요."))

    if reason_text:
        reason_text = reason_text.replace("\r", "\n")
        existing_line_texts = {line[2:] for line in lines if line}
        for piece in [p.strip(" -•\t") for p in reason_text.split("\n") if p.strip()]:
            if piece and piece not in existing_line_texts:
                lines.append(_line(piece))
                existing_line_texts.add(piece)

    actions = _make_action_items(risk_level, overlap, current_ing, new_ing)
    explanation = "💡 " + lead
    if lines:
        explanation += "\n\n" + "\n".join(line for line in lines if line)

    ingredient_explanations = get_local_ingredient_explanations(new_ing or overlap)

    return {
        "mode": "template",
        "friendly_summary": lead,
        "explanation_lines": [line for line in lines if line],
        "action_items": actions,
        "ingredient_explanations": ingredient_explanations,
        "explanation": explanation,
    }


def build_patient_guidance(subject: str, risk_level: str, reason_text: str = "", new_active_ingredients=None, current_active_ingredients=None, overlap_active_ingredients=None, compare_basis=None, selected_current_labels=None):
    from services.llm_service import generate_llm_guidance, merge_ingredient_explanations

    fallback = _fallback_guidance(
        subject=subject,
        risk_level=risk_level,
        reason_text=reason_text,
        new_ing=new_active_ingredients or [],
        current_ing=current_active_ingredients or [],
        overlap=overlap_active_ingredients or [],
        compare_basis=compare_basis or [],
        selected_current_labels=selected_current_labels or [],
    )

    llm_result = generate_llm_guidance(
        {
            "subject": subject,
            "risk": risk_level,
            "reason_text": reason_text,
            "new_active_ingredients": _dedupe_keep_order(new_active_ingredients or []),
            "current_active_ingredients": _dedupe_keep_order(current_active_ingredients or []),
            "overlap_active_ingredients": _dedupe_keep_order(overlap_active_ingredients or []),
            "compare_basis": _dedupe_keep_order(compare_basis or []),
            "selected_current_labels": _dedupe_keep_order(selected_current_labels or []),
        }
    )
    if not llm_result:
        return fallback

    merged_ingredient_explanations = merge_ingredient_explanations(
        llm_result.get("ingredient_explanations", []),
        fallback.get("ingredient_explanations", []),
    )

    friendly_summary = str(llm_result.get("friendly_summary") or fallback.get("friendly_summary") or "").strip()
    explanation_lines = llm_result.get("explanation_lines") or fallback.get("explanation_lines") or []
    action_items = llm_result.get("action_items") or fallback.get("action_items") or []

    explanation = "💡 " + friendly_summary if friendly_summary else fallback.get("explanation", "")
    if explanation_lines:
        explanation += "\n\n" + "\n".join(
            line if str(line).strip().startswith("-") else _line(str(line))
            for line in explanation_lines
            if str(line or "").strip()
        )

    return {
        "mode": llm_result.get("mode", "llm"),
        "friendly_summary": friendly_summary,
        "explanation_lines": explanation_lines,
        "action_items": action_items,
        "ingredient_explanations": merged_ingredient_explanations,
        "explanation": explanation,
    }
