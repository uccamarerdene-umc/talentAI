import sqlite3, json, time, os

DB_PATH = os.environ.get("SQLITE_DB_PATH", "./centraltest.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at REAL NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id, created_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS excel_sessions (
        session_id TEXT PRIMARY KEY,
        filename TEXT,
        rows INTEGER,
        columns_json TEXT,
        summary TEXT,
        test_name TEXT,
        raw_data_json TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL)""")
    conn.commit()
    conn.close()

def save_message(session_id, role, content):
    conn = get_conn()
    conn.execute("INSERT INTO chat_history (session_id, role, content, created_at) VALUES (?,?,?,?)",
        (session_id, role, content, time.time()))
    conn.commit()
    conn.close()

def get_messages(session_id, limit=30):
    conn = get_conn()
    rows = conn.execute("SELECT role, content, created_at FROM chat_history WHERE session_id=? ORDER BY created_at ASC LIMIT ?",
        (session_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_sessions():
    conn = get_conn()
    rows = conn.execute("""SELECT session_id, MIN(content) as first_msg,
        MAX(created_at) as last_active, COUNT(*) as msg_count
        FROM chat_history WHERE role='user'
        GROUP BY session_id ORDER BY last_active DESC LIMIT 50""").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_session(session_id):
    conn = get_conn()
    conn.execute("DELETE FROM chat_history WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM excel_sessions WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()

def save_excel_session(session_id, filename, rows, columns, summary, raw_data):
    now = time.time()
    conn = get_conn()
    conn.execute("""INSERT INTO excel_sessions
        (session_id, filename, rows, columns_json, summary, raw_data_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(session_id) DO UPDATE SET
        filename=excluded.filename, rows=excluded.rows,
        columns_json=excluded.columns_json, summary=excluded.summary,
        raw_data_json=excluded.raw_data_json, updated_at=excluded.updated_at""",
        (session_id, filename, rows, json.dumps(columns, ensure_ascii=False),
         summary, json.dumps(raw_data, ensure_ascii=False), now, now))
    conn.commit()
    conn.close()

def get_excel_session(session_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM excel_sessions WHERE session_id=?", (session_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["columns"] = json.loads(d["columns_json"])
    d["raw_data"] = json.loads(d["raw_data_json"]) if d["raw_data_json"] else []
    return d

import uuid

def create_session():
    """Шинэ session үүсгэж ID буцаана."""
    sid = str(uuid.uuid4())
    now = time.time()
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO excel_sessions
            (session_id, filename, rows, columns_json, summary, test_name, raw_data_json, created_at, updated_at)
            VALUES (?, '', 0, '[]', '', '', '[]', ?, ?)""", (sid, now, now)
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
    return sid

def get_session(session_id):
    """Session мэдээллийг буцаана."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM excel_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row
    except Exception:
        return None
    finally:
        conn.close()

def update_session_file(session_id, filename, summary, test_name, rows, columns, raw_data=None):
    """Session-д Excel файлын мэдээллийг шинэчилнэ."""
    now = time.time()
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO excel_sessions
            (session_id, filename, rows, columns_json, summary, test_name, raw_data_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                filename=excluded.filename,
                rows=excluded.rows,
                columns_json=excluded.columns_json,
                summary=excluded.summary,
                test_name=excluded.test_name,
                raw_data_json=excluded.raw_data_json,
                updated_at=excluded.updated_at
        """, (
            session_id, filename, rows,
            json.dumps(columns, ensure_ascii=False),
            summary, test_name,
            json.dumps(raw_data or [], ensure_ascii=False),
            now, now
        ))
        conn.commit()
    except Exception as e:
        print(f"update_session_file error: {e}")
    finally:
        conn.close()
