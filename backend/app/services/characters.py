from pathlib import Path
from copy import deepcopy
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app import models


ATTRIBUTE_ORDER = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU", "Luck"]
ATTRIBUTE_NAMES = {
    "STR": "力量",
    "CON": "体质",
    "SIZ": "体型",
    "DEX": "敏捷",
    "APP": "外貌",
    "INT": "智力",
    "POW": "意志",
    "EDU": "教育",
    "Luck": "幸运",
}
ATTRIBUTE_ALIASES = {
    "STR": ["STR", "力量"],
    "CON": ["CON", "体质"],
    "SIZ": ["SIZ", "体型"],
    "DEX": ["DEX", "敏捷"],
    "APP": ["APP", "外貌"],
    "INT": ["INT", "智力", "灵感"],
    "POW": ["POW", "意志"],
    "EDU": ["EDU", "教育", "知识"],
    "Luck": ["LUCK", "幸运"],
}
SKILL_ALIASES = {"射击": "射击（手枪）", "驾驶": "驾驶（船）"}


DEFAULT_INVESTIGATOR = {
    "name": "调查局探员",
    "archetype": "调查局探员",
    "occupation": "调查局探员",
    "hp_current": 12,
    "hp_max": 12,
    "san_current": 65,
    "san_max": 99,
    "mp_current": 13,
    "mp_max": 13,
    "luck": 50,
    "attributes": {
        "STR": 50,
        "CON": 60,
        "SIZ": 60,
        "DEX": 60,
        "APP": 50,
        "INT": 70,
        "POW": 65,
        "EDU": 70,
    },
    "skills": {
        "侦查": 55,
        "聆听": 50,
        "图书馆使用": 45,
        "心理学": 50,
        "射击（手枪）": 60,
        "斗殴": 45,
        "闪避": 30,
        "急救": 40,
        "法律": 50,
        "潜行": 40,
        "驾驶（船）": 50,
        "估价": 50,
        "电气维修": 20,
        "机械维修": 30,
        "博物学": 10,
        "神秘学": 5,
    },
    "inventory": ["调查局徽章", "记事本", "钢笔"],
    "background": {
        "trait": "有些神经紧张和警惕。这个任务的某方面让他不安。",
        "motivation": "从卧底同事那里拿到报告，好让上级把自己重新分配去不那么可怕的地方。",
    },
}


def import_characters(db: Session, scenario: models.Scenario, character_dir: Path) -> int:
    changed = 0
    known = {character.archetype: character for character in db.query(models.Character).filter(models.Character.scenario_id == scenario.id).all()}
    if character_dir.exists():
        for path in character_dir.glob("*.xlsx"):
            if path.name.startswith("~$"):
                continue
            archetype = path.stem
            payload = read_character_excel(path)
            payload.setdefault("name", archetype)
            payload.setdefault("archetype", archetype)
            payload.setdefault("occupation", archetype)
            merged = merge_character_defaults(payload)
            if archetype in known:
                apply_character_payload(known[archetype], merged)
            else:
                character = models.Character(scenario_id=scenario.id, **merged)
                db.add(character)
                known[archetype] = character
            changed += 1
    if "调查局探员" not in known:
        db.add(models.Character(scenario_id=scenario.id, **merge_character_defaults(DEFAULT_INVESTIGATOR)))
        changed += 1
    db.commit()
    return changed


def ensure_character_attributes(db: Session, scenario: models.Scenario, character_dir: Path) -> int:
    characters = db.query(models.Character).filter(models.Character.scenario_id == scenario.id).all()
    if not characters or any(needs_attribute_backfill(character) for character in characters):
        return import_characters(db, scenario, character_dir)
    return 0


def needs_attribute_backfill(character: models.Character) -> bool:
    attributes = character.attributes if isinstance(character.attributes, dict) else {}
    core = attributes.get("核心属性", {}) if isinstance(attributes.get("核心属性"), dict) else {}
    required = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU"]
    return any(not attributes.get(key) or not core.get(key) for key in required)


