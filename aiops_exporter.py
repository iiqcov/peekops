#!/usr/bin/env python3
"""
aiops_exporter.py
Always-on Prometheus multiprocess exporter.
Reads gauge files written by main.py and exposes them on :8000.

systemd: aiops-exporter.service
"""

import os
import config

os.environ['PROMETHEUS_MULTIPROC_DIR'] = config.PROMETHEUS_MULTIPROC_DIR
os.makedirs(config.PROMETHEUS_MULTIPROC_DIR, exist_ok=True)

from prometheus_client import make_wsgi_app, multiprocess, CollectorRegistry
from wsgiref.simple_server import make_server, WSGIRequestHandler


class _SilentHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def app(environ, start_response):
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return make_wsgi_app(registry)(environ, start_response)


if __name__ == "__main__":
    port = config.PROMETHEUS_PORT
    print(f"[aiops-exporter] started → http://0.0.0.0:{port}/metrics")
    make_server("0.0.0.0", port, app, handler_class=_SilentHandler).serve_forever()
