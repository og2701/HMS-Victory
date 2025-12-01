from database import DatabaseManager
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
import time

class ShopInventory:
    """Manages shop item quantities and restocking"""

    @staticmethod
    def initialize_item(item_id: str, initial_quantity: int, max_quantity: Optional[int] = None,
                       auto_restock: bool = False, restock_amount: int = 0) -> None:
        """Initialize an item in the inventory system"""
        current_time = int(time.time())
        DatabaseManager.execute('''
            INSERT OR REPLACE INTO shop_inventory
            (item_id, quantity, max_quantity, auto_restock, restock_amount, last_restock)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (item_id, initial_quantity, max_quantity, auto_restock, restock_amount, current_time))

    @staticmethod
    def get_quantity(item_id: str) -> int:
        """Get current quantity of an item"""
        result = DatabaseManager.fetch_one('SELECT quantity FROM shop_inventory WHERE item_id = ?', (item_id,))

        return result[0] if result else 0

    @staticmethod
    def get_item_info(item_id: str) -> Optional[Dict[str, Any]]:
        """Get full inventory info for an item"""
        result = DatabaseManager.fetch_one('''
            SELECT item_id, quantity, max_quantity, auto_restock, restock_amount, last_restock
            FROM shop_inventory WHERE item_id = ?
        ''', (item_id,))

        if result:
            return {
                'item_id': result[0],
                'quantity': result[1],
                'max_quantity': result[2],
                'auto_restock': bool(result[3]),
                'restock_amount': result[4],
                'last_restock': result[5]
            }
        return None

    @staticmethod
    def consume_item(item_id: str, quantity: int = 1) -> bool:
        """Consume items from inventory. Returns True if successful."""
        # Check current quantity
        result = DatabaseManager.fetch_one('SELECT quantity FROM shop_inventory WHERE item_id = ?', (item_id,))

        if not result or result[0] < quantity:
            return False

        # Update quantity
        new_quantity = result[0] - quantity
        DatabaseManager.execute('UPDATE shop_inventory SET quantity = ? WHERE item_id = ?', (new_quantity, item_id))

        return True

    @staticmethod
    def add_stock(item_id: str, quantity: int) -> bool:
        """Add stock to an item. Returns True if successful."""
        # Get current info
        result = DatabaseManager.fetch_one('SELECT quantity, max_quantity FROM shop_inventory WHERE item_id = ?', (item_id,))

        if not result:
            return False

        current_quantity, max_quantity = result
        new_quantity = current_quantity + quantity

        # Check max quantity limit
        if max_quantity is not None and new_quantity > max_quantity:
            new_quantity = max_quantity

        DatabaseManager.execute('UPDATE shop_inventory SET quantity = ? WHERE item_id = ?', (new_quantity, item_id))

        return True

    @staticmethod
    def set_stock(item_id: str, quantity: int) -> bool:
        """Set exact stock quantity for an item"""
        # Check if item exists
        if not DatabaseManager.fetch_one('SELECT item_id FROM shop_inventory WHERE item_id = ?', (item_id,)):
            return False

        DatabaseManager.execute('UPDATE shop_inventory SET quantity = ? WHERE item_id = ?', (quantity, item_id))

        return True

    @staticmethod
    def auto_restock_items() -> List[str]:
        """Perform restocking for all items to their max quantity. Returns list of restocked item IDs."""
        current_time = int(time.time())

        # Find all items with max_quantity set (these are restockable items)
        items_to_restock = DatabaseManager.fetch_all('''
            SELECT item_id, quantity, max_quantity
            FROM shop_inventory
            WHERE max_quantity IS NOT NULL AND quantity < max_quantity
        ''')

        restocked_items = []

        for item_id, current_qty, max_qty in items_to_restock:
            # Set to max quantity
            DatabaseManager.execute('''
                UPDATE shop_inventory
                SET quantity = ?, last_restock = ?
                WHERE item_id = ?
            ''', (max_qty, current_time, item_id))

            restocked_items.append(item_id)

        return restocked_items

    @staticmethod
    def get_all_inventory() -> List[Dict[str, Any]]:
        """Get inventory info for all items"""
        results = DatabaseManager.fetch_all('''
            SELECT item_id, quantity, max_quantity, auto_restock, restock_amount, last_restock
            FROM shop_inventory
            ORDER BY item_id
        ''')

        inventory = []
        for result in results:
            inventory.append({
                'item_id': result[0],
                'quantity': result[1],
                'max_quantity': result[2],
                'auto_restock': bool(result[3]),
                'restock_amount': result[4],
                'last_restock': result[5]
            })

        return inventory

    @staticmethod
    def record_purchase(user_id: str, item_id: str, quantity: int, price_paid: int) -> None:
        """Record a purchase in the database"""
        current_time = int(time.time())
        DatabaseManager.execute('''
            INSERT INTO shop_purchases (user_id, item_id, quantity, price_paid, purchase_time)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, item_id, quantity, price_paid, current_time))

    @staticmethod
    def get_purchase_history(user_id: Optional[str] = None, item_id: Optional[str] = None,
                           limit: int = 50) -> List[Dict[str, Any]]:
        """Get purchase history, optionally filtered by user or item"""
        query = '''
            SELECT id, user_id, item_id, quantity, price_paid, purchase_time
            FROM shop_purchases
        '''
        params = []

        conditions = []
        if user_id:
            conditions.append('user_id = ?')
            params.append(user_id)
        if item_id:
            conditions.append('item_id = ?')
            params.append(item_id)

        if conditions:
            query += ' WHERE ' + ' AND '.join(conditions)

        query += ' ORDER BY purchase_time DESC LIMIT ?'
        params.append(limit)

        results = DatabaseManager.fetch_all(query, tuple(params))

        purchases = []
        for result in results:
            purchases.append({
                'id': result[0],
                'user_id': result[1],
                'item_id': result[2],
                'quantity': result[3],
                'price_paid': result[4],
                'purchase_time': result[5]
            })

        return purchases

    @staticmethod
    def setup_default_inventory():
        """Set up default inventory for existing shop items"""
        # Initialize the shutcoin item with unlimited quantity
        ShopInventory.initialize_item("shutcoin", 999999, None, True, 100)

        # Add some example limited items (uncomment and modify as needed)
        # ShopInventory.initialize_item("ball_inspector", 5, 10, True, 2)  # Limited role
        # ShopInventory.initialize_item("personal_vc", 3, 5, False, 0)     # Very limited service
        # ShopInventory.initialize_item("custom_status", 10, 15, True, 3)  # Limited service
        # ShopInventory.initialize_item("message_highlight", 20, 25, True, 5)  # Common service