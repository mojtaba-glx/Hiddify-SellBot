import re

from Shared import i18n


def is_cancel_text(text: str | None) -> bool:
    """Recognize cancel commands and reply buttons in every supported language."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if re.fullmatch(r"/cancel(?:@[a-z0-9_]+)?", raw, flags=re.IGNORECASE):
        return True

    # Ignore button decoration and Telegram direction marks, but match whole labels.
    key = re.sub(r"[^\w]+", "", raw).casefold()
    return bool(key) and any(
        key == re.sub(r"[^\w]+", "", i18n.t("btn_cancel", lang)).casefold()
        for lang in i18n.supported_langs()
    )
