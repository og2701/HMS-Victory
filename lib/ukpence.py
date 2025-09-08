import sqlite3

SHOP = {"shutcoin": 1000}

def ensure_bb(uid):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM ukpence WHERE user_id = ?", (str(uid),))
    if c.fetchone() is None:
        c.execute("INSERT INTO ukpence (user_id, balance) VALUES (?, ?)", (str(uid), 20))
    conn.commit()
    conn.close()

def get_bb(uid):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM ukpence WHERE user_id = ?", (str(uid),))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

def set_bb(uid, amt):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO ukpence (user_id, balance) VALUES (?, ?)", (str(uid), amt))
    conn.commit()
    conn.close()

def add_bb(uid, amt):
    set_bb(uid, get_bb(uid) + amt)

def remove_bb(uid, amt):
    bal = get_bb(uid)
    if amt > bal:
        return False
    set_bb(uid, bal - amt)
    return True