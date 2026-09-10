from flask import Flask, session
import app
from app import get_user, DB_FILE
import sqlite3

app.init_db()

with app.app.test_request_context('/dashboard'):
    app.session['user_id'] = 1
    
    try:
        res = app.dashboard()
        print("SUCCESS! Output length:", len(res))
    except Exception as e:
        print("ERROR:", e)
