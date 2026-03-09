import os
import sqlite3
import re
from difflib import SequenceMatcher
from typing import Dict, List, Tuple
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
_ENV_DB = os.getenv("SAFEPILL_DB_PATH")
if _ENV_DB:
    DB_PATH = str((BASE_DIR / _ENV_DB).resolve()) if not os.path.isabs(_ENV_DB) else _ENV_DB
else:
    DB_PATH = str((BASE_DIR / "safepill.db").resolve()) if (BASE_DIR / "safepill.db").exists() else str((BASE_DIR / "pharmguard.db").resolve())

_norm_re_units = re.compile(r"(mg|g|ml|mcg|㎎|㎖|정|캡슐|환|포|병)\b", re.IGNORECASE)
_norm_re_brackets = re.compile(r"\(.*?\)|\[.*?\]|\{.*?\}")
_norm_re_nonword = re.compile(r"[^0-9a-zA-Z가-힣]+")


def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = str(s)
    s = _norm_re_brackets.sub(" ", s)
    s = _norm_re_units.sub(" ", s)
    s = _norm_re_nonword.sub("", s)
    return s.strip()


def _norm_key(s: str) -> str:
    return normalize_text(s).lower()


def extract_tokens(raw_text: str) -> List[str]:
    if not raw_text:
        return []
    tokens = re.findall(r"[0-9a-zA-Z가-힣]{2,}", raw_text)
    tokens = sorted(set(tokens), key=len, reverse=True)
    return tokens[:50]


_DRUG_CACHE = {
    "loaded": False,
    "norm_to_name": {},
    "firstchar_map": {},
    "table_missing": False,
}


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def _has_dur_table(conn: sqlite3.Connection) -> bool:
    return _has_table(conn, "dur_contraindications")


def _has_catalog_tables(conn: sqlite3.Connection) -> bool:
    return all(
        _has_table(conn, name)
        for name in ("drug_catalog", "drug_catalog_aliases", "drug_catalog_ingredients")
    )


def _load_drug_name_cache(force: bool = False) -> None:
    if _DRUG_CACHE["loaded"] and not force:
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        if not _has_dur_table(conn):
            _DRUG_CACHE.update(
                {
                    "loaded": True,
                    "norm_to_name": {},
                    "firstchar_map": {},
                    "table_missing": True,
                }
            )
            return

        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT drug_a FROM dur_contraindications
            UNION
            SELECT DISTINCT drug_b FROM dur_contraindications
            """
        )
        names = [r[0] for r in cur.fetchall() if r and r[0]]
    finally:
        conn.close()

    norm_to_name = {}
    firstchar_map = {}

    for name in names:
        n = normalize_text(name)
        if not n:
            continue
        if n not in norm_to_name or len(name) > len(norm_to_name[n]):
            norm_to_name[n] = name

        fc = n[0]
        firstchar_map.setdefault(fc, []).append(name)

    _DRUG_CACHE.update(
        {
            "loaded": True,
            "norm_to_name": norm_to_name,
            "firstchar_map": firstchar_map,
            "table_missing": False,
        }
    )


def resolve_drug_name(raw_text: str) -> Dict:
    _load_drug_name_cache()

    raw_norm = normalize_text(raw_text)
    if not raw_norm:
        return {
            "resolved_name": "",
            "suggested_name": "",
            "confidence": 0.0,
            "candidates": [],
            "note": "empty",
            "auto_applied": False,
            "needs_confirm": True,
        }

    if _DRUG_CACHE.get("table_missing"):
        return {
            "resolved_name": "",
            "suggested_name": "",
            "confidence": 0.0,
            "candidates": [],
            "note": "dur_table_missing",
            "auto_applied": False,
            "needs_confirm": True,
        }

    norm_to_name = _DRUG_CACHE["norm_to_name"]

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
    top3 = []
    for cand, _ in scored:
        if cand not in top3:
            top3.append(cand)
        if len(top3) >= 3:
            break

    score = round(best[1], 3)
    best_name = best[0] if best[0] else ""

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
            "resolved_name": "",
            "suggested_name": best_name,
            "confidence": score,
            "candidates": top3,
            "note": "fuzzy(needs_confirm 0.80~0.90)",
            "auto_applied": False,
            "needs_confirm": True,
        }

    return {
        "resolved_name": "",
        "suggested_name": best_name,
        "confidence": score,
        "candidates": top3,
        "note": "no_good_match",
        "auto_applied": False,
        "needs_confirm": True,
    }


def _ingredient_key(text: str) -> str:
    text = str(text or "").strip().lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^0-9a-zA-Z가-힣]+", "", text)
    return text


def _fetch_catalog_candidates(conn: sqlite3.Connection, alias_key: str) -> List[Dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.product_id, c.product_name, c.otc_type, c.manufacturer, c.efficacy
        FROM drug_catalog_aliases a
        JOIN drug_catalog c ON c.product_id = a.product_id
        WHERE a.alias_key = ?
        GROUP BY c.product_id, c.product_name, c.otc_type, c.manufacturer, c.efficacy
        """,
        (alias_key,),
    )
    rows = cur.fetchall()
    if not rows:
        return []

    product_ids = [row[0] for row in rows]
    placeholders = ",".join(["?"] * len(product_ids))
    cur.execute(
        f"""
        SELECT product_id, ingredient_name
        FROM drug_catalog_ingredients
        WHERE product_id IN ({placeholders})
        ORDER BY id
        """,
        product_ids,
    )
    ing_rows = cur.fetchall()
    ing_map: Dict[str, List[str]] = {}
    for product_id, ingredient_name in ing_rows:
        ing_map.setdefault(product_id, [])
        if ingredient_name not in ing_map[product_id]:
            ing_map[product_id].append(ingredient_name)

    result = []
    for product_id, product_name, otc_type, manufacturer, efficacy in rows:
        result.append(
            {
                "product_id": str(product_id),
                "product_name": product_name or "",
                "otc_type": otc_type or "",
                "manufacturer": manufacturer or "",
                "efficacy": efficacy or "",
                "ingredients": ing_map.get(str(product_id), []),
            }
        )
    return result


