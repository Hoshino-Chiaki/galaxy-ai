"""Real provider, synthetic owner, read-only public network and time tool only."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from cryptography.fernet import Fernet
from xingxu.ai import AiService
from xingxu.dsh_runtime import DshTurn, owner_key, worker_request
from xingxu.tool_context import ExecutionContext


async def main():
    root = Path('/opt/galaxy-ai/backend/private')
    config = json.loads((root/'ai.json').read_text())
    provider = config['providers'][config['settings']['default_provider_id']]
    user = {'id': 'dsh-smoke-' + uuid.uuid4().hex, 'workspace_id': 'dsh-synthetic-validation',
        'permissions': ['ai:chat', 'tools:galaxy_cli', 'galaxy:help', 'workspace:read']}
    context = ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),str(uuid.uuid4()),'synthetic')
    with tempfile.TemporaryDirectory(prefix='galaxy-dsh-smoke-') as temporary:
        os.environ['GALAXY_FILES_DIR'] = temporary+'/files'
        os.environ['GALAXY_DSH_BROKER_DIR'] = '/opt/galaxy-ai/hosted-files/.dsh-brokers'
        ai = AiService(Path(temporary)/'private',authorize=lambda _token:{'user':user})
        ai.cipher = Fernet((root/'ai-secret.key').read_bytes())
        state_root = Path('/opt/galaxy-ai/hosted-files/.dsh')/owner_key(context)
        async def prepare(wire):return wire
        try:
            first = DshTurn(ai,context,provider,run=None,progress={},thinking=False,reasoning_effort=None)
            result = await first.execute([{'role':'user','content':
                '这是隔离环境自检，请实际执行以下步骤：用Python计算1到100的整数之和，将纯数字保存为 /workspace/result.txt；'
                '通过galaxy-cli time now --input \'{}\' --format json 查询时间；'
                '用Python requests.get("http://pypi.org/pypi/deepseek-harness-sdk/json", timeout=25) 验证公网HTTP及跳转后的HTTPS访问，打印HTTP状态。'
                '本轮记忆口令 smoke-note-87。最后简短报告三步结果。不要调用任何业务写接口，不创建上传入口或定时任务。'}],prepare=prepare)
            file = state_root/context.conversation_id/result[-1]['run_id']/'workspace/result.txt'
            assert file.read_text().strip()=='5050'
            assert any(e['command']=='galaxy-cli time now' and e['status']=='success' for e in result[3])
            assert '200' in result[0], result[0][:300]
            second = DshTurn(ai,context,provider,run=None,progress={},thinking=False,reasoning_effort=None)
            resumed = await second.execute([{'role':'user','content':'读取上轮保存的 result.txt 并告诉我数字，以及上轮记忆口令。不要访问网络或业务接口。'}],prepare=prepare,checkpoint=result[-1])
            assert '5050' in resumed[0] and 'smoke-note-87' in resumed[0], resumed[0][:300]
            print(json.dumps({'real_provider':provider['model'],'native_protocol':first.native,
                'python_sum':5050,'time_tool':'success','public_http_https':'success','resume_memory_and_files':'success',
                'usage_first':result[1],'usage_second':resumed[1]},ensure_ascii=False))
        finally:
            for _ in range(50):
                if not (await worker_request({'command':'status'})).get('active'):break
                await asyncio.sleep(.1)
            if state_root.exists():
                assert state_root.parent==Path('/opt/galaxy-ai/hosted-files/.dsh') and not state_root.is_symlink()
                shutil.rmtree(state_root)


asyncio.run(main())
