import os
import re
import time
import uuid
import random
import email
import sqlite3
import imaplib
import threading
import json
import hmac
import hashlib
from datetime import datetime, timedelta
from functools import wraps

import requests
import qrcode
from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

from cryptography.fernet import Fernet

# --- ENCRYPTION SETUP ---
KEY_FILE = 'secret.key'
if not os.path.exists(KEY_FILE):
    with open(KEY_FILE, 'wb') as key_file:
        key_file.write(Fernet.generate_key())

with open(KEY_FILE, 'rb') as key_file:
    ENCRYPTION_KEY = key_file.read()

cipher_suite = Fernet(ENCRYPTION_KEY)

def encrypt_pass(plain_text):
    if not plain_text: return None
    return cipher_suite.encrypt(plain_text.encode('utf-8')).decode('utf-8')

def decrypt_pass(cipher_text):
    if not cipher_text: return None
    try:
        return cipher_suite.decrypt(cipher_text.encode('utf-8')).decode('utf-8')
    except Exception:
        # Fallback for plain-text passwords saved before this update
        return cipher_text

# ============================================
# SERVER CONFIGURATION
# ============================================
DB_FILE = "fampay_gateway.db"
PORT = int(os.environ.get("PORT", 5000))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fampay-super-secret-key")

# ============================================
# DATABASE INITIALIZATION
# ============================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            upi_id TEXT,
            gmail TEXT,
            app_pass TEXT,
            api_key TEXT UNIQUE,
            created_at DATETIME,
            display_name TEXT DEFAULT 'Merchant',
            theme TEXT DEFAULT 'default',
            provider TEXT DEFAULT 'fampay'
        )
    ''')
    try: c.execute("ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT 'Merchant'")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'default'")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN provider TEXT DEFAULT 'fampay'")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN username TEXT UNIQUE")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    except: pass
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            txn_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            utr TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME,
            expires_at DATETIME,
            paid_at DATETIME,
            merchant_order_id TEXT,
            customer_name TEXT,
            callback_url TEXT
        )
    ''')
    try: c.execute("ALTER TABLE transactions ADD COLUMN merchant_order_id TEXT")
    except: pass
    try: c.execute("ALTER TABLE transactions ADD COLUMN customer_name TEXT")
    except: pass
    try: c.execute("ALTER TABLE transactions ADD COLUMN callback_url TEXT")
    except: pass
    try: c.execute("ALTER TABLE transactions ADD COLUMN customer_email TEXT")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'merchant'")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN plan_name TEXT DEFAULT 'Free'")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN plan_expiry TEXT")
    except: pass

    c.execute("CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('maintenance_mode', 'false')")
    c.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('admin_password', 'admin123')")
    c.execute("CREATE TABLE IF NOT EXISTS system_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, message TEXT, created_at TEXT)")
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS webhook_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            txn_id TEXT,
            url TEXT,
            payload TEXT,
            response_code INTEGER,
            response_body TEXT,
            sent_at DATETIME
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            log_msg TEXT,
            log_time DATETIME
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT upi_id, gmail, app_pass, api_key, display_name, theme, username, provider, profile_pic, role, plan_name, plan_expiry FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "upi_id": row[0],
            "gmail": row[1],
            "app_pass": row[2],
            "api_key": row[3],
            "display_name": row[4] or "Merchant",
            "theme": row[5] or "default",
            "username": row[6],
            "provider": row[7] or "fampay",
            "profile_pic": row[8] if len(row) > 8 and row[8] else None,
            "role": row[9] if len(row) > 9 and row[9] else "merchant",
            "plan_name": row[10] if len(row) > 10 and row[10] else "Free",
            "plan_expiry": row[11] if len(row) > 11 and row[11] else None
        }
    return None

def save_user_account(user_id, upi_id, gmail, app_pass, provider='fampay'):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT api_key FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    api_key = row[0] if row and row[0] else "FAM_" + uuid.uuid4().hex + uuid.uuid4().hex[:12]
    c.execute('''UPDATE users SET upi_id=?, gmail=?, app_pass=?, api_key=?, provider=? WHERE user_id=?''', 
              (upi_id, gmail, encrypt_pass(app_pass), api_key, provider, user_id))
    conn.commit()
    conn.close()
    return api_key

# ============================================
# AUTHENTICATION
# ============================================
from functools import wraps
from datetime import timedelta

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return decorated_function