def lookup_catalog_by_name(raw_name: str, observed_ingredients: List[str] = None) -> Dict:
    query = str(raw_name or "").strip()
    alias_key = _norm_key(query)
    if not alias_key:
        return {"matched": False, "mode": "catalog_empty", "display_name": "", "ingredients": [], "candidates": []}

    conn = sqlite3.connect(DB_PATH)
    try:
        if not _has_catalog_tables(conn):
            return {"matched": False, "mode": "catalog_missing", "display_name": "", "ingredients": [], "candidates": []}
        candidates = _fetch_catalog_candidates(conn, alias_key)
    finally:
        conn.close()

    if not candidates:
        return {"matched": False, "mode": "catalog_none", "display_name": "", "ingredients": [], "candidates": []}

    observed_keys = {_ingredient_key(x) for x in (observed_ingredients or []) if _ingredient_key(x)}
    for cand in candidates:
        cand_keys = {_ingredient_key(x) for x in cand.get("ingredients", []) if _ingredient_key(x)}
        overlap = len(observed_keys & cand_keys)
        cand["_overlap"] = overlap
        cand["_exact_product"] = 1 if _norm_key(cand.get("product_name")) == alias_key else 0
        cand["_sig"] = tuple(sorted(cand_keys))

    candidates.sort(key=lambda x: (x["_overlap"], x["_exact_product"], len(x.get("ingredients", []))), reverse=True)
    top = candidates[0]
    top_overlap = top.get("_overlap", 0)
    top_name = top.get("product_name", "")
    names = [c.get("product_name", "") for c in candidates]

    if len(candidates) == 1:
        return {
            "matched": True,
            "mode": "catalog_unique",
            "display_name": top_name,
            "ingredients": top.get("ingredients", []),
            "candidates": names,
            "product_id": top.get("product_id", ""),
        }

    if top.get("_exact_product"):
        return {
            "matched": True,
            "mode": "catalog_exact_product",
            "display_name": top_name,
            "ingredients": top.get("ingredients", []),
            "candidates": names,
            "product_id": top.get("product_id", ""),
        }

    # observed ingredient overlap가 분명하면 해당 제품을 채택
    second_overlap = candidates[1].get("_overlap", -1)
    if top_overlap > 0 and top_overlap > second_overlap:
        return {
            "matched": True,
            "mode": "catalog_overlap_best",
            "display_name": top_name,
            "ingredients": top.get("ingredients", []),
            "candidates": names,
            "product_id": top.get("product_id", ""),
        }

    # 여럿이지만 성분 구성이 모두 같으면 제품명만 모호한 경우
    sigs = {cand.get("_sig") for cand in candidates if cand.get("_sig")}
    if len(sigs) == 1:
        return {
            "matched": True,
            "mode": "catalog_same_signature",
            "display_name": "",
            "ingredients": top.get("ingredients", []),
            "candidates": names,
            "product_id": top.get("product_id", ""),
        }

    # 최상위 overlap 후보들의 공통 성분만 보수적으로 사용
    if top_overlap > 0:
        top_group = [cand for cand in candidates if cand.get("_overlap", 0) == top_overlap]
        common = None
        for cand in top_group:
            keys = {_ingredient_key(x): x for x in cand.get("ingredients", []) if _ingredient_key(x)}
            if common is None:
                common = keys
            else:
                common = {k: common[k] for k in list(common.keys()) if k in keys}
        common_items = list(common.values()) if common else []
        if common_items:
            return {
                "matched": True,
                "mode": "catalog_common_intersection",
                "display_name": "",
                "ingredients": common_items,
                "candidates": names,
                "product_id": "",
            }

    return {
        "matched": False,
        "mode": "catalog_ambiguous",
        "display_name": "",
        "ingredients": [],
        "candidates": names,
        "product_id": "",
    }


