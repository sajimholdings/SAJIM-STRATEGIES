import sqlite3
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path="trading_data.db"):
        self.db_path = db_path
        self._initialize_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _initialize_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Trades table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument TEXT,
                    direction TEXT,
                    entry_time TEXT,
                    exit_time TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    position_size REAL,
                    pnl REAL,
                    fees REAL,
                    status TEXT
                )
            """)
            
            # Equity Curve table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS equity_curve (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    balance REAL,
                    max_drawdown REAL
                )
            """)
            
            # System Status table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_status (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_heartbeat TEXT,
                    current_balance REAL,
                    peak_balance REAL,
                    open_position TEXT
                )
            """)
            conn.commit()
            
            # Ensure system status has an initial row
            cursor.execute("SELECT COUNT(*) FROM system_status")
            if cursor.fetchone()[0] == 0:
                with open("config.json", "r") as f:
                    config = json.load(f)
                initial_balance = config.get("initial_balance", 100.0)
                cursor.execute("""
                    INSERT INTO system_status (id, last_heartbeat, current_balance, peak_balance, open_position)
                    VALUES (1, ?, ?, ?, NULL)
                """, (datetime.utcnow().isoformat(), initial_balance, initial_balance))
                conn.commit()
                
                # Insert initial equity point
                self.record_equity(initial_balance, 0.0)

    def record_trade(self, instrument, direction, entry_time, entry_price, position_size, status="OPEN"):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (instrument, direction, entry_time, entry_price, position_size, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (instrument, direction, entry_time, entry_price, position_size, status))
            conn.commit()
            return cursor.lastrowid

    def update_trade_exit(self, trade_id, exit_time, exit_price, pnl, fees):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE trades 
                SET exit_time = ?, exit_price = ?, pnl = ?, fees = ?, status = 'CLOSED'
                WHERE id = ?
            """, (exit_time, exit_price, pnl, fees, trade_id))
            conn.commit()

    def get_open_trade(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                # Convert to dict
                columns = [column[0] for column in cursor.description]
                return dict(zip(columns, row))
            return None

    def record_equity(self, balance, max_drawdown):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO equity_curve (timestamp, balance, max_drawdown)
                VALUES (?, ?, ?)
            """, (datetime.utcnow().isoformat(), balance, max_drawdown))
            conn.commit()

    def update_system_status(self, balance, peak_balance, open_position_id=None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE system_status 
                SET last_heartbeat = ?, current_balance = ?, peak_balance = ?, open_position = ?
                WHERE id = 1
            """, (datetime.utcnow().isoformat(), balance, peak_balance, json.dumps(open_position_id) if open_position_id else None))
            conn.commit()

    def get_system_status(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM system_status WHERE id = 1")
            row = cursor.fetchone()
            if row:
                columns = [column[0] for column in cursor.description]
                return dict(zip(columns, row))
            return None
