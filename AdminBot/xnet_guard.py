# AdminBot/xnet_guard.py
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from Shared import agent_db, database, userbot_db, xnet_api
from Shared.tg_button_styles import inline_button as InlineKeyboardButton

logger = logging.getLogger(__name__)


def _is_main_xnet(server_id: int) -> Tuple[bool, Optional[Dict[str, Any]]]:
    server = database.get_server_by_id(int(server_id or 0))
    if not server or not xnet_api.is_xnet_server(server):
        return False, server
    main_ids = {
        int((row or {}).get("id") or 0)
        for row in (database.get_main_servers() or [])
    }
    return int(server_id or 0) in main_ids, server


def build_guard_button(server_id: int) -> List[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            "🛡️ محافظ X-NET",
            callback_data=f"xnetguard:{int(server_id)}:menu",
        )
    ]


def _menu_keyboard(server_id: int, *, missing: int = 0) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "🔍 بررسی وضعیت",
                callback_data=f"xnetguard:{server_id}:status",
            )
        ],
        [
            InlineKeyboardButton(
                "💾 بروزرسانی Snapshot",
                callback_data=f"xnetguard:{server_id}:snapshot",
            )
        ],
    ]
    if missing > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    f"♻️ بازیابی کاربران حذف‌شده ({missing})",
                    callback_data=f"xnetguard:{server_id}:recover_ask",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data=f"server:{server_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _parse_dt(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _frozen_usage_for_uuid(server_id: int, user_uuid: str) -> float:
    """Best-effort: use the bot's frozen accounting as a stronger baseline."""
    sid = int(server_id or 0)
    uuid = str(user_uuid or "").strip().lower()
    best = 0.0

    try:
        svc = userbot_db.get_service_by_panel_uuid(user_uuid)
        if svc:
            for node in userbot_db.get_service_nodes(int(svc.get("id") or 0)) or []:
                if int(node.get("server_id") or 0) != sid:
                    continue
                if str(node.get("panel_user_uuid") or "").strip().lower() != uuid:
                    continue
                try:
                    best = max(best, float(node.get("usage_current") or 0.0))
                except Exception:
                    pass
            if int(svc.get("server_id") or 0) == sid:
                try:
                    best = max(best, float(svc.get("usage_current") or 0.0))
                except Exception:
                    pass
    except Exception:
        pass

    try:
        svc = agent_db.get_service_by_uuid(user_uuid)
        if svc:
            for node in agent_db.get_service_nodes(int(svc.get("id") or 0)) or []:
                if int(node.get("server_id") or 0) != sid:
                    continue
                if str(node.get("panel_user_uuid") or "").strip().lower() != uuid:
                    continue
                try:
                    best = max(best, float(node.get("usage_current") or 0.0))
                except Exception:
                    pass
            if int(svc.get("server_id") or 0) == sid:
                try:
                    best = max(best, float(svc.get("usage_current") or 0.0))
                except Exception:
                    pass
    except Exception:
        pass

    return max(best, 0.0)


async def _guard_state(server: Dict[str, Any]) -> Dict[str, Any]:
    inbounds = await xnet_api.get_inbounds(server)
    users = await xnet_api.list_users(server)
    server_id = int(server.get("id") or 0)
    snapshots = userbot_db.get_xnet_guard_snapshots(server_id)

    current = {
        str(u.get("uuid") or u.get("id") or "").strip().lower()
        for u in users
        if str(u.get("uuid") or u.get("id") or "").strip()
    }
    missing = [
        s for s in snapshots
        if str(s.get("user_uuid") or "").strip().lower() not in current
    ]

    enabled_inbounds = sum(1 for i in inbounds if bool(i.get("enabled", True)))
    return {
        "inbounds": inbounds,
        "enabled_inbounds": enabled_inbounds,
        "users": users,
        "snapshots": snapshots,
        "missing": missing,
    }


