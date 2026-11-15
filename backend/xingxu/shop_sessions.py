"""Compatibility imports; shop-specific sessions live in the Youzan integration."""
from .youzan.sessions import ShopSessions, sid_stamp

__all__ = ['ShopSessions', 'sid_stamp']
