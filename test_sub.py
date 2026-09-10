from flask import Flask, session
import app
from app import get_user, DB_FILE
import sqlite3

app.init_db()

with app.app.test_request_context('/subscription'):
    app.session['user_id'] = 1
    
    # create dummy user
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (user_id, username, gmail, app_pass, api_key, upi_id) VALUES (1, 'test', 'test@test.com', '123', 'key123', 'test@upi')")
        conn.commit()
    except:
        pass
    conn.close()
    
    try:
        res = app.subscription()
        print("SUCCESS! Output length:", len(res))
    except Exception as e:
        print("ERROR:", e)