def _state_text(server: Dict[str, Any], state: Dict[str, Any]) -> str:
    title = str(server.get("title") or f"X-NET #{server.get('id')}").strip()
    missing = len(state.get("missing") or [])
    status = "🟢 سالم" if missing == 0 else "🟠 نیاز به بررسی"
    return (
        f"🛡️ محافظ X-NET — {title}\n"
        f"❖ • -------------------------- • ❖\n"
        f"وضعیت: {status}\n"
        f"🧩 Inboundها: {len(state.get('inbounds') or [])} "
        f"(فعال: {state.get('enabled_inbounds') or 0})\n"
        f"👥 کاربران فعلی پنل: {len(state.get('users') or [])}\n"
        f"💾 Snapshotهای محفوظ: {len(state.get('snapshots') or [])}\n"
        f"⚠️ کاربران موجود در Snapshot ولی حذف‌شده از X-NET: {missing}\n\n"
        "این محافظ فقط روی X-NETی که «سرور اصلی» ربات است فعال می‌شود؛ "
        "برای X-NETهای نود نمایش داده نمی‌شود.\n"
        "Snapshot قدیمی هنگام حذف ناگهانی کاربر پاک نمی‌شود، بنابراین امکان "
        "بازیابی همان UUID و حجم باقی‌مانده وجود دارد."
    )


async def _snapshot(server: Dict[str, Any]) -> Tuple[int, int]:
    users = await xnet_api.list_users(server)
    saved = userbot_db.upsert_xnet_guard_snapshot_users(
        int(server.get("id") or 0),
        users,
    )
    return saved, len(users)


async def _recover_missing(server: Dict[str, Any]) -> Dict[str, Any]:
    state = await _guard_state(server)
    missing = state.get("missing") or []
    if not missing:
        return {
            "restored": 0,
            "failed": 0,
            "skipped": 0,
            "preserved_gb": 0.0,
            "errors": [],
        }

    inbounds = state.get("inbounds") or []
    target_ids = xnet_api._selected_inbound_ids(server, inbounds)
    if not target_ids:
        raise xnet_api.XnetApiError(
            "هیچ Inbound فعالی برای بازیابی کاربران وجود ندارد."
        )

    restored = 0
    failed = 0
    skipped = 0
    preserved_gb = 0.0
    errors: List[str] = []
    now = datetime.now(timezone.utc)
    server_id = int(server.get("id") or 0)

    for snap in missing:
        uuid = str(snap.get("user_uuid") or "").strip()
        if not uuid:
            skipped += 1
            continue
        payload = snap.get("snapshot") if isinstance(snap.get("snapshot"), dict) else {}
        username = str(
            payload.get("name")
            or payload.get("username")
            or snap.get("username")
            or uuid
        ).strip()

        try:
            original_limit = max(float(snap.get("usage_limit_gb") or 0.0), 0.0)
        except Exception:
            original_limit = 0.0
        try:
            snapshot_used = max(float(snap.get("usage_current_gb") or 0.0), 0.0)
        except Exception:
            snapshot_used = 0.0

        # Existing frozen accounting wins over the snapshot when it has a
        # larger value. This prevents a recovery from giving consumed traffic
        # back to the customer.
        used = max(snapshot_used, _frozen_usage_for_uuid(server_id, uuid))
        finite = original_limit > 0
        remaining = max(original_limit - used, 0.0) if finite else 0.0

        expire_date = str(snap.get("expire_date") or payload.get("expireDate") or "").strip()
        exp_dt = _parse_dt(expire_date)
        active = bool(int(snap.get("is_active") or 0))
        if exp_dt is not None and exp_dt <= now:
            active = False
        if finite and remaining <= 0:
            active = False

        # 0 means unlimited in X-NET. For an exhausted finite account use a
        # tiny finite quota while keeping it disabled, never 0/unlimited.
        restore_quota = remaining
        if finite and remaining <= 0:
            restore_quota = 0.001

        create_payload: Dict[str, Any] = {
            "name": username,
            "username": str(payload.get("username") or username).strip(),
            "email": str(payload.get("email") or "").strip(),
            "comment": str(payload.get("comment") or snap.get("comment") or "").strip(),
            "uuid": uuid,
            "usage_limit_GB": restore_quota,
            "is_active": active,
        }
        if expire_date:
            create_payload["expireDate"] = expire_date

        try:
            await xnet_api.create_user(server, create_payload)
            restored += 1
            preserved_gb += used
        except Exception as exc:
            failed += 1
            if len(errors) < 5:
                errors.append(f"{username}: {str(exc)[:180]}")

    if restored:
        try:
            await xnet_api.sync_users_to_inbounds(server)
        except Exception as exc:
            if len(errors) < 5:
                errors.append(f"همگام‌سازی نهایی: {str(exc)[:180]}")

    return {
        "restored": restored,
        "failed": failed,
        "skipped": skipped,
        "preserved_gb": round(preserved_gb, 3),
        "errors": errors,
    }


