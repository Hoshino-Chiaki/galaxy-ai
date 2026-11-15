"""Server-only, read-only business check; credentials enter stdin and stay private."""
import asyncio
import json
import sys
import uuid
from pathlib import Path

from cryptography.fernet import Fernet

from xingxu.auth import AuthFailure, AuthService, COMPANY_ATTENDANCE_READ
from xingxu.feishu.service import FeishuService
from xingxu.store import JsonRepository
from xingxu.tools import ExecutionContext, GalaxyDispatcher


def task_rows(value):
    if isinstance(value, dict):
        if isinstance(value.get('user_task_results'), list):
            return value['user_task_results']
        for key in ('data', 'response'):
            if key in value:
                found = task_rows(value[key])
                if found is not None:
                    return found
    return None


async def main():
    if Path(__file__).resolve().parent != Path('/opt/galaxy-ai/backend'):
        raise RuntimeError('This verification runs only on the server')
    values = json.load(sys.stdin)
    directory = Path('/opt/galaxy-ai/backend/private')
    auth = AuthService(JsonRepository(directory))
    service = FeishuService(directory, Fernet((directory / 'ai-secret.key').read_bytes()))
    dispatcher = GalaxyDispatcher(directory, auth.identity)
    dispatcher.feishu_provider = service
    sessions = []
    try:
        owner = values['accounts'][0]
        session = auth.login(owner['username'], owner['password'], False, 'company-cli-read-verification')
        sessions.append(session)
        user = session['user']
        if user['role'] != 'admin' or COMPANY_ATTENDANCE_READ not in user['permissions']:
            raise RuntimeError('Administrator company capability is not available')

        def context(current):
            actor = current['user']
            return ExecutionContext(actor['id'], actor['workspace_id'], tuple(actor['permissions']),
                                    'company-verification-' + str(uuid.uuid4()), current['access_token'])

        async def execute(current, args):
            call = {'id': str(uuid.uuid4()), 'type': 'function', 'function': {
                'name': 'galaxy_cli', 'arguments': json.dumps({'args': args})}}
            return await dispatcher.execute(context(current), call)

        clock = await execute(session, ['time', 'now'])
        if clock['status'] != 'success':
            raise RuntimeError('Clock command failed')
        day = int(clock['result']['today'].replace('-', ''))
        personal = await service.credentials(user)
        identity = await service.oauth.identity(personal['access_token'])
        # Force a small page to verify real cursor forwarding through the same
        # model-callable CLI, rather than using a private diagnostic API path.
        pages, cursors, request = [], set(), {'page_size': 1}
        while True:
            page = await execute(session, ['feishu', 'authorized-users', '--input', json.dumps(request)])
            if page['status'] != 'success' or page['result'].get('coverage') != 'application_authorized_scope':
                raise RuntimeError('Could not read authorized users through Galaxy CLI')
            data = page['result']['data']
            pages.append(data)
            if not data.get('has_more'):
                break
            cursor = data.get('page_token')
            if not cursor or cursor in cursors:
                raise RuntimeError('Invalid or repeated directory pagination cursor')
            cursors.add(cursor)
            request['page_token'] = cursor
        ids = list(dict.fromkeys(identifier for page in pages for identifier in page.get('user_ids', [])))
        department_ids = {identifier for page in pages for identifier in page.get('department_ids', [])}
        other = next((identifier for identifier in ids if identifier != identity.get('user_id')), None)
        if not other:
            raise RuntimeError('No other explicitly authorized employee is available')

        def args(identifiers):
            body = {'action': 'user_tasks.query', 'params': {'employee_type': 'employee_id'},
                    'data': {'user_ids': identifiers, 'check_date_from': day, 'check_date_to': day}}
            return ['feishu', 'attendance', '--input', json.dumps(body)]

        single = await execute(session, args([other]))
        single_rows = task_rows(single.get('result'))
        batch = await execute(session, args(ids[:50]))
        batch_rows = task_rows(batch.get('result'))
        profile = await execute(session, ['feishu', 'authorized-users', '--input',
            json.dumps({'action': 'user', 'user_id': other})])
        department = await execute(session, ['feishu', 'authorized-users', '--input',
            json.dumps({'action': 'users', 'department_id': '0'})])
        # Deliberately invalid ID verifies error fidelity, never administrator
        # status; authorized data above already proves the actual query access.
        invalid = await execute(session, args(['__invalid_employee_for_error_verification__']))
        # Confirm an actual other employee appears in business data, not just
        # an accepted request or the caller's own result.
        other_returned = bool(single_rows and other in json.dumps(single_rows))
        summary = service.summary(user)
        report = {'verification': 'authenticated_gateway_to_galaxy_cli', 'date': day,
            'galaxy_role': user['role'], 'company_permission': True,
            'platform_super_admin': summary['management']['is_tenant_manager'],
            'attendance_access': summary['attendance_access'],
            'other_employee_query_status': single['status'],
            'other_employee_results': len(single_rows) if single_rows is not None else None,
            'requested_other_employee_returned': other_returned,
            'batch_status': batch['status'], 'batch_requested': min(50, len(ids)),
            'batch_employee_results': len(batch_rows) if batch_rows is not None else None,
            'authorized_explicit_users': len(ids),
            'authorized_departments': len(department_ids), 'directory_pages': len(pages),
            'scope_has_more': bool(data.get('has_more')), 'directory_tool_used': True,
            'profile_status': profile['status'],
            'department_status': department['status'],
            'department_upstream_code': department.get('result', {}).get('error', {}).get('upstream_code'),
            'invalid_id_status': invalid['status'],
            'invalid_id_upstream_code': invalid.get('result', {}).get('error', {}).get('upstream_code'),
            'company_wide_coverage_claimed': False, 'tokens_exported': False}
        if single['status'] != 'success':
            report['error_code'] = single.get('result', {}).get('error', {}).get('code')
        if len(values['accounts']) > 1:
            employee = values['accounts'][1]
            staff = auth.login(employee['username'], employee['password'], False, 'company-cli-read-verification')
            sessions.append(staff)
            if staff['user']['role'] != 'employee':
                raise RuntimeError('Second account is not an employee')
            report['employee_has_company_permission'] = COMPANY_ATTENDANCE_READ in staff['user']['permissions']
            staff_directory = await execute(staff, ['feishu', 'authorized-users'])
            report['employee_directory_status'] = staff_directory['status']
            report['employee_directory_error'] = staff_directory.get('result', {}).get('error', {}).get('code')
            staff_id = None
            try:
                staff_token = await service.credentials(staff['user'])
                staff_identity = await service.oauth.identity(staff_token['access_token'])
                staff_id = staff_identity.get('user_id')
            except AuthFailure:
                pass
            staff_target = next(identifier for identifier in ids if identifier != staff_id)
            denied = await execute(staff, args([staff_target]))
            report['employee_query_status'] = denied['status']
            report['employee_error_code'] = denied.get('result', {}).get('error', {}).get('code')
        print(json.dumps(report, ensure_ascii=False))
        if single['status'] != 'success' or batch['status'] != 'success' or not other_returned:
            raise SystemExit(1)
        if profile['status'] != 'success' or type(report['invalid_id_upstream_code']) is not int:
            raise SystemExit(1)
        if report.get('employee_has_company_permission') or (
                'employee_query_status' in report and report['employee_query_status'] != 'denied'):
            raise SystemExit(1)
        if 'employee_directory_status' in report and report['employee_directory_status'] != 'denied':
            raise SystemExit(1)
    finally:
        for current in sessions:
            auth.logout(current['access_token'])


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        # Never print credential-bearing source, raw responses, or exception text.
        print(json.dumps({'verification_failed': type(error).__name__, 'code': getattr(error, 'code', None)}))
        raise SystemExit(1) from None
