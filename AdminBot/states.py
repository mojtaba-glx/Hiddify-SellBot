# AdminBot/states.py
# تمام ثابت‌های state که بین فایل‌های مختلف استفاده می‌شوند.

# ===============================
#   افزودن سرور (wizard)
# ===============================
ADD_STATE_TYPE = "add_server_type"
ADD_STATE_TITLE = "add_server_title"
ADD_STATE_PANEL_URL = "add_server_panel_url"
ADD_STATE_ADMIN_PROXY = "add_server_admin_proxy"
ADD_STATE_ADMIN_UUID = "add_server_admin_uuid"
ADD_STATE_USER_PROXY = "add_server_user_proxy"
ADD_STATE_LIMIT = "add_server_limit"
ADD_STATE_XUI_USERNAME = "add_server_xui_username"
ADD_STATE_XUI_PASSWORD = "add_server_xui_password"
ADD_STATE_XUI_SUB_DOMAIN = "add_server_xui_sub_domain"
ADD_STATE_XUI_INBOUND = "add_server_xui_inbound"

# ===============================
#   ویرایش سرور (wizard)
# ===============================
EDIT_SERVER_TITLE = "edit_server_title"
EDIT_SERVER_PANEL_URL = "edit_server_panel_url"
EDIT_SERVER_ADMIN_PROXY = "edit_server_admin_proxy"
EDIT_SERVER_ADMIN_UUID = "edit_server_admin_uuid"
EDIT_SERVER_USER_PROXY = "edit_server_user_proxy"
EDIT_SERVER_LIMIT = "edit_server_limit"
EDIT_SERVER_PRIORITY = "edit_server_priority"
EDIT_SERVER_XUI_USERNAME = "edit_server_xui_username"
EDIT_SERVER_XUI_PASSWORD = "edit_server_xui_password"
EDIT_SERVER_XUI_SUB_DOMAIN = "edit_server_xui_sub_domain"
EDIT_SERVER_XUI_INBOUND = "edit_server_xui_inbound"
XUI_CREATE_INBOUND_FROM_LINK = "xui_create_inbound_from_link"
XUI_CREATE_INBOUND_FROM_LINK_PORT = "xui_create_inbound_from_link_port"

# ===============================
#   ویرایش کاربر
# ===============================
EDIT_STATE_NAME = "edit_user_name"
EDIT_STATE_USAGE = "edit_user_usage"
EDIT_STATE_DAYS = "edit_user_days"
EDIT_STATE_COMMENT = "edit_user_comment"

# ===============================
#   افزودن کاربر
# ===============================
ADD_USER_NAME = "add_user_name"
ADD_USER_USAGE = "add_user_usage"
ADD_USER_DAYS = "add_user_days"
ADD_USER_CONFIRM = "add_user_confirm"
ADD_USER_PLAN_NAME = "add_user_plan_name"
ADD_USER_PLAN_CONFIRM = "add_user_plan_confirm"

# ===============================
#   جستجوی هوشمند کاربر
# ===============================
SEARCH_SMART_INPUT = "search_smart_input"

# ===============================
#   stateهای مربوط به پلن‌ها (پریفیکس کلی)
# ===============================
PLANS_STATE_PREFIX = "plans:"

# ===============================
#   کلمات مربوط به لغو عملیات
# ===============================
CANCEL_WORDS = {"لغو❌", "لغو", "/cancel"}
