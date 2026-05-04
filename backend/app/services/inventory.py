from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app import models
from app.utils import safe_key


OPERATION_ALIASES = {
    "获得": "获得物品",
    "取得": "获得物品",
    "拾取": "获得物品",
    "添加": "获得物品",
    "新增": "获得物品",
    "add": "获得物品",
    "消耗": "消耗物品",
    "消费": "消耗物品",
    "consume": "消耗物品",
    "丢弃": "丢弃物品",
    "移除": "丢弃物品",
    "失去": "丢弃物品",
    "remove": "丢弃物品",
    "使用": "使用物品",
    "use": "使用物品",
}
ALLOWED_OPERATIONS = {"获得物品", "消耗物品", "丢弃物品", "使用物品"}


def sync_character_inventory_to_session(db: Session, session: models.GameSession, character: models.Character) -> list[models.InventoryItem]:
    synced: list[models.InventoryItem] = []
    for entry in character.inventory or []:
        payload = normalize_character_inventory_entry(entry)
        if not payload:
            continue
        item = upsert_inventory_item(
            db,
            session.id,
            payload["item_key"],
            payload["name"],
            payload.get("description", ""),
            int(payload.get("quantity") or 1),
            {**payload.get("metadata", {}), "来源": "角色初始物品"},
        )
        synced.append(item)
    return synced


def normalize_character_inventory_entry(entry: Any) -> dict[str, Any] | None:
    if isinstance(entry, str):
        name = entry.strip()
        if not name:
            return None
        return {"item_key": safe_item_key(name), "name": name, "description": "", "quantity": 1, "metadata": {}}
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or entry.get("名称") or entry.get("item") or entry.get("物品") or "").strip()
    if not name:
        return None
    quantity = clamp_int(to_int(entry.get("quantity") or entry.get("数量"), 1), 1, 99)
    description = str(entry.get("description") or entry.get("描述") or "").strip()[:500]
    key = safe_item_key(str(entry.get("item_key") or entry.get("key") or entry.get("物品键") or name))
    consumable = bool(entry.get("consumable") or entry.get("可消耗"))
    metadata = {"可消耗": consumable} if consumable else {}
    return {"item_key": key, "name": name[:120], "description": description, "quantity": quantity, "metadata": metadata}


def normalize_inventory_changes(value: Any, issues: list[str] | None = None) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    raw_items = value if isinstance(value, list) else [value]
    changes: list[dict[str, Any]] = []
    for item in raw_items[:10]:
        if not isinstance(item, dict):
            append_issue(issues, "忽略了格式不正确的物品变化。")
            continue
        operation = normalize_operation(item.get("operation") or item.get("操作") or item.get("type") or item.get("action"))
        if operation not in ALLOWED_OPERATIONS:
            append_issue(issues, "忽略了不支持的物品操作。")
            continue
        name = str(item.get("name") or item.get("名称") or item.get("item") or item.get("物品") or "").strip()
        item_key_source = str(item.get("item_key") or item.get("key") or item.get("物品键") or name).strip()
        if not name and not item_key_source:
            append_issue(issues, "忽略了缺少物品名的物品变化。")
            continue
        if not name:
            name = item_key_source
        item_key = safe_item_key(item_key_source or name)
        has_quantity = item.get("quantity") is not None or item.get("数量") is not None
        default_quantity = 99 if operation == "丢弃物品" and not has_quantity else 1
        quantity = clamp_int(to_int(item.get("quantity") or item.get("数量"), default_quantity), 1, 99)
        description = str(item.get("description") or item.get("描述") or "").strip()[:500]
        reason = str(item.get("reason") or item.get("原因") or "").strip()[:300]
        consumable = bool(item.get("consumable") or item.get("可消耗"))
        changes.append(
            {
                "operation": operation,
                "item_key": item_key,
                "name": name[:120],
                "quantity": quantity,
                "description": description,
                "consumable": consumable,
                "reason": reason,
            }
        )
    return changes


