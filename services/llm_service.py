import json
import os
from typing import Dict, List, Optional


INGREDIENT_GLOSSARY: Dict[str, Dict[str, str]] = {
    "아세트아미노펜": {
        "summary": "열을 내리고 통증을 줄이는 데 자주 쓰이는 해열진통 성분이에요.",
        "role": "해열·진통",
        "caution": "감기약·두통약·몸살약에 함께 들어있는 경우가 많아 중복 복용을 주의해야 해요.",
    },
    "이부프로펜": {
        "summary": "염증과 통증, 열을 줄이는 데 쓰이는 소염진통 성분이에요.",
        "role": "소염·진통·해열",
        "caution": "다른 진통소염제와 겹치지 않는지 확인하는 것이 좋아요.",
    },
    "덱시부프로펜": {
        "summary": "이부프로펜 계열의 진통·해열 성분으로 통증과 열을 줄이는 데 쓰여요.",
        "role": "소염·진통·해열",
        "caution": "같은 계열 진통제와 함께 복용하지 않도록 확인해 주세요.",
    },
    "구아이페네신": {
        "summary": "가래를 묽게 해서 배출을 돕는 거담 성분이에요.",
        "role": "가래 배출 도움",
        "caution": "기침감기약에 함께 포함되는 경우가 있어 성분표를 같이 보는 게 좋아요.",
    },
    "카페인무수물": {
        "summary": "졸림을 줄이거나 진통 성분의 체감 효과를 보조할 때 들어가는 성분이에요.",
        "role": "각성·보조",
        "caution": "카페인 음료를 많이 함께 섭취하면 불편할 수 있어요.",
    },
    "클로르페니라민말레산염": {
        "summary": "콧물, 재채기 같은 알레르기 증상을 줄이는 항히스타민 성분이에요.",
        "role": "콧물·재채기 완화",
        "caution": "졸릴 수 있어 운전 전에는 주의가 필요해요.",
    },
    "슈도에페드린염산염": {
        "summary": "코막힘을 덜 느끼게 도와주는 비충혈 완화 성분이에요.",
        "role": "코막힘 완화",
        "caution": "심장이 두근거리거나 잠이 잘 안 올 수 있어 늦은 시간 복용은 주의해요.",
    },
    "덱스트로메토르판브롬화수소산염수화물": {
        "summary": "기침을 줄이는 데 자주 쓰이는 진해 성분이에요.",
        "role": "기침 완화",
        "caution": "다른 기침약과 성분이 겹치는지 확인해 주세요.",
    },
    "덱스트로메토르판브롬화수소산염": {
        "summary": "기침을 줄이는 데 자주 쓰이는 진해 성분이에요.",
        "role": "기침 완화",
        "caution": "다른 기침약과 성분이 겹치는지 확인해 주세요.",
    },
    "디히드로코데인타르타르산염": {
        "summary": "기침을 줄이는 데 쓰이는 성분이에요.",
        "role": "기침 완화",
        "caution": "졸림이 올 수 있어 다른 감기약과 함께 먹을 때 성분 확인이 필요해요.",
    },
    "메틸에페드린염산염": {
        "summary": "기관지를 넓혀 숨쉬기 불편한 느낌을 덜어주는 데 쓰이는 성분이에요.",
        "role": "기관지 증상 완화",
        "caution": "감기약에 복합으로 들어가는 경우가 많아 겹침 여부를 보는 게 좋아요.",
    },
    "브롬헥신염산염": {
        "summary": "가래를 배출하기 쉽게 도와주는 거담 성분이에요.",
        "role": "가래 배출 도움",
        "caution": "비슷한 거담 성분이 함께 들어있지 않은지 확인해 주세요.",
    },
    "암브록솔염산염": {
        "summary": "가래를 묽게 해 배출을 돕는 데 쓰이는 성분이에요.",
        "role": "가래 배출 도움",
        "caution": "기침·가래약을 여러 개 함께 먹을 때 성분이 겹치지 않는지 확인해 주세요.",
    },
    "에르도스테인": {
        "summary": "끈적한 가래를 묽게 해 배출을 돕는 성분이에요.",
        "role": "가래 배출 도움",
        "caution": "다른 가래약과 함께 먹는 경우 중복 성분을 확인해 주세요.",
    },
    "레보드로프로피진": {
        "summary": "기침을 가라앉히는 데 쓰이는 진해 성분이에요.",
        "role": "기침 완화",
        "caution": "기침약을 여러 개 먹을 때 성분이 겹치지 않는지 살펴보는 게 좋아요.",
    },
    "세티리진염산염": {
        "summary": "알레르기 때문에 생기는 콧물·재채기·가려움 완화에 쓰이는 성분이에요.",
        "role": "알레르기 증상 완화",
        "caution": "사람에 따라 졸릴 수 있어요.",
    },
    "로라타딘": {
        "summary": "알레르기성 콧물이나 재채기 완화에 자주 쓰이는 성분이에요.",
        "role": "알레르기 증상 완화",
        "caution": "다른 알레르기약과 겹치지 않는지 확인해 주세요.",
    },
    "알마게이트": {
        "summary": "속쓰림이나 위산 과다 증상을 완화하는 제산 성분이에요.",
        "role": "속쓰림 완화",
        "caution": "다른 약과 복용 간격을 두는 것이 필요한 경우가 있어요.",
    },
    "수산화마그네슘": {
        "summary": "위산을 중화해 속쓰림을 줄이거나 변을 부드럽게 하는 데 쓰이는 성분이에요.",
        "role": "제산·완화",
        "caution": "다른 약과 간격을 두고 먹는 게 필요한 경우가 있어요.",
    },
}


