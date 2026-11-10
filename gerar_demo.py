from pathlib import Path
from datetime import date
import json
from core import Database, apply, report

root = Path(__file__).resolve().parent
target = root / 'dados/Clinica.xlsx'
if target.exists():
    raise SystemExit('A base de demonstração já existe. Use uma pasta nova.')
target.parent.mkdir(parents=True, exist_ok=True)
db = Database(target)
state = db.read()
def add(table, **fields):
    return apply(state, table, fields)
specialties = [add('Especialidades', nome=n) for n in ('Psicologia','Psiquiatria','Neurologia')]
doctors = [add('Medicos', nome=n, especialidade=specialties[i % 3]) for i,n in enumerate(
    ('Marina Almeida','Rafael Nogueira','Bianca Castro','Lucas Moura','Helena Duarte'))]
plans = [add('Modalidades',nome=n,tipo='Convênio') for n in ('Plano Aurora','Plano Horizonte','Plano Vital','Plano Essencial')]
service = state['Servicos'][0]
add('Servicos',id=service['id'],nome='Consulta',unitario='180')
for j, spec in enumerate(specialties):
    for i, plan in enumerate(plans):
        add('Tarifas',especialidade=spec,modalidade=plan,unitario=140+12*j+9*i)
for year in (2025,2026):
    for month, volume in enumerate((48,76,57,66,59,92,53,79,44,71,51,63),1):
        for i, doctor in enumerate(doctors):
            day = date(year,month,3+i).isoformat()
            quantity = max(1, int(volume * (1-i*.14) * (.76 if year == 2025 else 1)))
            base = dict(medico=doctor,data=day,servico=service['id'])
            add('producao', **base,tipo='Convênio',modalidade=plans[(i+month)%4],quantidade=quantity//2,clinica_unitario=48)
            add('producao', **base,tipo='Particular',quantidade=quantity//5+month%3,unitario=190,clinica_unitario=72)
            add('producao', **base,tipo='Retorno',quantidade=1+quantity//22)
            if (month+i)%3==0:
                add('producao', **base,tipo='Cortesia',quantidade=1)
            add('Pagamentos',medico=doctor,data=day,valor=quantity*47,referencia='Demonstração')
db.save(state)
fixture = dict(cadastros={k:v for k,v in state.items() if k not in ('Itens','Atendimentos','Pagamentos')}, report=report(state))
(root.parent / 'v2-fixture.json').write_text(json.dumps(fixture, ensure_ascii=False),encoding='utf-8')
