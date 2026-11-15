"""Shared browser transport and process primitives; no integration-specific rules."""
from .http import ResponseLimitExceeded, open_session, read_page
from .locks import SessionLocks
from .process import OutputLimitExceeded, VirtualDisplay, run_json_process

__all__ = ['ResponseLimitExceeded', 'open_session', 'read_page', 'SessionLocks',
           'OutputLimitExceeded', 'VirtualDisplay', 'run_json_process']
