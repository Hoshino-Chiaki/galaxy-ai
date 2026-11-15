from ...feishu_capabilities import register_domain


def prepare(value, credentials):
    """Apply server-verified employee scope before an application token is used."""
    import copy
    from datetime import datetime
    from ...registry import ToolError

    result = copy.deepcopy(value)
    params = result.setdefault('params', {})
    employee_type = params.setdefault('employee_type', 'employee_id')
    own = credentials.get('employee_id' if employee_type == 'employee_id' else 'employee_no')
    ids = result['data'].get('user_ids')
    if ids is None:
        if not own:
            raise ToolError('feishu_employee_id_required', '无法确认本人对应的员工 ID，请补全通讯录应用权限或使用已核验的员工标识。')
        ids = result['data']['user_ids'] = [own]
    if not ids or len(ids) > 50 or any(not isinstance(v, str) or not v for v in ids):
        raise ToolError('invalid_input', 'user_ids 必须包含 1 至 50 个员工标识。')
    if credentials.get('identity') != 'application':
        raise ToolError('feishu_application_identity_required', '考勤查询需要服务器提供应用身份。')
    if credentials.get('attendance_access') != 'application' and any(v != own for v in ids):
        raise ToolError('feishu_attendance_forbidden', '当前 Galaxy 账号未获公司考勤读取权限，仅允许查询本人。这是 Galaxy 网关的权限限制；提供其他员工 ID 不会改变权限。')
    try:
        dates = [datetime.strptime(str(result['data'][k]), '%Y%m%d') for k in ('check_date_from', 'check_date_to')]
        if any(len(str(result['data'][k])) != 8 for k in ('check_date_from', 'check_date_to')) or dates[0] > dates[1]:
            raise ValueError()
    except (TypeError, ValueError):
        raise ToolError('invalid_input', '工作日必须是有效的八位 YYYYMMDD，开始日期不能晚于结束日期。') from None
    return result

def register(registry):
    register_domain(registry, 'attendance')
