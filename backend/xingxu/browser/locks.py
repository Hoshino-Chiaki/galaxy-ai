"""Serialize operations sharing an upstream session, without retaining its secret."""
import asyncio
import hashlib


class SessionLocks:
    def __init__(self, namespace):
        self.namespace = namespace
        self._locks = {}

    def get(self, secret=None, *, fallback):
        identifier = hashlib.sha256(secret.encode()).hexdigest() if secret else fallback
        return self._locks.setdefault((self.namespace, identifier), asyncio.Lock())
