"""Same-origin WSGI application. Only selected public assets and login are public."""
from copy import deepcopy
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import json
import logging
import mimetypes
import os
import re
import secrets

from core import apply, production_item, quote, report, shared_report
from postgres_store import Postgres, token_hash

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)
COOKIE = 'vivamentesid'
CATALOG = ('Especialidades','Medicos','Modalidades','Servicos','Tarifas','Regras')

def create_app(db, origin, secure=True):
    parsed = urlsplit(origin)
    if not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.scheme not in ('http','https'):
        raise ValueError('PUBLIC_ORIGIN deve conter apenas esquema e host.')
    if secure and parsed.scheme != 'https':
        raise ValueError('A hospedagem exige PUBLIC_ORIGIN com HTTPS.')

    def application(env, start):
        nonce = secrets.token_urlsafe(24)
        def respond(status, data, mime='application/json; charset=utf-8', headers=()):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            codes = {200:'OK',303:'See Other',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',
                     405:'Method Not Allowed',413:'Payload Too Large',429:'Too Many Requests',500:'Internal Server Error'}
            response_headers = [
                ('Content-Type',mime),('Content-Length',str(len(data))),('Cache-Control','no-store'),
                ('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),
                ('Permissions-Policy','camera=(), microphone=(), geolocation=()'),
                ('Content-Security-Policy',f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
            ]
            if secure:
                response_headers.append(('Strict-Transport-Security','max-age=31536000'))
            start(f'{status} {codes[status]}', response_headers + list(headers))
            return [data]
        def error(status, message):
            return respond(status, {'erro':message}, headers=[('Retry-After','900')] if status==429 else [])
        def html(name):
            page=(ROOT/name).read_text(encoding='utf-8-sig').replace('<script>',f'<script nonce="{nonce}">').replace('__TOKEN__','v2')
            return respond(200,page.encode(),'text/html; charset=utf-8')
        def cookie(value, age):
            return ('Set-Cookie',f'{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}'+('; Secure' if secure else ''))
        try:
            if env.get('HTTP_HOST') != parsed.netloc:
                return error(403,'Endereço inválido.')
            path = env['PATH_INFO']
            method = env['REQUEST_METHOD']
            if method not in ('GET','POST'):
                return error(405,'Método indisponível.')
            if method == 'POST' and (env.get('HTTP_ORIGIN')!=origin or env.get('HTTP_X_CLINICA')!='v2'):
                return error(403,'Origem da solicitação inválida.')
            if method == 'GET' and path.startswith('/assets/'):
                name=path.removeprefix('/assets/')
                if not re.fullmatch(r'[A-Za-z0-9_-]+\.(?:svg|png|ttf)',name):
                    return error(404,'Arquivo não encontrado.')
                asset=ROOT/'assets'/name
                if not asset.is_file():
                    return error(404,'Arquivo não encontrado.')
                return respond(200,asset.read_bytes(),mimetypes.guess_type(name)[0] or 'application/octet-stream')
            if path == '/login' and method == 'GET':
                return html('login.html')
            payload = {}
            if method == 'POST':
                size=int(env.get('CONTENT_LENGTH') or '0')
                if not 0 < size <= 100_000:
                    return error(413,'Tamanho de solicitação inválido.')
                if env.get('CONTENT_TYPE','').split(';')[0] != 'application/json':
                    return error(400,'Formato de solicitação inválido.')
                payload=json.loads(env['wsgi.input'].read(size))
                if not isinstance(payload,dict):
                    return error(400,'Formato de solicitação inválido.')
            with db.pool.connection() as conn:
                if path == '/login' and method == 'POST':
                    email,password=payload.get('email'),payload.get('senha')
                    if not isinstance(email,str) or len(email)>200 or not isinstance(password,str) or len(password)>128:
                        return error(400,'Credenciais inválidas.')
                    status, token=db.login(conn,email.strip().lower(),password,env.get('REMOTE_ADDR',''))
                    if status==200:
                        conn.commit()
                        return respond(200,{'ok':True},headers=[cookie(token,28800)])
                    return error(status,'Muitas tentativas. Tente novamente mais tarde.' if status==429 else 'Credenciais inválidas.')
                cookies=SimpleCookie(env.get('HTTP_COOKIE',''))
                token=cookies[COOKIE].value if COOKIE in cookies else ''
                account=db.account(conn,token)
                if not account:
                    if path=='/' and method=='GET':
                        return respond(303,b'','text/plain',[('Location','/login')])
                    return error(401,'Entre novamente para continuar.')
                actor,name=account
                if method=='POST':
                    if path=='/logout':
                        conn.execute('SELECT pg_advisory_xact_lock(739231)')
                        conn.execute('DELETE FROM admin_sessions WHERE token_hash=%s',(token_hash(token),))
                        db.audit(conn,actor,'logout')
                        conn.commit()
                        return respond(200,{'ok':True},headers=[cookie('',0)])
                    if path=='/senha':
                        current,replacement=payload.get('atual'),payload.get('nova')
                        if not isinstance(current,str) or not isinstance(replacement,str):
                            return error(400,'Confira os campos de senha.')
                        conn.execute('SELECT pg_advisory_xact_lock(739231)')
                        if not db.account(conn,token):
                            return error(401,'Entre novamente para continuar.')
                        db.change_password(conn,actor,current,replacement)
                        conn.commit()
                        return respond(200,{'ok':True},headers=[cookie('',0)])
                    if path!='/salvar':
                        return error(404,'Rota não encontrada.')
                    table,values=payload['tabela'],payload['dados']
                    if table not in (*CATALOG,'Pagamentos','producao','excluir_atendimento','excluir_cadastro') or not isinstance(values,dict):
                        return error(400,'Operação inválida.')
                    conn.execute('SELECT pg_advisory_xact_lock(739231)')
                    # Revalidate after the write lock if password rotation or logout has revoked the session.
                    if not db.account(conn,token):
                        return error(401,'Entre novamente para continuar.')
                    before=db.read(conn)
                    after=deepcopy(before)
                    result=apply(after,table,values)
                    db.write(conn,before,after)
                    row=next((x for x in before['Atendimentos'] if x['id']==result),{})
                    doctor_id=values.get('medico') or row.get('medico') or (result if table=='Medicos' else '')
                    doctor=next((x['nome'] for x in after['Medicos'] if x['id']==doctor_id),'')
                    action='excluir' if table in ('excluir_atendimento','excluir_cadastro') else 'editar' if values.get('id') else 'cadastrar'
                    collection=values['cadastro'] if table=='excluir_cadastro' else table
                    if table=='excluir_cadastro' and collection=='Medicos':
                        doctor=next(x['nome'] for x in before['Medicos'] if x['id']==result)
                    db.audit(conn,actor,action,dict(tabela=collection,registro=result,profissional=doctor,
                                                  data_atendimento=values.get('data') or row.get('data','')))
                    conn.commit()
                    return respond(200,{'resultado':result})
                if path=='/':
                    return html('interface.html')
                if path=='/conta':
                    return respond(200,dict(email=actor,nome=name))
                if path=='/auditoria':
                    return respond(200,db.audit_log(conn))
                if path=='/cadastros':
                    return respond(200,db.read(conn,CATALOG))
                if path not in ('/cotacao','/painel','/lancamentos','/relatorio'):
                    return error(404,'Rota não encontrada.')
                q={k:v[0] for k,v in parse_qs(env.get('QUERY_STRING',''),max_num_fields=24).items()}
                params=[q.get(k,'') for k in ('medico','inicio','fim')]
                state=db.read(conn)
                if path=='/cotacao':
                    return respond(200,quote(state,q))
                if path=='/relatorio':
                    return respond(200,shared_report(state,*params))
                if path=='/painel':
                    return respond(200,report(state,*params))
                if key:=q.get('id'):
                    item=production_item(state,key)
                    if not item:
                        return error(400,'Lançamento indisponível para edição.')
                    row=next(r for r in state['Atendimentos'] if r['id']==key)
                    return respond(200,dict(row,servico=item['servico'],unitario=item['unitario'],clinica_unitario=item['clinica_unitario']))
                return respond(200,list(reversed(report(state,*params)['linhas'])))
        except ValueError as exc:
            # Domain validation strings are intentionally authored by the application.
            if type(exc) is ValueError and not str(exc).startswith(('invalid literal','Max number')):
                return error(400,str(exc))
            return error(400,'Confira os dados enviados.')
        except (TypeError,KeyError):
            return error(400,'Confira os dados enviados.')
        except Exception:
            LOG.error('Erro interno na aplicação VivaMente.')
            return error(500,'Erro interno. Tente novamente.')
    return application

def from_env():
    return create_app(Postgres(os.environ['DATABASE_URL']),os.environ['PUBLIC_ORIGIN'])
