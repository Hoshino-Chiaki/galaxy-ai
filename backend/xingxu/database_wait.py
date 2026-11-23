"""Short boot dependency wait; never starts a local development service."""
import time
from .database import database,DatabaseUnavailable

if __name__=='__main__':
    for attempt in range(30):
        try:
            with database().connection() as connection,connection.cursor() as cursor:
                cursor.execute('SELECT 1')
            break
        except DatabaseUnavailable:
            if attempt==29:raise SystemExit('MySQL did not become ready') from None
            time.sleep(2)
