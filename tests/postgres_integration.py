"""Integration checks for the empty, disposable PostgreSQL service in CI."""
from io import BytesIO
from pathlib import Path
import json
import os
from urllib.parse import urlencode

from hosted import create_app
from postgres_store import Postgres, make_password

db = Postgres(os.environ['DATABASE_URL'])
origin = 'https://vivamente.test'
app = create_app(db, origin)
password = 'CI-only-password-123'

def call(path, payload=None, cookie='', host='vivamente.test', request_origin=origin, ip='192.0.2.2', query=None):
    body = json.dumps(payload).encode() if payload is not None else b''
    response = {}
    def start(status, headers):
        response.update(status=int(status.split()[0]), headers=dict(headers))
    env = {'PATH_INFO':path, 'QUERY_STRING':urlencode(query or {}), 'HTTP_HOST':host,
           'REQUEST_METHOD':'GET' if payload is None else 'POST', 'HTTP_ORIGIN':request_origin,
           'HTTP_X_CLINICA':'v2','HTTP_COOKIE':cookie,'REMOTE_ADDR':ip,
           'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(body)),'wsgi.input':BytesIO(body)}
    response['body'] = b''.join(app(env, start))
    return response

def mutate(table, status=200, **data):
    response = call('/salvar', {'tabela':table,'dados':data}, cookie)
    assert response['status'] == status, response
    return json.loads(response['body']).get('resultado')

try:
    with db.pool.connection() as conn:
        conn.execute(Path('schema.sql').read_text(encoding='utf-8-sig'))
        conn.execute('INSERT INTO admin_account VALUES (true,%s,%s,%s)',
                     ('test@example.invalid','CI',make_password(password)))
    assert call('/relatorio')['status'] == 401
    assert call('/',host='wrong.invalid')['status'] == 403
    login = call('/login',{'email':'test@example.invalid','senha':password})
    assert login['status'] == 200
    header = login['headers']['Set-Cookie']
    assert all(x in header for x in ('HttpOnly','Secure','SameSite=Strict'))
    cookie = header.split(';')[0]
    assert call('/salvar',{},cookie,request_origin='https://other.invalid')['status'] == 403
    specialty=mutate('Especialidades',nome='Especialidade')
    doctor=mutate('Medicos',nome='Profissional',especialidades=[specialty])
    unused=mutate('Medicos',nome='Profissional sem vínculos',especialidades=[specialty])
    service=mutate('Servicos',nome='Consulta',unitario='150')
    launch=mutate('producao',medico=doctor,especialidade=specialty,servico=service,
                  quantidade=4,tipo='Particular',unitario='150',clinica_unitario='50',data='2026-11-10')
    mutate('excluir_cadastro',status=400,cadastro='Medicos',id=doctor)
    mutate('excluir_cadastro',status=400,cadastro='Especialidades',id=specialty)
    mutate('excluir_cadastro',cadastro='Medicos',id=unused)
    report=json.loads(call('/relatorio',cookie=cookie,query={'medico':doctor})['body'])
    assert report['quantidade']==4 and report['repasse']==40000
    assert 'clinica' not in report and 'valor' not in report['linhas'][0]
    audit=json.loads(call('/auditoria',cookie=cookie)['body'])
    assert any(event['acao']=='login' for event in audit['acessos'])
    assert any(event['acao']=='excluir' and event['detalhes']['registro']==unused for event in audit['operacoes'])
    mutate('excluir_atendimento',id=launch)
    assert json.loads(call('/relatorio',cookie=cookie)['body'])['repasse']==0
    for i in range(7):
        result=call('/login',{'email':'test@example.invalid','senha':'incorreta'},ip='192.0.2.3')
        assert result['status']==(401 if i<6 else 429)
    assert call('/logout',{},cookie)['status']==200
    assert call('/conta',cookie=cookie)['status']==401
    print('PostgreSQL: conta, rate limit, exclusão, auditoria e relatórios validados.')
finally:
    db.pool.close()
