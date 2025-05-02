import os, json

UKPENCE_FILE = "ukpence.json"
SHOP = {"shutcoin": 1000}

def _load():
    return json.load(open(UKPENCE_FILE)) if os.path.exists(UKPENCE_FILE) else {}

def _save(d):
    json.dump(d, open(UKPENCE_FILE, "w"), indent=4)

def get_bb(uid):             return _load().get(str(uid), 0)
def set_bb(uid, amt):        d=_load(); d[str(uid)] = amt; _save(d)
def add_bb(uid, amt):        set_bb(uid, get_bb(uid) + amt)

def remove_bb(uid, amt):
    bal = get_bb(uid)
    if amt > bal:
        return False
    set_bb(uid, bal - amt)
    return True
