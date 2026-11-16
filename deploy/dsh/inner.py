"""Runs wholly inside the jail. No real platform/provider credential is present."""
import http.client
import http.server
import json
import os
import socket
import sys
import threading


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(900)
        self.sock.connect('/run/broker.sock')


class Relay(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def forward(self):
        connection = UnixConnection('localhost')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 8 * 1024 * 1024:
                raise ValueError()
            headers = {key.lower(): value for key, value in self.headers.items()
                       if key.lower() not in ('proxy-authorization', 'connection', 'x-galaxy-run', 'transfer-encoding')}
            headers.update({'x-galaxy-run': os.environ['GALAXY_RUN_TOKEN'], 'content-length': str(size)})
            connection.request(self.command, self.path, self.rfile.read(size), headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in ('connection', 'transfer-encoding', 'server', 'date'):
                    self.send_header(key, value)
            self.send_header('Connection', 'close')
            self.end_headers()
            while chunk := response.read(64 * 1024):
                self.wfile.write(chunk)
        except (OSError, ValueError, http.client.HTTPException):
            self.close_connection = True
        finally:
            connection.close()

    do_POST = forward
    do_GET = forward
    do_PUT = forward
    do_PATCH = forward
    do_DELETE = forward
    do_HEAD = forward

    def do_CONNECT(self):
        # A Unix connection is the only route out. The server resolves and checks
        # public destinations; this process has no host network namespace.
        import select
        remote = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            remote.connect('/run/broker.sock')
            remote.sendall(('CONNECT ' + self.path + ' HTTP/1.1\r\nX-Galaxy-Run: '
                            + os.environ['GALAXY_RUN_TOKEN'] + '\r\n\r\n').encode())
            header = bytearray()
            while not header.endswith(b'\r\n\r\n') and len(header) < 8192:
                part = remote.recv(1)
                if not part:
                    return
                header.extend(part)
            self.connection.sendall(header)
            if not header.startswith(b'HTTP/1.1 200 '):
                return
            sockets = [remote, self.connection]
            while True:
                readable, _, _ = select.select(sockets, [], [], 120)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (remote if source is self.connection else self.connection).sendall(data)
        except OSError:
            pass
        finally:
            remote.close()
            self.close_connection = True


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    body = json.load(sys.stdin)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Relay)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = 'http://127.0.0.1:' + str(server.server_port)
    # DSH requires a credential-shaped value to initialize its adapter. This is
    # only the per-run broker token; it is not a provider key and expires with
    # the job. The broker never forwards it upstream.
    os.environ.update(DEEPSEEK_API_KEY=os.environ['GALAXY_RUN_TOKEN'], DEEPSEEK_BASE_URL=endpoint,
                      GALAXY_BRIDGE_URL=endpoint, DSH_SYSTEM_PROMPT=body['system_prompt'],
                      DSH_CONTEXT_WINDOW=str(body['context_window']),
                      DSH_MODEL_ID=body['model'], DSH_MAX_TOKENS=str(body.get('max_tokens') or 256000),
                      HTTP_PROXY=endpoint, HTTPS_PROXY=endpoint, http_proxy=endpoint, https_proxy=endpoint,
                      NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')

    if body.get('probe'):
        # The exact same jail/limits as DSH, with a synthetic isolation probe.
        import subprocess
        result = subprocess.run(['/usr/bin/python3', '-c', body['probe']], capture_output=True, text=True, timeout=20)
        emit({'type': 'result', 'content': result.stdout, 'finish_reason': 'completed', 'probe_exit': result.returncode})
        return
    from deepseek_harness import DeepSeekHarness

    def notification(item):
        event = item.payload.get('event', {})
        kind = event.get('type', '')
        if kind.startswith('tool/'):
            emit({'type': 'phase', 'phase': 'tool'})
        elif 'compact' in kind:
            emit({'type': 'phase', 'phase': 'summarizing'})

    try:
        with DeepSeekHarness(provider='deepseek-official', model=body['model'],
                max_tokens=body.get('max_tokens'), cwd='/workspace', dsh_home='/state',
                profile='sdk-minimal', patches=('/runtime/profile.patch.yml',),
                initialize_timeout_seconds=60, request_timeout_seconds=900) as harness:
            session = harness.resume_session(body['session_id']) if body.get('resume') else harness.start_session(body['session_id'])
            result = session.run(body['prompt'], on_notification=notification)
            if result.finish_reason != 'completed' or not result.final_response.strip():
                emit({'type': 'error', 'code': 'dsh_turn_incomplete', 'detail': result.finish_reason,
                    **({'diagnostic': {'text': result.final_response[:1500],
                        'turn_end': [e for e in result.events if e.get('type') == 'turn/end']}}
                       if os.environ.get('GALAXY_DSH_DEBUG') == '1' else {})})
                return
            emit({'type': 'result', 'content': result.final_response, 'finish_reason': result.finish_reason})
    except Exception as error:
        # Runtime diagnostics never include raw model input or session credentials.
        emit({'type': 'error', 'code': 'dsh_runtime_error', 'detail': type(error).__name__})
        if os.environ.get('GALAXY_DSH_DEBUG') == '1':
            print(str(error)[:3000], file=sys.stderr)
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
