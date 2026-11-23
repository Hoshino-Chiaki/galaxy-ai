"""Official SDK's blocking WebSocket loop lives in a supervised child, never in the API event loop."""
import ctypes
import json
import logging
import os
import select
import signal
import sys


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def main():
    os.umask(0o077)
    if sys.platform == 'linux':
        ctypes.CDLL(None).prctl(1, signal.SIGTERM)
        if os.getppid() == 1:
            return
    config = json.loads(sys.stdin.readline())
    import lark_oapi as lark
    logging.disable(logging.CRITICAL)

    def message(data):
        event, header = data.event, data.header
        source = event.message
        sender = event.sender
        if sender.sender_type != 'user' or source.chat_type != 'p2p':
            return
        body = {'type': 'message', 'app_id': header.app_id, 'tenant_key': header.tenant_key,
            'open_id': sender.sender_id.open_id, 'sender_type': sender.sender_type,
            'message_id': source.message_id, 'chat_id': source.chat_id,
            'chat_type': source.chat_type, 'message_type': source.message_type, 'content': source.content}
        emit(body)
        # ACK only after the parent durably records this message. SDK/platform retries on failure.
        if not select.select([sys.stdin], [], [], 2.5)[0]:
            raise RuntimeError('Inbox acknowledgment timed out')
        response = json.loads(sys.stdin.readline())
        if response.get('ack') != source.message_id or not response.get('accepted'):
            raise RuntimeError('Inbox did not accept message')

    class Client(lark.ws.Client):
        async def _connect(self):
            await super()._connect()
            emit({'type': 'connected'})

        async def _disconnect(self):
            await super()._disconnect()
            emit({'type': 'disconnected'})

    handler = lark.EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(message).build()
    try:
        Client(config['app_id'], config['app_secret'], event_handler=handler, log_level=lark.LogLevel.ERROR).start()
    except Exception:
        emit({'type': 'error', 'message': '无法连接飞书消息服务，请检查应用配置、机器人能力和事件订阅。'})
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
