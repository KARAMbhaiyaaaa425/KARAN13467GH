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

import qrcode
from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for

# ============================================
# SERVER CONFIGURATION
# ============================================
DB_FILE = "fampay_gateway.db"
PORT = int(os.environ.get("PORT", 5000))

app = Flask(__name__)

# Personal Gateway - Only One Admin User
ADMIN_USER_ID = 1

# ============================================
# DATABASE INITIALIZATION
# ============================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            upi_id TEXT,
            gmail TEXT,
            app_pass TEXT,
            api_key TEXT UNIQUE,
            created_at DATETIME,
            display_name TEXT DEFAULT 'Merchant',
            theme TEXT DEFAULT 'default'
        )
    ''')
    try:
        c.execute("ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT 'Merchant'")
        c.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'default'")
    except:
        pass
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            txn_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            utr TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME,
            expires_at DATETIME,
            paid_at DATETIME
        )
    ''')
    
    conn.commit()
    conn.close()

def get_admin_user():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT upi_id, gmail, app_pass, api_key, display_name, theme FROM users WHERE user_id = ?", (ADMIN_USER_ID,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"upi_id": row[0], "gmail": row[1], "app_pass": row[2], "api_key": row[3], "display_name": row[4] or 'Merchant', "theme": row[5] or 'default'}
    return None

def save_admin_user(upi_id, gmail, app_pass):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT api_key FROM users WHERE user_id = ?", (ADMIN_USER_ID,))
    row = c.fetchone()
    if row:
        api_key = row[0]
        c.execute('''UPDATE users SET upi_id=?, gmail=?, app_pass=? WHERE user_id=?''', 
                  (upi_id, gmail, app_pass, ADMIN_USER_ID))
    else:
        api_key = "FAM_" + uuid.uuid4().hex + uuid.uuid4().hex[:12]
        c.execute('''INSERT INTO users (user_id, upi_id, gmail, app_pass, api_key, created_at)
                     VALUES (?, ?, ?, ?, ?, ?)''', 
                  (ADMIN_USER_ID, upi_id, gmail, app_pass, api_key, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return api_key

# ============================================
# FLASK WEB INTERFACE (DASHBOARD)
# ============================================

@app.route('/')
def dashboard():
    error = request.args.get('error')
    success = request.args.get('success')
    user_info = get_admin_user()
    
    # Get stats
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(amount) FROM transactions WHERE user_id=? AND status='completed'", (ADMIN_USER_ID,))
    total_count, total_amount = c.fetchone()
    
    # Recent 10 transactions
    c.execute("SELECT txn_id, amount, utr, paid_at FROM transactions WHERE user_id=? AND status='completed' ORDER BY paid_at DESC LIMIT 10", (ADMIN_USER_ID,))
    txns = c.fetchall()
    conn.close()
    
    return render_template('dashboard.html', 
                           user_info=user_info, 
                           total_count=total_count or 0, 
                           total_amount=f"{total_amount or 0:.2f}",
                           txns=txns,
                           error=error,
                           success=success)

@app.route('/save_account', methods=['POST'])
def save_account():
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
            
        save_admin_user(upi_id, gmail, app_pass)
        return redirect(url_for('dashboard', success='Account Connected & Verified Perfectly!'))
        
    return redirect(url_for('dashboard'))


@app.route('/save_customize', methods=['POST'])
def save_customize():
    display_name = request.form.get('display_name', 'Merchant')
    theme = request.form.get('theme', 'default')
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET display_name=?, theme=? WHERE user_id=?", (display_name, theme, ADMIN_USER_ID))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard', success='Customization Saved!'))

@app.route('/generate_link', methods=['POST'])
def generate_link():
    amount = request.form.get('amount')
    user_info = get_admin_user()
    if user_info and amount:
        return redirect(f"/pay?api_key={user_info['api_key']}&amount={amount}")
    return redirect(url_for('dashboard'))

# ============================================
# PAYMENT GATEWAY API & CHECKOUT
# ============================================

@app.route('/pay', methods=['GET'])
def checkout_page():
    api_key = request.args.get('api_key')
    amount_raw = request.args.get('amount')

    if not api_key or not amount_raw:
        return "<h1>Error: Missing api_key or amount</h1>", 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, upi_id, display_name, theme FROM users WHERE api_key = ?", (api_key,))
    user = c.fetchone()
    
    if not user:
        return "<h1>Error: Invalid API Key</h1>", 401
    
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
                           theme=theme or 'default')

@app.route('/qr/<txn_id>')
def serve_qr(txn_id):
    qr_path = f"static/qr_{txn_id}.png"
    if os.path.exists(qr_path):
        return send_file(qr_path, mimetype='image/png')
    return jsonify({"error": "QR code not found"}), 404


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
    c.execute("SELECT user_id FROM users WHERE api_key = ?", (api_key,))
    user = c.fetchone()
    
    if not user:
        return jsonify({"status": "error", "message": "Invalid API Key"}), 401

    c.execute("SELECT status, amount, utr, paid_at FROM transactions WHERE txn_id = ? AND user_id = ?", (txn_id, user[0]))
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
# GMAIL BACKGROUND READER
# ============================================
def monitor_gmails():
    while True:
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT user_id, gmail, app_pass FROM users")
            users = c.fetchall()
            conn.close()

            for user_id, gmail_user, app_pass in users:
                if not gmail_user or not app_pass: continue
                try:
                    mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
                    mail.login(gmail_user, app_pass)
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
                                c_db.execute("SELECT txn_id, status FROM transactions WHERE utr=?", (utr,))
                                row = c_db.fetchone()
                                if row:
                                    if row[1] == 'pending':
                                        c_db.execute("UPDATE transactions SET status='completed', paid_at=? WHERE txn_id=?", (now_str, row[0]))
                                        conn_db.commit()
                                else:
                                    # Amount-based fallback (if UTR not submitted by user yet)
                                    c_db.execute("SELECT txn_id FROM transactions WHERE user_id=? AND status='pending' AND amount=? AND (utr IS NULL OR utr='') ORDER BY created_at ASC LIMIT 1", (user_id, amount))
                                    pending_txn = c_db.fetchone()
                                    if pending_txn:
                                        c_db.execute("UPDATE transactions SET status='completed', utr=?, paid_at=? WHERE txn_id=?", (utr, now_str, pending_txn[0]))
                                        conn_db.commit()
                                        
                                conn_db.close()
                    mail.close()
                    mail.logout()
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(10)

def main():
    init_db()
    # Start background gmail reader
    t_gmail = threading.Thread(target=monitor_gmails, daemon=True)
    t_gmail.start()
    
    print(f"🚀 FamPay Web Gateway Running on Port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
