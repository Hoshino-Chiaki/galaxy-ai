"""Text/tool bridge between DSH Messages and Galaxy's configured model gateway."""
import json
import uuid


def text(blocks):
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        raise ValueError('Invalid content')
    parts = []
    for block in blocks:
        if block.get('type') == 'text':
            parts.append(block.get('text', ''))
        else:
            raise ValueError('Only text tool results are supported')
    return '\n'.join(parts)


def messages_request(body):
    """Preserve call/result order and reasoning; never interpret historical tool calls."""
    messages = []
    if body.get('system'):
        messages.append({'role': 'system', 'content': text(body['system'])})
    for entry in body['messages']:
        role, content = entry['role'], entry['content']
        if role not in ('user', 'assistant'):
            raise ValueError('Invalid role')
        if isinstance(content, str):
            messages.append({'role': role, 'content': content})
            continue
        pending, calls, reasoning = [], [], []

        def flush():
            if pending:
                messages.append({'role': role, 'content': '\n'.join(pending)})
                pending.clear()

        for block in content:
            kind = block.get('type')
            if kind == 'text':
                pending.append(block.get('text', ''))
            elif kind == 'thinking' and role == 'assistant':
                reasoning.append(block.get('thinking', ''))
            elif kind == 'tool_use' and role == 'assistant':
                calls.append({'id': block['id'], 'type': 'function', 'function': {
                    'name': block['name'], 'arguments': json.dumps(block['input'], ensure_ascii=False)}})
            elif kind == 'tool_result' and role == 'user':
                flush()
                messages.append({'role': 'tool', 'tool_call_id': block['tool_use_id'],
                                 'content': text(block.get('content', []))})
            else:
                raise ValueError('Unsupported content block')
        if role == 'assistant':
            message = {'role': role, 'content': '\n'.join(pending) or None,
                       'reasoning_content': '\n'.join(reasoning)}
            if calls:
                message['tool_calls'] = calls
            messages.append(message)
        else:
            flush()
    tools = [{'type': 'function', 'function': {'name': t['name'],
              'description': t.get('description', ''), 'parameters': t['input_schema']}}
             for t in body.get('tools', [])]
    return messages, tools


def messages_response(assistant, usage, model, *, stream=True):
    blocks = []
    if assistant.get('reasoning_content'):
        blocks.append({'type': 'thinking', 'thinking': assistant['reasoning_content']})
    if assistant.get('content'):
        blocks.append({'type': 'text', 'text': assistant['content']})
    for call in assistant.get('tool_calls', []):
        blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'],
                       'input': json.loads(call['function']['arguments'])})
    reason = 'tool_use' if assistant.get('tool_calls') else 'end_turn'
    counts = {'input_tokens': usage.get('prompt_tokens', 0), 'output_tokens': usage.get('completion_tokens', 0)}
    message = {'id': 'msg_' + uuid.uuid4().hex, 'type': 'message', 'role': 'assistant',
               'model': model, 'content': blocks, 'stop_reason': reason, 'stop_sequence': None, 'usage': counts}
    if not stream:
        return 'application/json', json.dumps(message, ensure_ascii=False).encode()
    events = []

    def emit(kind, **values):
        events.append('event: ' + kind + '\ndata: ' + json.dumps({'type': kind, **values}, ensure_ascii=False) + '\n\n')

    emit('message_start', message={**message, 'content': [], 'stop_reason': None,
                                  'usage': {**counts, 'output_tokens': 0}})
    for i, block in enumerate(blocks):
        kind = block['type']
        empty = ({'type': 'text', 'text': ''} if kind == 'text' else
                 {'type': 'thinking', 'thinking': ''} if kind == 'thinking' else {**block, 'input': {}})
        emit('content_block_start', index=i, content_block=empty)
        delta = ({'type': 'text_delta', 'text': block['text']} if kind == 'text' else
                 {'type': 'thinking_delta', 'thinking': block['thinking']} if kind == 'thinking' else
                 {'type': 'input_json_delta', 'partial_json': json.dumps(block['input'], ensure_ascii=False)})
        emit('content_block_delta', index=i, delta=delta)
        emit('content_block_stop', index=i)
    emit('message_delta', delta={'stop_reason': reason, 'stop_sequence': None}, usage=counts)
    emit('message_stop')
    return 'text/event-stream', ''.join(events).encode()
