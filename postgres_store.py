"""Persistence and account state for the hosted VivaMente application."""
from collections import Counter
from contextlib import contextmanager
from getpass import getpass
from hashlib import scrypt, sha256
from hmac import compare_digest
from pathlib import Path
import argparse
import os
import secrets

from core import SCHEMA, Database, apply, report
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parent

def make_password(value):
    if not 12 <= len(value) <= 128:
        raise ValueError('Use uma senha de 12 a 128 caracteres.')
    salt = secrets.token_bytes(16)
    return salt.hex() + ':' + scrypt(value.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=268435456).hex()

def verify_password(value, stored):
    if not isinstance(value, str) or len(value) > 128:
        return False
    salt, digest = stored.split(':', 1)
    candidate = scrypt(value.encode(), salt=bytes.fromhex(salt), n=2**17, r=8, p=1, maxmem=268435456)
    return compare_digest(candidate, bytes.fromhex(digest))

def token_hash(token):
    return sha256(token.encode()).hexdigest()

class Postgres:
    def __init__(self, dsn):
        self.pool = ConnectionPool(dsn, min_size=1, max_size=5, max_waiting=24, timeout=5, open=False)
        self.pool.open(wait=True, timeout=15)

    def read(self, conn, collections=None):
        collections = list(collections or SCHEMA)
        state = {key: [] for key in collections}
        rows = conn.execute('SELECT collection,data FROM clinic_records WHERE collection = ANY(%s) ORDER BY id', (collections,))
        for table, row in rows:
            state[table].append(row)
        return state

    def write(self, conn, before, after):
        updated, removed = [], []
        for collection, rows in after.items():
            old = {r['id']: r for r in before[collection]}
            current = {r['id']: r for r in rows}
            removed.extend((collection, key) for key in old.keys() - current.keys())
            updated.extend((collection, key, Jsonb(row)) for key, row in current.items() if old.get(key) != row)
        with conn.cursor() as cur:
            if removed:
                cur.executemany('DELETE FROM clinic_records WHERE collection=%s AND id=%s', removed)
            if updated:
                cur.executemany('INSERT INTO clinic_records(collection,id,data) VALUES (%s,%s,%s) ON CONFLICT(collection,id) DO UPDATE SET data=excluded.data', updated)

    def audit(self, conn, actor, action, details=None):
        conn.execute('INSERT INTO audit_events(actor,action,details) VALUES (%s,%s,%s)', (actor, action, Jsonb(details or {})))

    def account(self, conn, token):
        if not token or len(token) > 128:
            return None
        return conn.execute('SELECT email,name FROM admin_account,admin_sessions WHERE token_hash=%s AND expires_at>now()', (token_hash(token),)).fetchone()

    def login(self, conn, email, password, client):
        conn.execute('DELETE FROM login_limits WHERE expires_at <= now()')
        scopes = ('address:' + token_hash(client), 'account')
        for scope, limit in zip(scopes, (6, 30)):
            attempts = conn.execute("""
                INSERT INTO login_limits VALUES (%s,1,now()+interval '15 minutes')
                ON CONFLICT(scope) DO UPDATE SET attempts=login_limits.attempts+1
                RETURNING attempts
            """, (scope,)).fetchone()[0]
            if attempts > limit:
                return 429, None
        row = conn.execute('SELECT email,password FROM admin_account FOR SHARE').fetchone()
        valid = row and verify_password(password, row[1])
        if not valid or not compare_digest(email.encode(), row[0].encode()):
            self.audit(conn, 'Anônimo', 'login_falhou')
            return 401, None
        conn.execute('DELETE FROM login_limits WHERE scope=ANY(%s)', (list(scopes),))
        conn.execute('DELETE FROM admin_sessions WHERE expires_at <= now()')
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO admin_sessions VALUES (%s, now()+interval '8 hours')", (token_hash(token),))
        self.audit(conn, row[0], 'login')
        return 200, token

    def change_password(self, conn, actor, current, replacement):
        stored = conn.execute('SELECT password FROM admin_account FOR UPDATE').fetchone()[0]
        if not verify_password(current, stored):
            raise ValueError('Senha atual inválida.')
        conn.execute('UPDATE admin_account SET password=%s', (make_password(replacement),))
        conn.execute('DELETE FROM admin_sessions')
        self.audit(conn, actor, 'senha_alterada')

    def audit_log(self, conn):
        result = {}
        for kind, where in [('acessos', 'action IN (\'login\',\'login_falhou\',\'logout\')'),
                            ('operacoes', 'action NOT IN (\'login\',\'login_falhou\',\'logout\')')]:
            rows = conn.execute('SELECT occurred_at,actor,action,details FROM audit_events WHERE ' + where + ' ORDER BY id DESC LIMIT 100')
            result[kind] = [dict(data=stamp.isoformat(), conta=actor, acao=action, detalhes=details)
                            for stamp, actor, action, details in rows]
        return result

def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('operation', choices=['init','importar'])
    cli.add_argument('--base', type=Path)
    args = cli.parse_args()
    db = Postgres(os.environ['DATABASE_URL'])
    try:
        if args.operation == 'init':
            email = input('E-mail do administrador: ').strip().lower()
            name = input('Nome do administrador: ').strip()
            if not email or '@' not in email or not name or max(len(email),len(name)) > 200:
                raise ValueError('Informe nome e e-mail válidos.')
            first = getpass('Nova senha: ')
            if first != getpass('Confirme a senha: '):
                raise ValueError('As senhas devem ser iguais.')
            password = make_password(first)
            with db.pool.connection() as conn:
                conn.execute((ROOT/'schema.sql').read_text(encoding='utf-8-sig'))
                conn.execute('INSERT INTO admin_account VALUES (true,%s,%s,%s)', (email,name,password))
            print('Administrador configurado.')
        else:
            if not args.base or not args.base.is_file():
                raise ValueError('Informe um XLSX existente com --base.')
            state = Database(args.base).read()
            for rows in state.values():
                if any(not r['id'] for r in rows) or len({r['id'] for r in rows}) != len(rows):
                    raise ValueError('A base contém IDs duplicados ou vazios.')
            original = report(state)
            with db.pool.connection() as conn:
                conn.execute('SELECT pg_advisory_xact_lock(739231)')
                before = db.read(conn)
                if any(before.values()):
                    raise ValueError('O destino deve estar vazio. Use um banco de teste para a primeira importação.')
                db.write(conn,before,state)
                imported = db.read(conn)
                if any({r['id']:r for r in imported[k]} != {r['id']:r for r in state[k]} for k in SCHEMA):
                    raise ValueError('A conferência por ID falhou.')
                totals = report(imported)
                if any(totals[k] != original[k] for k in ('total','repasse','clinica','pago','contagem')):
                    raise ValueError('A conferência dos totais falhou.')
            print('Importação validada:', {k:len(v) for k,v in imported.items()})
    finally:
        db.pool.close()

if __name__ == '__main__':
    main()
