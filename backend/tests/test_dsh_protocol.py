import json
import unittest
from xingxu.dsh_protocol import messages_request, messages_response


class ProtocolTests(unittest.TestCase):
    def test_tool_roundtrip_preserves_reasoning_and_call_ids(self):
        wire, tools = messages_request({'system': [{'type': 'text', 'text': 'system'}], 'messages': [
            {'role': 'user', 'content': 'task'}, {'role': 'assistant', 'content': [
                {'type': 'thinking', 'thinking': 'reason'},
                {'type': 'tool_use', 'id': 'c1', 'name': 'bash', 'input': {'command': 'true'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'c1',
                'content': [{'type': 'text', 'text': 'done'}]}]}],
            'tools': [{'name': 'bash', 'input_schema': {'type': 'object'}}]})
        self.assertEqual(wire[2]['reasoning_content'], 'reason')
        self.assertEqual(wire[3]['tool_call_id'], 'c1')
        self.assertEqual(tools[0]['function']['name'], 'bash')
        kind, response = messages_response(wire[2], {'prompt_tokens': 9, 'completion_tokens': 4}, 'model')
        self.assertEqual(kind, 'text/event-stream')
        self.assertIn('"stop_reason": "tool_use"', response.decode())
        self.assertIn('input_json_delta', response.decode())
        self.assertIn('thinking_delta', response.decode())

    def test_nonstream_and_unsupported_block(self):
        kind, response = messages_response({'content': 'done'}, {}, 'model', stream=False)
        self.assertEqual(json.loads(response)['stop_reason'], 'end_turn')
        with self.assertRaises(ValueError):
            messages_request({'messages': [{'role': 'user', 'content': [{'type': 'image'}]}]})