def apply_inventory_changes(db: Session, session: models.GameSession, changes: list[dict[str, Any]], turn_index: int) -> dict[str, Any]:
    applied: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    for change in changes:
        operation = str(change.get("operation") or "")
        item_key = safe_item_key(str(change.get("item_key") or change.get("name") or "物品"))
        name = str(change.get("name") or item_key)[:120]
        quantity = clamp_int(to_int(change.get("quantity"), 1), 1, 99)
        existing = find_inventory_item(db, session.id, item_key)
        if operation == "获得物品":
            item = upsert_inventory_item(
                db,
                session.id,
                item_key,
                name,
                str(change.get("description") or "")[:500],
                quantity,
                build_metadata(change, turn_index),
            )
            applied.append(result_payload(operation, item, quantity, change))
            continue
        if existing is None:
            ignored.append({"operation": operation, "item_key": item_key, "name": name, "reason": "物品不存在"})
            continue
        if operation == "使用物品" and not is_consumable_change(change, existing):
            existing.metadata_ = merge_metadata(existing.metadata_, build_metadata(change, turn_index))
            applied.append(result_payload(operation, existing, 0, change))
            continue
        if operation in {"消耗物品", "使用物品"}:
            applied.append(decrease_or_remove_item(db, existing, operation, quantity, change, turn_index))
            continue
        if operation == "丢弃物品":
            applied.append(decrease_or_remove_item(db, existing, operation, quantity, change, turn_index))
            continue
        ignored.append({"operation": operation, "item_key": item_key, "name": name, "reason": "未应用"})
    return {"applied": applied, "ignored": ignored, "summary": summarize_inventory_results(applied, ignored)}


def upsert_inventory_item(db: Session, session_id: str, item_key: str, name: str, description: str, quantity: int, metadata: dict[str, Any]) -> models.InventoryItem:
    item = find_inventory_item(db, session_id, item_key)
    if item is None:
        item = models.InventoryItem(session_id=session_id, item_key=item_key, name=name[:120], description=description[:500], quantity=quantity, metadata_=metadata)
        db.add(item)
        return item
    item.name = name[:120]
    if description:
        item.description = description[:500]
    item.quantity = clamp_int(int(item.quantity or 0) + quantity, 1, 999)
    item.metadata_ = merge_metadata(item.metadata_, metadata)
    return item


def find_inventory_item(db: Session, session_id: str, item_key: str) -> models.InventoryItem | None:
    return db.query(models.InventoryItem).filter(models.InventoryItem.session_id == session_id, models.InventoryItem.item_key == item_key).one_or_none()


def decrease_or_remove_item(db: Session, item: models.InventoryItem, operation: str, quantity: int, change: dict[str, Any], turn_index: int) -> dict[str, Any]:
    consumed = min(quantity, int(item.quantity or 0))
    remaining = int(item.quantity or 0) - consumed
    payload = result_payload(operation, item, consumed, change)
    payload["remaining_quantity"] = max(remaining, 0)
    if remaining <= 0:
        db.delete(item)
    else:
        item.quantity = remaining
        item.metadata_ = merge_metadata(item.metadata_, build_metadata(change, turn_index))
    return payload


def result_payload(operation: str, item: models.InventoryItem, quantity: int, change: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": operation,
        "item_key": item.item_key,
        "name": item.name,
        "quantity": quantity,
        "reason": str(change.get("reason") or "")[:300],
    }


def build_metadata(change: dict[str, Any], turn_index: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {"最近回合": turn_index}
    if change.get("reason"):
        metadata["最近原因"] = str(change.get("reason"))[:300]
    if change.get("consumable"):
        metadata["可消耗"] = True
    return metadata


def merge_metadata(current: dict[str, Any] | None, update: dict[str, Any]) -> dict[str, Any]:
    base = current.copy() if isinstance(current, dict) else {}
    base.update(update)
    return base


def is_consumable_change(change: dict[str, Any], item: models.InventoryItem) -> bool:
    metadata = item.metadata_ if isinstance(item.metadata_, dict) else {}
    return bool(change.get("consumable") or metadata.get("可消耗"))


def normalize_operation(value: Any) -> str:
    raw = str(value or "").strip()
    if raw in ALLOWED_OPERATIONS:
        return raw
    return OPERATION_ALIASES.get(raw, raw)


def safe_item_key(value: str) -> str:
    key = safe_key(value)[:160]
    return key or "item"


def summarize_inventory_results(applied: list[dict[str, Any]], ignored: list[dict[str, Any]]) -> list[str]:
    summary: list[str] = []
    for item in applied:
        operation = str(item.get("operation") or "物品变化")
        name = str(item.get("name") or item.get("item_key") or "物品")
        quantity = int(item.get("quantity") or 0)
        if operation == "使用物品" and quantity <= 0:
            summary.append(f"使用：{name}")
        else:
            summary.append(f"{operation.replace('物品', '')}：{name} ×{max(quantity, 1)}")
    if ignored:
        summary.append(f"忽略无效物品变化 {len(ignored)} 条")
    return summary


def append_issue(issues: list[str] | None, message: str) -> None:
    if issues is not None:
        issues.append(message)


def to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