DEFAULT_INGREDIENT_TEMPLATE = {
    "summary": "이 성분은 약의 핵심 작용을 담당하는 유효성분이에요. 자세한 역할은 제품 설명서나 전문가 안내로 확인하는 것이 가장 정확해요.",
    "role": "유효성분",
    "caution": "같은 증상약이라도 성분명이 겹치면 중복 복용이 될 수 있어요.",
}


def _env_enabled(name: str, default: str = "0") -> bool:
    value = str(os.getenv(name, default) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _clean_list(items: Optional[List[str]]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _extract_json_object(raw: str) -> Optional[Dict]:
    text = str(raw or "").strip()
    if not text:
        return None

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _normalize_ingredient_explanations(items: Optional[List[Dict]]) -> List[Dict]:
    normalized: List[Dict] = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("ingredient") or item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        summary = str(item.get("summary") or "").strip()
        role = str(item.get("role") or "").strip()
        caution = str(item.get("caution") or "").strip()
        source = str(item.get("source") or item.get("mode") or "llm").strip() or "llm"
        normalized.append(
            {
                "ingredient": name,
                "summary": summary,
                "role": role,
                "caution": caution,
                "source": source,
            }
        )
    return normalized


def get_local_ingredient_explanations(ingredients: Optional[List[str]]) -> List[Dict]:
    result: List[Dict] = []
    for name in _clean_list(ingredients):
        info = INGREDIENT_GLOSSARY.get(name, DEFAULT_INGREDIENT_TEMPLATE)
        result.append(
            {
                "ingredient": name,
                "summary": info.get("summary", ""),
                "role": info.get("role", ""),
                "caution": info.get("caution", ""),
                "source": "local_glossary" if name in INGREDIENT_GLOSSARY else "template",
            }
        )
    return result


def merge_ingredient_explanations(primary: Optional[List[Dict]], fallback: Optional[List[Dict]]) -> List[Dict]:
    primary_map = {item["ingredient"]: item for item in _normalize_ingredient_explanations(primary)}
    merged: List[Dict] = []
    seen = set()
    for item in _normalize_ingredient_explanations(primary) + _normalize_ingredient_explanations(fallback):
        name = item.get("ingredient", "")
        if not name or name in seen:
            continue
        seen.add(name)
        p = primary_map.get(name, {})
        merged.append(
            {
                "ingredient": name,
                "summary": str(p.get("summary") or item.get("summary") or "").strip(),
                "role": str(p.get("role") or item.get("role") or "").strip(),
                "caution": str(p.get("caution") or item.get("caution") or "").strip(),
                "source": str(p.get("source") or item.get("source") or "template").strip(),
            }
        )
    return merged


def generate_llm_guidance(payload: Dict) -> Optional[Dict]:
    """환경변수가 켜진 경우에만 LLM으로 복약 안내 문구를 생성한다.

    - SAFEPILL_ENABLE_LLM=1 일 때만 동작
    - OPENAI_API_KEY 또는 SAFEPILL_OPENAI_API_KEY 필요
    - 실패하면 None 반환 (서버는 템플릿 설명으로 자동 폴백)
    """
    if not _env_enabled("SAFEPILL_ENABLE_LLM"):
        return None

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("SAFEPILL_OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except Exception:
        return None

    subject = str(payload.get("subject") or "이 약").strip() or "이 약"
    risk = str(payload.get("risk") or "특이사항 없음").strip() or "특이사항 없음"
    reason = str(payload.get("reason_text") or "").strip()
    new_active_ingredients = _clean_list(payload.get("new_active_ingredients"))
    current_active_ingredients = _clean_list(payload.get("current_active_ingredients"))
    overlap_active_ingredients = _clean_list(payload.get("overlap_active_ingredients"))
    compare_basis = _clean_list(payload.get("compare_basis"))
    selected_current_labels = _clean_list(payload.get("selected_current_labels"))
    fallback_ingredient_explanations = get_local_ingredient_explanations(new_active_ingredients or overlap_active_ingredients)

    client = OpenAI(api_key=api_key)
    model = os.getenv("SAFEPILL_LLM_MODEL", "gpt-4o-mini")

    system_prompt = (
        "당신은 다정하지만 과장하지 않는 한국어 복약 도우미입니다. "
        "반드시 제공된 facts만 사용해 이미 계산된 risk와 reason을 쉬운 말로 풀어주세요. "
        "병용 판정 자체를 새로 만들지 말고, 금지/허용을 단정하지 마세요. "
        "의학 전문용어는 일상어로 번역하고, 출력은 반드시 JSON 하나만 반환하세요."
    )

    user_payload = {
        "subject": subject,
        "risk": risk,
        "reason_text": reason,
        "new_active_ingredients": new_active_ingredients,
        "current_active_ingredients": current_active_ingredients,
        "overlap_active_ingredients": overlap_active_ingredients,
        "compare_basis": compare_basis,
        "selected_current_labels": selected_current_labels,
        "fallback_ingredient_explanations": fallback_ingredient_explanations,
        "output_schema": {
            "friendly_summary": "1~2문장 요약",
            "action_items": ["사용자 행동 지침 2~4개"],
            "explanation_lines": ["쉬운 말 설명 2~4개"],
            "ingredient_explanations": [
                {
                    "ingredient": "성분명",
                    "summary": "이 성분이 보통 어떤 역할인지 한 줄 설명",
                    "role": "짧은 역할명",
                    "caution": "있으면 좋은 한 줄 주의사항",
                }
            ],
        },
        "hard_rules": [
            "risk/reason에 없는 새로운 병용금기 판단을 만들지 말 것",
            "불확실하면 fallback_ingredient_explanations 문장을 유지할 것",
            "중복 복용 주의, 전문가 상담 권고 톤을 유지할 것",
        ],
    }

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        )
        content = response.choices[0].message.content or ""
    except Exception:
        return None

    data = _extract_json_object(content)
    if not data:
        return None

    friendly_summary = str(data.get("friendly_summary") or "").strip()
    action_items = _clean_list(data.get("action_items") if isinstance(data.get("action_items"), list) else [])
    explanation_lines = _clean_list(data.get("explanation_lines") if isinstance(data.get("explanation_lines"), list) else [])
    ingredient_explanations = merge_ingredient_explanations(
        data.get("ingredient_explanations") if isinstance(data.get("ingredient_explanations"), list) else [],
        fallback_ingredient_explanations,
    )

    if not friendly_summary and not action_items and not explanation_lines and not ingredient_explanations:
        return None

    return {
        "mode": "llm",
        "friendly_summary": friendly_summary,
        "action_items": action_items[:4],
        "explanation_lines": explanation_lines[:4],
        "ingredient_explanations": ingredient_explanations[:8],
    }
