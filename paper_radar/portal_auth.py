# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Authentication and account administration for the separate public portal.

Password hashes use hashlib.scrypt with unique salts. Only the SHA-256 of the
opaque session bearer is stored. Public requests never touch local settings,
keyring, Zotero or desktop-only HTTP endpoints.
"""
from __future__ import annotations
from contextlib import contextmanager
from hashlib import scrypt, sha256
import hmac
import re
import secrets
import sqlite3
import time
from pathlib import Path
from .migrations import migrate

USERNAME = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{2,31}$')
SESSION_SECONDS=7*86400
IDLE_SECONDS=12*3600


class AuthError(ValueError):pass


def now():return int(time.time())


def valid_password(value: str):
    if not isinstance(value,str) or not 12<=len(value)<=128 or len(value.encode('utf-8'))>256:
        raise AuthError('密码长度必须为 12—128 个字符。')
    return value


def password_hash(password: str, salt: bytes) -> str:
    return scrypt(password.encode('utf-8'),salt=salt,n=2**14,r=8,p=1,dklen=32).hex()


@contextmanager
def db(path: Path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    conn=sqlite3.connect(path,timeout=15)
    conn.row_factory=sqlite3.Row
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        migrate(conn,'portal_auth')
        yield conn
        conn.commit()
    finally:conn.close()


def create_account(conn,username: str,password: str,*,role: str='user') -> dict:
    if not isinstance(username,str) or not USERNAME.fullmatch(username):
        raise AuthError('用户名须为 3—32 位，英文字母开头，仅包含字母、数字、下划线和连字符。')
    valid_password(password)
    if role not in ('user','admin'):raise AuthError('Invalid role')
    if conn.execute('SELECT 1 FROM accounts WHERE username=? COLLATE NOCASE',(username,)).fetchone():
        raise AuthError('用户名已被使用。')
    uid=secrets.token_hex(16)
    salt=secrets.token_bytes(16)
    try:
        conn.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?)',
            (uid,username,salt.hex(),password_hash(password,salt),role,1,
             time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
    except sqlite3.IntegrityError as exc:raise AuthError('用户名已被使用。') from exc
    return {'id':uid,'username':username,'role':role}


def check_login(conn,username:str,password:str) -> dict|None:
    if not isinstance(username,str) or not USERNAME.fullmatch(username):return None
    if not isinstance(password,str) or len(password)>128:return None
    row=conn.execute('SELECT * FROM accounts WHERE username=? COLLATE NOCASE',(username,)).fetchone()
    if not row or not row['is_active']:
        # Constant work for absent users prevents cheap account enumeration.
        password_hash(password,secrets.token_bytes(16))
        return None
    expected=password_hash(password,bytes.fromhex(row['salt']))
    if not hmac.compare_digest(expected,row['password_hash']):return None
    return {'id':row['id'],'username':row['username'],'role':row['role']}


def create_session(conn,account_id:str) -> tuple[str,str]:
    raw=secrets.token_urlsafe(32)
    csrf=secrets.token_urlsafe(24)
    t=now()
    conn.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',
        (sha256(raw.encode()).hexdigest(),account_id,csrf,t,t+SESSION_SECONDS,t))
    return raw,csrf


def authenticate(conn,token:str) -> dict|None:
    if not isinstance(token,str) or not 30<=len(token)<=128:return None
    digest=sha256(token.encode()).hexdigest()
    row=conn.execute('''SELECT a.id,a.username,a.role,a.is_active,
        s.csrf,s.expires_at,s.last_seen FROM sessions s
        JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=?''',(digest,)).fetchone()
    t=now()
    if not row or not row['is_active'] or row['expires_at']<=t or row['last_seen']+IDLE_SECONDS<=t:
        conn.execute('DELETE FROM sessions WHERE token_hash=?',(digest,))
        return None
    conn.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?',(t,digest))
    return {k:row[k] for k in ('id','username','role','csrf')}


def delete_session(conn,token:str):
    conn.execute('DELETE FROM sessions WHERE token_hash=?',(sha256(token.encode()).hexdigest(),))


def throttle(conn,key:str,*,limit:int,seconds:int) -> bool:
    t=now()
    conn.execute('DELETE FROM auth_attempts WHERE occurred_at<?',(t-7*86400,))
    count=conn.execute('SELECT COUNT(*) FROM auth_attempts WHERE identifier=? AND occurred_at>=?',
        (key,t-seconds)).fetchone()[0]
    if count>=limit:return False
    conn.execute('INSERT INTO auth_attempts(identifier,occurred_at) VALUES (?,?)',(key,t))
    return True


def set_active(conn,actor_id:str,user_id:str,active:bool):
    row=conn.execute('SELECT role,is_active FROM accounts WHERE id=?',(user_id,)).fetchone()
    if not row:raise AuthError('用户不存在。')
    if user_id==actor_id:raise AuthError('不能修改当前管理员自身的状态。')
    if row['role']=='admin' and not active:
        count=conn.execute("SELECT COUNT(*) FROM accounts WHERE role='admin' AND is_active=1").fetchone()[0]
        if count<=1:raise AuthError('至少保留一个可用管理员。')
    conn.execute('UPDATE accounts SET is_active=? WHERE id=?',(int(active),user_id))
    if not active:conn.execute('DELETE FROM sessions WHERE account_id=?',(user_id,))


def change_password(conn,account_id:str,old_password:str,new_password:str):
    valid_password(new_password)
    row=conn.execute('SELECT username FROM accounts WHERE id=?',(account_id,)).fetchone()
    if not row or not check_login(conn,row['username'],old_password):
        raise AuthError('当前密码不正确。')
    salt=secrets.token_bytes(16)
    conn.execute('UPDATE accounts SET salt=?,password_hash=? WHERE id=?',
                 (salt.hex(),password_hash(new_password,salt),account_id))
    conn.execute('DELETE FROM sessions WHERE account_id=?',(account_id,))
