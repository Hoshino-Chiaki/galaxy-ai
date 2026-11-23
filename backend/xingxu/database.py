"""Bounded MySQL connections. Credentials are read only from an operator-owned file."""
import json
import os
import queue
import threading
import contextlib
from contextlib import contextmanager
from pathlib import Path


class DatabaseUnavailable(RuntimeError):
    pass


class MySQLPool:
    def __init__(self, file):
        import pymysql
        self.driver = pymysql
        config = json.loads(Path(file).read_text())
        if config.get('host') not in ('127.0.0.1', 'localhost'):
            raise RuntimeError('Database must use the local server endpoint')
        self.options = {key: config[key] for key in ('host', 'port', 'user', 'password', 'database')}
        self.options.update(charset='utf8mb4', autocommit=False, connect_timeout=5,
                            read_timeout=30, write_timeout=30, cursorclass=pymysql.cursors.DictCursor)
        self.idle = queue.LifoQueue(8)
        self.slots = threading.BoundedSemaphore(8)

    @contextmanager
    def connection(self):
        if not self.slots.acquire(timeout=10):
            raise DatabaseUnavailable('Database connection capacity reached')
        connection = None
        reusable = True
        try:
            try:
                connection = self.idle.get_nowait()
                connection.ping(reconnect=True)
            except queue.Empty:
                connection = self.driver.connect(**self.options)
            yield connection
            connection.commit()
        except self.driver.MySQLError:
            reusable = False
            if connection:
                with contextlib.suppress(self.driver.MySQLError):
                    connection.rollback()
            raise DatabaseUnavailable('MySQL operation failed') from None
        except BaseException:
            if connection:
                with contextlib.suppress(self.driver.MySQLError):
                    connection.rollback()
            raise
        finally:
            if connection:
                if reusable:
                    self.idle.put_nowait(connection)
                else:
                    connection.close()
            self.slots.release()


_pools = {}
_pool_lock = threading.Lock()


def database():
    file = os.environ.get('GALAXY_DATABASE_CONFIG')
    if not file:
        raise DatabaseUnavailable('MySQL is not configured')
    with _pool_lock:
        if file not in _pools:
            _pools[file] = MySQLPool(file)
        return _pools[file]