def read_character_excel(path: Path) -> dict[str, Any]:
    try:
        raw = pd.read_excel(path, sheet_name="人物卡", header=None).fillna("")
    except Exception:
        return {"archetype": path.stem}
    text_cells = [str(cell).strip() for row in raw.values.tolist() for cell in row if str(cell).strip()]
    text = "\n".join(text_cells)
    age = find_number_after_keyword(text_cells, "年龄")
    payload: dict[str, Any] = {
        "name": path.stem,
        "archetype": path.stem,
        "occupation": path.stem,
        "background": {"source_file": str(path), "raw_excerpt": text[:1500], "age": age},
    }
    attributes = extract_character_attributes(raw, age)
    if attributes:
        payload["attributes"] = attributes
        apply_derived_fields(payload, attributes)
    extracted_skills: dict[str, int] = {}
    for skill in ["侦查", "聆听", "图书馆使用", "心理学", "射击", "斗殴", "闪避", "急救", "驾驶", "估价", "机械维修", "电气维修", "博物学", "神秘学"]:
        value = find_number_after_keyword(text_cells, skill)
        if value is not None:
            extracted_skills[SKILL_ALIASES.get(skill, skill)] = value
    if extracted_skills:
        payload["skills"] = extracted_skills
    return payload


def find_number_after_keyword(cells: list[str], keyword: str) -> int | None:
    for index, cell in enumerate(cells):
        if keyword not in cell:
            continue
        candidates = [cell]
        candidates.extend(cells[index + 1 : index + 4])
        for candidate in candidates:
            digits = "".join(ch if ch.isdigit() else " " for ch in candidate).split()
            for number in digits:
                value = int(number)
                if 1 <= value <= 99:
                    return value
    return None


def merge_character_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    base = deepcopy(DEFAULT_INVESTIGATOR) if payload.get("archetype") == "调查局探员" else {
        "hp_current": 10,
        "hp_max": 10,
        "san_current": 60,
        "san_max": 99,
        "mp_current": 10,
        "mp_max": 10,
        "luck": 50,
        "attributes": {},
        "skills": {},
        "inventory": [],
        "background": {},
    }
    base.update(payload)
    if base.get("attributes"):
        base["attributes"] = normalize_attribute_payload(base["attributes"], int(base.get("luck") or 50))
        apply_derived_fields(base, base["attributes"])
    if "skills" in payload and payload["skills"]:
        merged_skills = DEFAULT_INVESTIGATOR["skills"].copy() if payload.get("archetype") == "调查局探员" else {}
        merged_skills.update(payload["skills"])
        base["skills"] = merged_skills
    return base


def apply_character_payload(character: models.Character, payload: dict[str, Any]) -> None:
    old_hp_max = character.hp_max
    old_mp_max = character.mp_max
    old_derived = character.attributes.get("派生属性", {}) if isinstance(character.attributes, dict) else {}
    old_san_start = int(old_derived.get("SAN") or character.attributes.get("SAN", character.san_current) or character.san_current) if isinstance(character.attributes, dict) else character.san_current
    for key in ["name", "archetype", "occupation", "luck", "attributes", "skills", "inventory", "background"]:
        if key in payload:
            setattr(character, key, payload[key])
    if character.hp_current == old_hp_max or character.hp_current <= 0:
        character.hp_current = int(payload.get("hp_current", payload.get("hp_max", character.hp_current)))
    character.hp_max = int(payload.get("hp_max", character.hp_max))
    if character.mp_current == old_mp_max or character.mp_current <= 0:
        character.mp_current = int(payload.get("mp_current", payload.get("mp_max", character.mp_current)))
    character.mp_max = int(payload.get("mp_max", character.mp_max))
    if character.san_current == old_san_start or character.san_current <= 0:
        character.san_current = int(payload.get("san_current", character.san_current))
    character.san_max = int(payload.get("san_max", character.san_max))


def extract_character_attributes(raw: pd.DataFrame, age: int | None) -> dict[str, Any]:
    core: dict[str, dict[str, int | str]] = {}
    for row_index in range(raw.shape[0]):
        for column_index in range(raw.shape[1]):
            attribute = match_attribute_label(raw.iat[row_index, column_index])
            if not attribute:
                continue
            value = number_at(raw, row_index, column_index + 2)
            if value is None or value <= 0:
                continue
            half = number_at(raw, row_index, column_index + 4) or value // 2
            fifth = number_at(raw, row_index + 1, column_index + 2) or value // 5
            core[attribute] = {"名称": ATTRIBUTE_NAMES[attribute], "简单鉴定": value, "中等鉴定": half, "困难鉴定": fifth}
    if not core:
        return {}
    attributes: dict[str, Any] = {key: int(core[key]["简单鉴定"]) for key in ATTRIBUTE_ORDER if key in core}
    attributes["核心属性"] = {key: core[key] for key in ATTRIBUTE_ORDER if key in core}
    if age is not None:
        attributes["年龄"] = age
    attributes["派生属性"] = derive_secondary_attributes(attributes, age)
    return attributes


