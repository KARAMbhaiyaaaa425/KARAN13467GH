import os
import re
import time
import uuid
import random
import email
import sqlite3
import imaplib
import threading
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
            theme TEXT DEFAULT 'default'
        )
    ''')
    try: c.execute("ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT 'Merchant'")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'default'")
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
    
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT upi_id, gmail, app_pass, api_key, display_name, theme, username FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"upi_id": row[0], "gmail": row[1], "app_pass": row[2], "api_key": row[3], "display_name": row[4] or 'Merchant', "theme": row[5] or 'default', "username": row[6]}
    return None

def save_user_account(user_id, upi_id, gmail, app_pass):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT api_key FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    api_key = row[0] if row and row[0] else "FAM_" + uuid.uuid4().hex + uuid.uuid4().hex[:12]
    c.execute('''UPDATE users SET upi_id=?, gmail=?, app_pass=?, api_key=? WHERE user_id=?''', 
              (upi_id, gmail, encrypt_pass(app_pass), api_key, user_id))
    conn.commit()
    conn.close()
    return api_key

# ============================================
# AUTHENTICATION
# ============================================
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

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

# ============================================
# FLASK WEB INTERFACE (DASHBOARD)
# ============================================

@app.route('/')
@login_required
def dashboard():
    user_id = session['user_id']
    error = request.args.get('error')
    success = request.args.get('success')
    user_info = get_user(user_id)
    
    # Get stats
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(amount) FROM transactions WHERE user_id=? AND status='completed'", (user_id,))
    total_count, total_amount = c.fetchone()
    
    # Recent transactions (all statuses)
    c.execute("SELECT txn_id, amount, utr, paid_at, status FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 15", (user_id,))
    txns = c.fetchall()
    
    # Chart Data: Revenue for last 7 days
    from datetime import datetime, timedelta
    chart_labels = []
    chart_data = []
    for i in range(6, -1, -1):
        dt = datetime.now() - timedelta(days=i)
        d_str = dt.strftime('%Y-%m-%d')
        c.execute("SELECT SUM(amount) FROM transactions WHERE user_id=? AND status='completed' AND paid_at LIKE ?", (user_id, f"{d_str}%"))
        daily_sum = c.fetchone()[0] or 0
        chart_labels.append(dt.strftime('%d %b'))
        chart_data.append(daily_sum)

    conn.close()
    
    return render_template('dashboard.html', 
                           user_info=user_info, 
                           total_count=total_count or 0, 
                           total_amount=f"{total_amount or 0:.2f}",
                           txns=txns,
                           chart_labels=chart_labels,
                           chart_data=chart_data,
                           error=error,
                           success=success)

@app.route('/save_account', methods=['POST'])
@login_required
def save_account():
    user_id = session['user_id']
    upi_id = request.form.get('upi_id')
    gmail = request.form.get('gmail')
    app_pass = request.form.get('app_pass')
    
    if upi_id and gmail and app_pass:
        if '@' not in upi_id:
            return redirect(url_for('dashboard', error='Invalid UPI ID format! Must contain @'))
            
        if '@' not in gmail or '.com' not in gmail:
            return redirect(url_for('dashboard', error='Invalid Gmail Address!'))
            
        # Verify the IMAP connection instantly
        try:
            import imaplib
            mail = imaplib.IMAP4_SSL('imap.gmail.com', timeout=5)
            mail.login(gmail, app_pass)
            mail.logout()
        except Exception as e:
            return redirect(url_for('dashboard', error='Connection Failed! Please check your Gmail App Password.'))
            
        save_user_account(user_id, upi_id, gmail, app_pass)
        return redirect(url_for('dashboard', success='Account Connected & Verified Perfectly!'))
        
    return redirect(url_for('dashboard'))


@app.route('/save_customize', methods=['POST'])
@login_required
def save_customize():
    user_id = session['user_id']
    display_name = request.form.get('display_name', 'Merchant')
    theme = request.form.get('theme', 'default')
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET display_name=?, theme=? WHERE user_id=?", (display_name, theme, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard', success='Customization Saved!'))

@app.route('/delete_account')
@login_required
def delete_account():
    user_id = session['user_id']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Just clear the payment details
    c.execute("UPDATE users SET upi_id=NULL, gmail=NULL, app_pass=NULL WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard', success='Account Connection Deleted!'))

@app.route('/generate_link', methods=['POST'])
@login_required
def generate_link():
    user_id = session['user_id']
    amount = request.form.get('amount')
    user_info = get_user(user_id)
    if user_info and user_info.get('api_key') and amount:
        return redirect(f"/pay?api_key={user_info['api_key']}&amount={amount}")
    return redirect(url_for('dashboard', error='Please connect account first'))

# ============================================
# PAYMENT GATEWAY API & CHECKOUT
# ============================================


@app.route('/export/transactions')
def export_transactions():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user_id = session['user_id']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT txn_id, amount, utr, status, created_at, paid_at FROM transactions WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    
    import csv
    from io import StringIO
    from flask import Response
    
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Transaction ID', 'Amount (INR)', 'UTR', 'Status', 'Created At', 'Paid At'])
    cw.writerows(rows)
    
    output = si.getvalue()
    return Response(output, mimetype="text/csv", headers={"Content-disposition": "attachment; filename=transactions.csv"})

@app.route('/api/create-order', methods=['POST'])
def api_create_order():
    api_key = request.headers.get('X-Fam-Key') or request.json.get('api_key')
    if not api_key:
        return jsonify({"status": "error", "message": "Missing X-Fam-Key header"}), 401
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE api_key = ?", (api_key,))
    user = c.fetchone()
    if not user:
        conn.close()
        return jsonify({"status": "error", "message": "Invalid API Key"}), 401
    
    user_id = user[0]
    data = request.json or {}
    
    amount_raw = data.get('amount')
    merchant_order_id = data.get('order_id')
    customer_name = data.get('customer_name')
    callback_url = data.get('callback_url')
    
    if not amount_raw:
        return jsonify({"status": "error", "message": "Missing amount"}), 400
        
    try:
        amount = float(amount_raw)
        if amount <= 0: raise ValueError
        # Dynamic Amount Logic
        if amount == int(amount):
            amount += round(random.uniform(0.01, 0.99), 2)
        amount = round(amount, 2)
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid amount"}), 400

    txn_id = f"FAM{int(time.time())}{uuid.uuid4().hex[:4].upper()}"
    now = datetime.now()
    expires = now + timedelta(minutes=5)

    c.execute('''INSERT INTO transactions (txn_id, user_id, amount, status, created_at, expires_at, merchant_order_id, customer_name, callback_url)
                 VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)''', 
              (txn_id, user_id, amount, now.isoformat(), expires.isoformat(), merchant_order_id, customer_name, callback_url))
    conn.commit()
    conn.close()
    
    payment_url = f"{request.host_url.rstrip('/')}/pay/{txn_id}"
    
    return jsonify({
        "status": "success",
        "payment_url": payment_url,
        "txn_id": txn_id
    })

@app.route('/pay/<txn_id>', methods=['GET'])
def checkout_page_by_id(txn_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, amount, status, callback_url FROM transactions WHERE txn_id = ?", (txn_id,))
    txn = c.fetchone()
    
    if not txn:
        conn.close()
        return "<h1>Error: Transaction not found</h1>", 404
        
    user_id, amount, status, callback_url = txn
    
    c.execute("SELECT upi_id, display_name, theme, api_key FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    
    if not user or not user[0]:
        return "<h1>Error: Merchant account not configured properly</h1>", 400
        
    upi_id, display_name, theme, api_key = user
    
    payment_url = f"upi://pay?pa={upi_id}&pn=Merchant&tr={txn_id}&am={amount}&cu=INR"
    
    qr = qrcode.make(payment_url)
    qr_path = f"static/qr_{txn_id}.png"
    os.makedirs("static", exist_ok=True)
    qr.save(qr_path)
    
    return render_template('checkout.html', 
                           amount=f"{amount:.2f}",
                           txn_id=txn_id,
                           api_key=api_key,
                           payment_url=payment_url,
                           qr_url=f"/qr/{txn_id}",
                           upi_id=upi_id,
                           display_name=display_name or 'Merchant',
                           theme=theme or 'default',
                           status=status,
                           callback_url=callback_url)

@app.route('/pay', methods=['GET'])
def checkout_page_legacy():
    api_key = request.args.get('api_key')
    amount_raw = request.args.get('amount')

    if not api_key or not amount_raw:
        return "<h1>Error: Missing api_key or amount</h1>", 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, upi_id, display_name, theme FROM users WHERE api_key = ?", (api_key,))
    user = c.fetchone()
    
    if not user or not user[1]:
        return "<h1>Error: Invalid API Key or Not Configured</h1>", 401
    
    user_id, upi_id, display_name, theme = user

    try:
        amount = float(amount_raw)
        if amount <= 0: raise ValueError
        # Dynamic Amount Logic
        if amount == int(amount):
            amount += round(random.uniform(0.01, 0.99), 2)
        amount = round(amount, 2)
    except ValueError:
        return "<h1>Error: Invalid amount</h1>", 400

    txn_id = f"FAM{int(time.time())}{uuid.uuid4().hex[:4].upper()}"
    now = datetime.now()
    expires = now + timedelta(minutes=5)

    c.execute('''INSERT INTO transactions (txn_id, user_id, amount, status, created_at, expires_at)
                 VALUES (?, ?, ?, 'pending', ?, ?)''', 
              (txn_id, user_id, amount, now.isoformat(), expires.isoformat()))
    conn.commit()
    conn.close()

    return redirect(url_for('checkout_page_by_id', txn_id=txn_id))


@app.route('/qr/<txn_id>')
def serve_qr(txn_id):
    qr_path = f"static/qr_{txn_id}.png"
    if os.path.exists(qr_path):
        return send_file(qr_path, mimetype='image/png')
    return jsonify({"error": "QR code not found"}), 404



@app.route('/api/cancel_txn', methods=['POST'])
def cancel_txn():
    txn_id = request.json.get('txn_id')
    if not txn_id:
        return jsonify({'status': 'error'}), 400
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE transactions SET status='failed' WHERE txn_id=? AND status='pending'", (txn_id,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/api/submit_utr', methods=['POST'])
def submit_utr():
    txn_id = request.json.get('txn_id')
    utr = request.json.get('utr')
    if not txn_id or not utr:
        return jsonify({'status': 'error', 'message': 'Missing data'}), 400
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Only update if pending
    c.execute("UPDATE transactions SET utr=? WHERE txn_id=? AND status='pending'", (utr, txn_id))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/api/verify', methods=['GET'])
def verify_api():
    api_key = request.args.get('api_key')
    txn_id = request.args.get('txn_id')

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    if api_key:
        c.execute("SELECT user_id FROM users WHERE api_key = ?", (api_key,))
        user = c.fetchone()
        if not user:
            conn.close()
            return jsonify({"status": "error", "message": "Invalid API Key"}), 401
        c.execute("SELECT status, amount, utr, paid_at FROM transactions WHERE txn_id = ? AND user_id = ?", (txn_id, user[0]))
    else:
        # Allow checking by txn_id alone for the checkout page polling
        c.execute("SELECT status, amount, utr, paid_at FROM transactions WHERE txn_id = ?", (txn_id,))
        
    row = c.fetchone()
    conn.close()

    if not row:
        return jsonify({"status": "error", "message": "Transaction not found"}), 404

    status, amount, utr, paid_at = row
    return jsonify({
        "status": status,
        "data": {
            "txn_id": txn_id,
            "amount": amount,
            "utr": utr if utr else "",
            "paid_at": paid_at if paid_at else ""
        }
    })

# ============================================
# GMAIL BACKGROUND READER & WEBHOOK DISPATCHER
# ============================================

def send_webhook(callback_url, txn_id, merchant_order_id, amount, utr):
    try:
        payload = {
            "status": "success",
            "txn_id": txn_id,
            "merchant_order_id": merchant_order_id,
            "amount": amount,
            "utr": utr
        }
        requests.post(callback_url, json=payload, timeout=5)
    except Exception as e:
        print(f"Webhook failed for {txn_id}: {e}")

def monitor_gmails():
    while True:
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT user_id, gmail, app_pass FROM users WHERE gmail IS NOT NULL AND app_pass IS NOT NULL")
            users = c.fetchall()
            conn.close()

            for user_id, gmail_user, app_pass in users:
                if not gmail_user or not app_pass: continue
                try:
                    mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
                    mail.login(gmail_user, decrypt_pass(app_pass))
                    mail.select("INBOX")

                    since_date = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
                    status, messages = mail.search(None, f'(SINCE {since_date})')

                    if status == 'OK':
                        for num in messages[0].split():
                            status, data = mail.fetch(num, '(RFC822)')
                            if status != 'OK': continue

                            msg = email.message_from_bytes(data[0][1])
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                        break
                            else:
                                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

                            text = str(msg.get("Subject", "")) + " " + body
                            amt_match = re.search(r'(?:Rs\.?|INR|₹)\s*([\d,]+\.?\d*)', text, re.IGNORECASE)
                            utr_match = re.search(r'(?:UPI\s*Ref|UTR|Txn\s*ID|RRN)\s*[:.]?\s*([A-Z0-9]{8,20})', text, re.IGNORECASE)

                            if amt_match and utr_match:
                                amount = float(amt_match.group(1).replace(',', ''))
                                utr = utr_match.group(1)

                                conn_db = sqlite3.connect(DB_FILE)
                                c_db = conn_db.cursor()
                                now_str = datetime.now().isoformat()
                                
                                # Check if user manually submitted this UTR
                                c_db.execute("SELECT txn_id, status, callback_url, merchant_order_id FROM transactions WHERE utr=?", (utr,))
                                row = c_db.fetchone()
                                txn_completed_now = False
                                
                                if row:
                                    if row[1] == 'pending':
                                        c_db.execute("UPDATE transactions SET status='completed', paid_at=? WHERE txn_id=?", (now_str, row[0]))
                                        conn_db.commit()
                                        txn_completed_now = True
                                        completed_txn = row
                                else:
                                    # Amount-based fallback (if UTR not submitted by user yet)
                                    c_db.execute("SELECT txn_id, callback_url, merchant_order_id FROM transactions WHERE user_id=? AND status='pending' AND amount=? AND (utr IS NULL OR utr='') ORDER BY created_at ASC LIMIT 1", (user_id, amount))
                                    pending_txn = c_db.fetchone()
                                    if pending_txn:
                                        c_db.execute("UPDATE transactions SET status='completed', utr=?, paid_at=? WHERE txn_id=?", (utr, now_str, pending_txn[0]))
                                        conn_db.commit()
                                        txn_completed_now = True
                                        completed_txn = (pending_txn[0], 'pending', pending_txn[1], pending_txn[2])
                                        
                                conn_db.close()
                                
                                # Fire webhook if completed now
                                if txn_completed_now and completed_txn[2]:
                                    threading.Thread(target=send_webhook, args=(completed_txn[2], completed_txn[0], completed_txn[3], amount, utr)).start()
                                    
                    mail.close()
                    mail.logout()
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(3)

@app.route('/regenerate_key', methods=['POST'])
def regenerate_key():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    import secrets
    new_api_key = 'FAM' + secrets.token_hex(16).upper()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE users SET api_key = ? WHERE user_id = ?', (new_api_key, session['user_id']))
    conn.commit()
    conn.close()
    
    flash('API Key successfully regenerated! Update your webhook integrations.')
    return redirect(url_for('dashboard'))


# ============================================
# WEBHOOK LOGS & RETRY
# ============================================

@app.route('/api/retry-webhook/<int:log_id>', methods=['POST'])
def retry_webhook(log_id):
    if 'user_id' not in session: return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT url, payload FROM webhook_logs WHERE id = ? AND user_id = ?", (log_id, session['user_id']))
    log = c.fetchone()
    
    if not log:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Log not found'}), 404
        
    url, payload = log
    import json
    status = "failed"
    try:
        res = requests.post(url, json=json.loads(payload), timeout=5)
        if res.status_code in [200, 201]:
            status = "success"
    except:
        pass
        
    c.execute("UPDATE webhook_logs SET status = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?", (status, log_id))
    conn.commit()
    conn.close()
    
    return jsonify({'status': status})

# ============================================
# SUPER ADMIN PANEL
# ============================================

@app.route('/admin-karan', methods=['GET', 'POST'])
def super_admin():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == 'karan123':  # HARDCODED ADMIN PASSWORD
            session['is_admin'] = True
            return redirect(url_for('super_admin'))
        else:
            return render_template('admin.html', error="Invalid password")
            
    if not session.get('is_admin'):
        return render_template('admin.html', login_required=True)
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Get Stats
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*), SUM(amount) FROM transactions WHERE status='completed'")
    stats = c.fetchone()
    total_txns = stats[0] or 0
    total_volume = stats[1] or 0
    
    # Get Users List
    c.execute("SELECT user_id, username, gmail, display_name, upi_id FROM users ORDER BY user_id DESC")
    users = c.fetchall()
    
    conn.close()
    
    return render_template('admin.html', total_users=total_users, total_txns=total_txns, total_volume=total_volume, users=users)

@app.route('/admin-karan/ban/<int:user_id>', methods=['POST'])
def admin_ban(user_id):
    if not session.get('is_admin'): return redirect(url_for('super_admin'))
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # To ban, we just wipe their API key and credentials so they can't login or use the gateway
    c.execute("UPDATE users SET password_hash = 'BANNED', api_key = NULL, gmail = NULL, app_pass = NULL WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('super_admin'))

def main():
    init_db()
    # Start background gmail reader
    t_gmail = threading.Thread(target=monitor_gmails, daemon=True)
    t_gmail.start()
    
    print(f"🚀 FamPay Web Gateway Running on Port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