async def handle_xnet_guard_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    parts = str(query.data or "").split(":")
    if len(parts) != 3:
        return
    try:
        server_id = int(parts[1])
    except Exception:
        return
    action = parts[2]

    is_main, server = _is_main_xnet(server_id)
    if not is_main or not server:
        await query.message.edit_text(
            "ℹ️ محافظ X-NET فقط برای X-NETی که به‌عنوان سرور اصلی ثبت شده فعال است. "
            "برای نودها از سرور اصلی Hiddify و سیستم Freeze استفاده می‌شود.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"server:{server_id}")]]
            ),
        )
        return

    if action in {"menu", "status"}:
        try:
            state = await _guard_state(server)
            await query.message.edit_text(
                _state_text(server, state),
                reply_markup=_menu_keyboard(server_id, missing=len(state["missing"])),
            )
        except Exception as exc:
            await query.message.edit_text(
                f"❌ خطا در بررسی محافظ X-NET:\n{str(exc)[:700]}",
                reply_markup=_menu_keyboard(server_id),
            )
        return

    if action == "snapshot":
        try:
            saved, total = await _snapshot(server)
            state = await _guard_state(server)
            await query.message.edit_text(
                f"✅ Snapshot بروزرسانی شد.\n\n"
                f"👥 کاربران خوانده‌شده: {total}\n"
                f"💾 رکوردهای ذخیره/بروزرسانی‌شده: {saved}\n"
                f"⚠️ کاربران حذف‌شده که Snapshot آنها هنوز محفوظ است: "
                f"{len(state['missing'])}",
                reply_markup=_menu_keyboard(server_id, missing=len(state["missing"])),
            )
        except Exception as exc:
            await query.message.edit_text(
                f"❌ Snapshot ساخته نشد:\n{str(exc)[:700]}",
                reply_markup=_menu_keyboard(server_id),
            )
        return

    if action == "recover_ask":
        state = await _guard_state(server)
        missing = len(state.get("missing") or [])
        if missing <= 0:
            await query.message.edit_text(
                "✅ کاربر حذف‌شده‌ای برای بازیابی پیدا نشد.",
                reply_markup=_menu_keyboard(server_id),
            )
            return
        await query.message.edit_text(
            f"⚠️ {missing} کاربر در Snapshot وجود دارد ولی در X-NET پیدا نمی‌شود.\n\n"
            "در بازیابی، UUID همان کاربر حفظ می‌شود و حجم مصرف‌شده از Snapshot/Freeze "
            "کم می‌شود؛ یعنی کاربر حجم مصرف‌شده را دوباره رایگان دریافت نمی‌کند.\n"
            "کاربران روی Inboundهای فعال فعلی ساخته و سپس همگام می‌شوند.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ بازیابی کن",
                            callback_data=f"xnetguard:{server_id}:recover_yes",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ لغو",
                            callback_data=f"xnetguard:{server_id}:menu",
                        )
                    ],
                ]
            ),
        )
        return

    if action == "recover_yes":
        await query.message.edit_text("⏳ در حال بازیابی کاربران X-NET...")
        try:
            result = await _recover_missing(server)
            err_txt = ""
            if result.get("errors"):
                err_txt = "\n\n❌ چند خطا:\n" + "\n".join(result["errors"])
            await query.message.edit_text(
                f"✅ بازیابی X-NET تمام شد.\n\n"
                f"♻️ بازیابی‌شده: {result.get('restored', 0)}\n"
                f"⏭ ردشده: {result.get('skipped', 0)}\n"
                f"❌ ناموفق: {result.get('failed', 0)}\n"
                f"🧊 مصرف محفوظ در محاسبه بازیابی: "
                f"{result.get('preserved_gb', 0.0):.3f} GB"
                f"{err_txt}",
                reply_markup=_menu_keyboard(server_id),
            )
        except Exception as exc:
            await query.message.edit_text(
                f"❌ بازیابی انجام نشد:\n{str(exc)[:700]}",
                reply_markup=_menu_keyboard(server_id),
            )
        return
