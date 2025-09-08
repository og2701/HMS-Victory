import sqlite3

SHUTCOIN_ENABLED = True

def get_shutcoins(user_id):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM shutcoins WHERE user_id = ?", (str(user_id),))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

def set_shutcoins(user_id, amount):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO shutcoins (user_id, balance) VALUES (?, ?)", (str(user_id), amount))
    conn.commit()
    conn.close()

def add_shutcoins(user_id, amount):
    current = get_shutcoins(user_id)
    set_shutcoins(user_id, current + amount)

def remove_shutcoin(user_id):
    current = get_shutcoins(user_id)
    if current > 0:
        set_shutcoins(user_id, current - 1)
        return True
    return False

def can_use_shutcoin(user_id):
    return get_shutcoins(user_id) > 0