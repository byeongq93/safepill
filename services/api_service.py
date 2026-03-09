import os
from typing import Dict, List
from urllib.parse import unquote

import requests

# 기존 프로젝트에 있던 키를 기본값으로 두고,
# 환경변수 SAFEPILL_DUR_API_KEY가 있으면 그 값을 우선 사용합니다.
API_KEY = os.getenv(
    "SAFEPILL_DUR_API_KEY",
    "c19810ce2ac3ca44903a0b27c6773c819ca6da948e6d12195b77101538bec3f4",
)

API_URL = (
    "http://apis.data.go.kr/1471000/"
    "DURPrdlstInfoService03/getUsjntTabooInfoList03"
)


def _normalize_items(body: Dict) -> List[Dict]:
    items = body.get("items", [])
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def check_dur_api(drug_name: str) -> Dict:
    """
    main.py 에서 호출하는 식약처 DUR 조회 함수.

    반환 형식 예시:
    {
        "status": "safe" | "danger" | "error",
        "message": "...",
        "warnings": [
            {"mix_drug": "...", "warning_text": "..."}
        ]
    }
    """
    drug_name = (drug_name or "").strip()
    if not drug_name:
        return {
            "status": "error",
            "message": "drug_name is empty",
            "warnings": [],
        }

    if not API_KEY:
        return {
            "status": "error",
            "message": "API key is empty",
            "warnings": [],
        }

    params = {
        "serviceKey": unquote(API_KEY),
        "pageNo": "1",
        "numOfRows": "20",
        "type": "json",
        "itemName": drug_name,
    }

    try:
        response = requests.get(API_URL, params=params, timeout=6)
        response.raise_for_status()
        data = response.json()

        body = data.get("body", {}) if isinstance(data, dict) else {}
        total_count = body.get("totalCount", 0)
        try:
            total_count = int(total_count)
        except Exception:
            total_count = 0

        items = _normalize_items(body)
        warnings: List[Dict] = []

        for item in items:
            warnings.append(
                {
                    "mix_drug": item.get("MIXTURE_INGR_KOR_NAME", "") or "알 수 없음",
                    "warning_text": item.get("PROHBT_CONTENT", "") or "병용금기 주의 정보가 있습니다.",
                }
            )

        if total_count > 0 or warnings:
            return {
                "status": "danger",
                "message": f"총 {max(total_count, len(warnings))}건의 병용금기 후보가 확인되었습니다.",
                "warnings": warnings,
            }

        return {
            "status": "safe",
            "message": "국가 DUR 기준 병용금기 사항이 확인되지 않았습니다.",
            "warnings": [],
        }

    except requests.exceptions.RequestException as e:
        return {
            "status": "error",
            "message": f"국가 DUR API 통신 실패: {e}",
            "warnings": [],
        }
    except ValueError as e:
        return {
            "status": "error",
            "message": f"응답 JSON 파싱 실패: {e}",
            "warnings": [],
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"서버 오류: {e}",
            "warnings": [],
        }
