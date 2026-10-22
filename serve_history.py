"""Loopback-only development server. No persistence, generation, or .env loading."""

import argparse
from wsgiref.simple_server import WSGIRequestHandler, make_server

from history_http import HistoryHTTP, origin_value
from history_service import HistoryService
from server_identity import ProfileVerifier


class SafeDevelopmentHandler(WSGIRequestHandler):
    def get_environ(self):
        environ = super().get_environ()
        environ['history.duplicate_headers'] = any(
            len(self.headers.get_all(name, [])) > 1
            for name in ('Authorization', 'Content-Length', 'Origin', 'Content-Type'))
        return environ

    def log_message(self, format, *args):
        # Default access logging includes raw request URLs; disable for privacy.
        pass

    def handle(self):
        self.request.settimeout(10)
        super().handle()


def unavailable_store(_owner):
    raise RuntimeError('Persistence is not configured')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-origin', required=True)
    parser.add_argument('--allow-loopback-backend-http', action='store_true')
    parser.add_argument('--frontend-origin', action='append', type=origin_value, default=[])
    parser.add_argument('--port', type=int, default=8081)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535')
    verifier = ProfileVerifier(args.backend_origin, allow_loopback_http=args.allow_loopback_backend_http)
    service = HistoryService(verifier, unavailable_store, enabled=False)
    app = HistoryHTTP(service, allowed_origins=args.frontend_origin)
    with make_server('127.0.0.1', args.port, app, handler_class=SafeDevelopmentHandler) as server:
        print(f'Local history adapter: http://127.0.0.1:{args.port}; persistence/generation DISABLED', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()