def lookup_catalog_by_candidates(candidates: List[str], observed_ingredients: List[str] = None) -> Dict:
    ordered = []
    seen = set()
    for item in candidates or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)

    best = {"matched": False, "mode": "catalog_none", "display_name": "", "ingredients": [], "candidates": []}
    rank_map = {
        "catalog_exact_product": 5,
        "catalog_unique": 4,
        "catalog_overlap_best": 3,
        "catalog_same_signature": 2,
        "catalog_common_intersection": 1,
    }

    for cand in ordered[:12]:
        result = lookup_catalog_by_name(cand, observed_ingredients=observed_ingredients)
        score = rank_map.get(result.get("mode"), 0)
        best_score = rank_map.get(best.get("mode"), 0)
        if score > best_score:
            best = dict(result)
            best["used_seed"] = cand
    return best


def get_user_medicine_ingredients(nickname: str, medicine_names: List[str] = None) -> Dict[str, List[str]]:
    nickname = (nickname or "").strip()
    if not nickname:
        return {}

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_medicine_ingredients'"
        )
        if cur.fetchone() is None:
            return {}

        if medicine_names:
            cleaned = [str(x or "").strip() for x in medicine_names if str(x or "").strip()]
            if not cleaned:
                return {}
            placeholders = ",".join(["?"] * len(cleaned))
            sql = f"""
                SELECT medicine_name, ingredient_name
                FROM user_medicine_ingredients
                WHERE nickname = ? AND medicine_name IN ({placeholders})
                ORDER BY medicine_name, id
            """
            cur.execute(sql, [nickname, *cleaned])
        else:
            cur.execute(
                """
                SELECT medicine_name, ingredient_name
                FROM user_medicine_ingredients
                WHERE nickname = ?
                ORDER BY medicine_name, id
                """,
                (nickname,),
            )

        rows = cur.fetchall()
    finally:
        conn.close()

    result: Dict[str, List[str]] = {}
    for med, ingredient in rows:
        med = (med or "").strip()
        ingredient = (ingredient or "").strip()
        if not med or not ingredient:
            continue
        result.setdefault(med, [])
        if ingredient not in result[med]:
            result[med].append(ingredient)
    return result


def check_drug_interaction(new_drug: str, current_drugs: list = None) -> dict:
    if current_drugs is None or not current_drugs:
        return {"risk": "특이사항 없음", "reason": "현재 복용 중인 약이 없어 충돌 위험이 없습니다."}

    conn = sqlite3.connect(DB_PATH)
    try:
        if not _has_dur_table(conn):
            return {
                "risk": "주의",
                "reason": "병용금기 DB 테이블(dur_contraindications)을 찾지 못했습니다. 데이터를 먼저 적재해 주세요.",
            }

        cursor = conn.cursor()
        for existing_drug in current_drugs:
            cursor.execute(
                """
                SELECT risk_level, reason FROM dur_contraindications
                WHERE (drug_a = ? AND drug_b = ?)
                   OR (drug_a = ? AND drug_b = ?)
                """,
                (new_drug, existing_drug, existing_drug, new_drug),
            )
            result = cursor.fetchone()
            if result:
                return {"risk": result[0], "reason": f"[{existing_drug}] 약물과 상호작용: {result[1]}"}
    finally:
        conn.close()

    return {"risk": "특이사항 없음", "reason": "식약처 데이터베이스 상 충돌 기록이 발견되지 않았습니다."}
