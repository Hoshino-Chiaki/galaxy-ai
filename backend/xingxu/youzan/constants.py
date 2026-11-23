"""Youzan URLs, status codes and user-facing messages."""
from pathlib import Path

MESSAGES = {
    "starting": "正在检查已保存的登录会话。",
    "shops": "正在获取店铺列表。",
    "shops_ready": "店铺列表已更新，请选择要连接的店铺。",
    "shop_access_required": "SID 已取得，但无法进入所选店铺，请重新选择店铺或检查店铺权限。",
    "unexpected_redirect": "有赞返回了未支持的页面跳转，SID 已保留。",
    "background_unavailable": "服务器未找到可用的 Xvfb 虚拟显示程序，请检查登录运行环境。",
    "login_runtime_unavailable": "服务器登录程序或浏览器依赖缺失，请联系管理员修复运行环境。",
    "browser_missing": "服务器 Chromium 浏览器未安装或无法启动，请检查浏览器运行环境。",
    "human_verification_required": "有赞要求人工完成滑块或安全验证，本次自动登录已暂停，未覆盖原有会话。",
    "browser_ready": "正在检查浏览器会话并登录。",
    "form_ready": "正在提交登录。",
    "slider_payload_ready": "正在处理登录验证。",
    "crm": "正在获取 CRM CSRF。",
    "sid_ready": "SID 已获取，请从店铺列表选择店铺，再刷新 CRM。",
    "success": "SID 和 CRM CSRF 已更新。",
    "csrf_success": "CRM CSRF 已更新。",
    "all_csrf": "正在逐店获取 CRM 会话。",
    "all_ready": "全部授权店铺的 CRM 会话已就绪。",
    "all_partial": "部分店铺的 CRM 会话未就绪，请查看各店铺状态后重试。",
    "sms_send_failed": "有赞未能发送验证码，请稍后重新连接。",
    "sms_code_rejected": "验证码未通过，请重新连接并获取新验证码。",
    "verification_pending": "请完成有赞登录验证。",
    "verification_submitted": "正在处理有赞验证。",
    "verification_expired": "有赞验证已超时，请重新连接。",
    "sms_verification_required": "有赞要求短信二次验证，本次登录未完成。",
    "slider_rejected": "有赞未通过滑块验证，请稍后手动重试。",
    "profile_account_mismatch": "浏览器账号与配置不一致，请检查配置。",
    "login_rejected": "有赞拒绝登录，请检查账号、密码或账号状态。",
    "credentials_missing": "请先保存有赞账号和密码。",
    "login_unconfirmed": "未确认登录成功，未覆盖已保存的 SID。",
    "browser_or_adapter_error": "登录程序执行失败，请检查服务器浏览器及登录运行环境。",
    "timeout": "登录超时，请稍后重试。",
    "interrupted": "服务已重启或任务被取消，请手动重新刷新。",
    "login_required": "SID 已失效或需要重新登录，请刷新 SID。",
    "csrf_not_found": "未从 CRM 页面取得 CSRF，请检查店铺 ID 和账号权限。",
    "upstream_error": "无法读取有赞 CRM 页面，请检查网络后重试。",
    "cancelled": "本次刷新已取消。",
    "forbidden": "当前登录已失效或权限已变更，请重新登录。",
    "failed": "刷新失败，请稍后重试。",
}
ACTIVE = {"running"}
CRM_URL = "https://crm.youzan.com/crm/member/customer/manage"
SHOP_LIST_URL = "https://www.youzan.com/v4/shop/getShopList.json"
SHOP_PAGE_URL = "https://www.youzan.com/v4/shop/shop-list"
ROOT = Path(__file__).resolve().parents[3]

