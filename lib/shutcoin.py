import os
import json

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}


def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

SHUTCOIN_FILE = "shutcoins.json"

def get_shutcoins(user_id):
    data = load_json(SHUTCOIN_FILE)
    return data.get(str(user_id), 0)

def set_shutcoins(user_id, amount):
    data = load_json(SHUTCOIN_FILE)
    data[str(user_id)] = amount
    save_json(SHUTCOIN_FILE, data)

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
