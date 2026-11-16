"""Use saved history only; generate one summary without tools or history writes."""
import asyncio
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from xingxu.ai import AiService
from xingxu.auth import AuthFailure
from xingxu.agent_runtime import ContextPolicy, estimate_tokens
from xingxu.conversation_context import ConversationContext, SUMMARY_INSTRUCTION


class SummaryFinished(Exception):
    pass


async def main():
    directory = Path('/opt/galaxy-ai/backend/private')
    config = json.loads((directory/'ai.json').read_text())
    provider = config['providers'][config['settings']['default_provider_id']]
    chats = json.loads((directory/'chats.json').read_text())['conversations']
    chat = next(c for c in chats.values() if c['id']==os.environ['GALAXY_SMOKE_CHAT_ID'])
    history = chat['messages']
    with tempfile.TemporaryDirectory(prefix='galaxy-summary-smoke-') as temporary:
        service = AiService(Path(temporary)/'private')
        service.cipher = Fernet((directory/'ai-secret.key').read_bytes())
        caps, summary_calls = [], []
        actual = service._model_message

        async def model(p, wire, **kwargs):
            assert kwargs.get('tools') == []
            assert kwargs.get('thinking') is False
            caps.append(p['max_tokens'])
            result = await actual(p, wire, **kwargs)
            summary_calls.append({'output_tokens':result[1].get('completion_tokens'),
                                  'estimated_summary_tokens':estimate_tokens(result[0].get('content') or '')})
            return result

        service._model_message = model

        class FirstSummary(ConversationContext):
            async def _summarize(self, history, summarize):
                async def first_only(wire):
                    value = await summarize(wire)
                    assert isinstance(value,str) and value.strip()
                    raise SummaryFinished()
                return await super()._summarize(history, first_only)

        service.runtime_name = 'langgraph'
        memory=FirstSummary(history,chat.get('context_state'),service.context_policy,deepseek=True)
        try:
            await service.complete(provider,[{'role':'user','content':'不要再次拉取数据，复用之前的数据。'}], memory=memory)
        except SummaryFinished:
            print(json.dumps({'saved_history_messages':len(history),'summary_caps':caps,
                'summary_requests':len(caps),'completed_summary':True,'summary_measurements':summary_calls,
                'business_tools_executed':0,'production_history_written':False}))


asyncio.run(main())