def match_attribute_label(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return ""
    compact = text.upper().replace(" ", "")
    stripped = text.replace(" ", "")
    for attribute, aliases in ATTRIBUTE_ALIASES.items():
        for alias in aliases:
            normalized_alias = alias.upper() if alias.isascii() else alias
            if alias.isascii() and normalized_alias in compact:
                return attribute
            if not alias.isascii() and (stripped.startswith(alias) or f"\n{alias}" in stripped):
                return attribute
    return ""


def number_at(raw: pd.DataFrame, row_index: int, column_index: int) -> int | None:
    if row_index < 0 or column_index < 0 or row_index >= raw.shape[0] or column_index >= raw.shape[1]:
        return None
    return parse_number(raw.iat[row_index, column_index])


def parse_number(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        digits = "".join(ch if ch.isdigit() else " " for ch in text).split()
        return int(digits[0]) if digits else None


def normalize_attribute_payload(attributes: dict[str, Any], fallback_luck: int) -> dict[str, Any]:
    normalized = dict(attributes)
    core: dict[str, dict[str, int | str]] = {}
    for attribute in ATTRIBUTE_ORDER:
        value = parse_number(normalized.get(attribute))
        if attribute == "Luck" and (value is None or value <= 0):
            value = fallback_luck
        if value is None or value <= 0:
            continue
        normalized[attribute] = value
        core[attribute] = {"名称": ATTRIBUTE_NAMES[attribute], "简单鉴定": value, "中等鉴定": value // 2, "困难鉴定": value // 5}
    normalized["核心属性"] = {**core, **{key: value for key, value in normalized.get("核心属性", {}).items() if key in ATTRIBUTE_ORDER}}
    normalized["派生属性"] = derive_secondary_attributes(normalized, parse_number(normalized.get("年龄")))
    return normalized


def apply_derived_fields(payload: dict[str, Any], attributes: dict[str, Any]) -> None:
    derived = attributes.get("派生属性", {}) if isinstance(attributes, dict) else {}
    hp = int(derived.get("HP") or payload.get("hp_max") or 10)
    mp = int(derived.get("MP") or payload.get("mp_max") or 10)
    san = int(derived.get("SAN") or payload.get("san_current") or 60)
    luck = int(attributes.get("Luck") or payload.get("luck") or 50)
    payload["hp_max"] = hp
    payload.setdefault("hp_current", hp)
    payload["mp_max"] = mp
    payload.setdefault("mp_current", mp)
    payload["san_current"] = san
    payload.setdefault("san_max", 99)
    payload["luck"] = luck


def derive_secondary_attributes(attributes: dict[str, Any], age: int | None) -> dict[str, Any]:
    strength = int(attributes.get("STR") or 50)
    constitution = int(attributes.get("CON") or 50)
    size = int(attributes.get("SIZ") or 50)
    dexterity = int(attributes.get("DEX") or 50)
    power = int(attributes.get("POW") or 50)
    damage_bonus, build = derive_damage_bonus_and_build(strength + size)
    return {
        "HP": (constitution + size) // 10,
        "MP": power // 5,
        "SAN": power,
        "伤害加值": damage_bonus,
        "体格": build,
        "MOV": derive_move_rate(strength, dexterity, size, age),
    }


def derive_damage_bonus_and_build(total: int) -> tuple[str, int]:
    if total <= 64:
        return "-2", -2
    if total <= 84:
        return "-1", -1
    if total <= 124:
        return "0", 0
    if total <= 164:
        return "+1d4", 1
    if total <= 204:
        return "+1d6", 2
    extra = max(2, ((total - 205) // 80) + 2)
    return f"+{extra}d6", extra + 1


def derive_move_rate(strength: int, dexterity: int, size: int, age: int | None) -> int:
    if strength < size and dexterity < size:
        move = 7
    elif strength > size and dexterity > size:
        move = 9
    else:
        move = 8
    if age is None:
        return move
    if 40 <= age <= 49:
        move -= 1
    elif 50 <= age <= 59:
        move -= 2
    elif 60 <= age <= 69:
        move -= 3
    elif 70 <= age <= 79:
        move -= 4
    elif 80 <= age <= 89:
        move -= 5
    return max(1, move)