def get_sys_setting(key, default=None):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT value FROM system_settings WHERE key=?", (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default
    except:
        return default

def set_sys_setting(key, value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE system_settings SET value=? WHERE key=?", (value, key))
    conn.commit()
    conn.close()

def add_sys_log(type_str, msg):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO system_logs (type, message, created_at) VALUES (?, ?, ?)", (type_str, msg, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except:
        pass

@app.before_request
def check_maintenance():
    if request.path.startswith('/admin') or request.path.startswith('/static') or request.path == '/api/create-order' or request.path.startswith('/pay'):
        return
    if get_sys_setting('maintenance_mode') == 'true':
        return "<h1>Platform Under Maintenance</h1><p>We are upgrading our systems. Please check back in a few minutes.</p>", 503

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        pwd = request.form.get('password')
        actual_pwd = get_sys_setting('admin_password', 'admin123')
        if pwd == actual_pwd:
            session['admin_logged_in'] = True
            return redirect('/admin')
        else:
            error = "Invalid Password"
    return render_template('admin_login.html', error=error)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect('/admin/login')

@app.route('/admin/settings', methods=['POST'])
@admin_required
def admin_settings():
    m_mode = request.form.get('maintenance_mode', 'false')
    new_pass = request.form.get('admin_password')
    set_sys_setting('maintenance_mode', m_mode)
    if new_pass and len(new_pass) > 2:
        set_sys_setting('admin_password', new_pass)
    return redirect('/admin?success=Settings updated')

@app.route('/admin/logs')
@admin_required
def admin_logs():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT type, message, created_at FROM system_logs ORDER BY id DESC LIMIT 100")
    logs = c.fetchall()
    conn.close()
    return render_template('admin_logs.html', logs=logs)

@app.route('/admin/users')
@admin_required
def admin_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, username, display_name, plan_name, plan_expiry, role FROM users")
    all_users = c.fetchall()
    conn.close()
    return render_template('admin_users.html', all_users=all_users)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT user_id, password_hash FROM users WHERE username=?", (username,))
        row = c.fetchone()
        conn.close()
        
        if row and check_password_hash(row[1], password):
            session['user_id'] = row[0]
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            return render_template('register.html', error='Username and password required')
            
        password_hash = generate_password_hash(password)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)", (username, password_hash, datetime.now().isoformat()))
            user_id = c.lastrowid
            conn.commit()
            session['user_id'] = user_id
            return redirect(url_for('dashboard'))
        except sqlite3.IntegrityError:
            return render_template('register.html', error='Username already exists')
        finally:
            conn.close()
    return render_template('register.html')


@app.route('/subscription')
@login_required
def subscription():
    user_id = session['user_id']
    user_info = get_user(user_id)
    return render_template('subscription.html', user_info=user_info)

@app.route('/admin')
@admin_required
def admin_panel():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Get all users limit 10 for overview
    c.execute("SELECT user_id, username, display_name, plan_name, plan_expiry, role FROM users LIMIT 10")
    all_users = c.fetchall()
    
    # Get Stats
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM transactions")
    total_orders = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM transactions WHERE status='completed'")
    successful_orders = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM transactions WHERE status='pending'")
    pending_orders = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM transactions WHERE status IN ('failed', 'expired')")
    failed_orders = c.fetchone()[0] or 0
    c.execute("SELECT SUM(amount) FROM transactions WHERE status='completed'")
    total_revenue = c.fetchone()[0] or 0.0
    conn.close()
    
    stats = {
        "total_revenue": round(total_revenue, 2),
        "total_users": total_users,
        "total_orders": total_orders,
        "successful": successful_orders,
        "pending": pending_orders,
        "failed": failed_orders
    }
    
    m_mode = get_sys_setting('maintenance_mode', 'false')
    
    return render_template('admin.html', all_users=all_users, stats=stats, maintenance_mode=m_mode)

@app.route('/admin/update_plan', methods=['POST'])
@admin_required
def admin_update_plan():
    target_user = request.form.get('target_user')
    new_plan = request.form.get('new_plan')
    expiry_days = int(request.form.get('expiry_days', 30))
    if expiry_days > 0:
        expiry_date = (datetime.now() + timedelta(days=expiry_days)).isoformat()
    else:
        expiry_date = None
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET plan_name=?, plan_expiry=? WHERE user_id=?", (new_plan, expiry_date, target_user))
    conn.commit()
    conn.close()
    return redirect('/admin?success=Plan updated successfully')


