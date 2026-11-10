from collections import Counter, defaultdict
from base64 import b64decode
from binascii import Error as Base64Error
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
import os
import tempfile

SCHEMA = {
    'Especialidades': 'id nome',
    'Medicos': 'id nome especialidade registro especialidades foto',
    'Modalidades': 'id nome tipo',
    'Servicos': 'id nome unitario',
    'Tarifas': 'id especialidade modalidade unitario',
    'Regras': 'id medico modalidade servico percentual fixo',
    'Atendimentos': 'id medico paciente data modalidade modalidade_nome tipo quantidade especialidade_nome especialidade',
    'Itens': 'id atendimento servico servico_nome valor percentual fixo repasse regra unitario clinica_unitario',
    'Pagamentos': 'id medico data valor referencia',
}
TIPOS = ('Particular', 'Convênio', 'Cortesia', 'Retorno')
NEW_COLUMNS = {'Medicos': 2, 'Servicos': 1, 'Atendimentos': 3, 'Itens': 2}

def add_id():
    return uuid4().hex

class Database:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if not self.path.exists():
            s = {key: [] for key in SCHEMA}
            for tipo in ('Particular', 'Cortesia', 'Retorno'):
                apply(s, 'Modalidades', {'nome': tipo, 'tipo': tipo})
            apply(s, 'Servicos', {'nome': 'Consulta'})
            self.save(s)
            return s
        book = load_workbook(self.path, read_only=True, data_only=True)
        try:
            result = {}
            for table, cols in SCHEMA.items():
                if table == 'Tarifas' and table not in book:
                    result[table] = []
                    continue
                rows = book[table].values
                columns = tuple(cols.split())
                header = next(rows)
                extra = NEW_COLUMNS.get(table, 0)
                if not len(columns) - extra <= len(header) <= len(columns) or header != columns[:len(header)]:
                    raise ValueError(f'Cabeçalho alterado na aba {table}.')
                result[table] = [
                    {col: row[i] if i < len(header) and row[i] is not None else ''
                     for i, col in enumerate(columns)}
                    for row in rows if row[0]
                ]
            return result
        finally:
            book.close()

    def save(self, state):
        book = Workbook()
        book.remove(book.active)
        for table, schema in SCHEMA.items():
            sheet = book.create_sheet(table)
            cols = schema.split()
            sheet.append(cols)
            sheet.freeze_panes = 'B2'
            for cell in sheet[1]:
                cell.font = Font(bold=True, color='FFFFFF')
                cell.fill = PatternFill('solid', fgColor='17453F')
            for row in state[table]:
                sheet.append([row.get(c, '') for c in cols])
                for cell in sheet[sheet.max_row]:
                    if isinstance(cell.value, str):
                        cell.data_type = 's'
            sheet.auto_filter.ref = sheet.dimensions
        with tempfile.NamedTemporaryFile(dir=self.path.parent, suffix='.xlsx', delete=False) as f:
            temporary = Path(f.name)
        try:
            book.save(temporary)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

def clean(value, label, required=True):
    value = str(value or '').strip()
    if (required and not value) or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise ValueError(f'Confira o campo {label}.')
    return value

def decimal(value):
    try:
        result = Decimal(str(value).strip().replace(',', '.'))
        if result.is_finite() and 0 <= result <= 1_000_000_000:
            return result
    except InvalidOperation:
        pass
    raise ValueError('Informe um valor numérico válido e maior ou igual a zero.')

def rounded(value):
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def cents(value):
    return rounded(decimal(value) * 100)

def day(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError('Informe uma data válida.') from None

def index(state, table):
    return {row['id']: row for row in state[table]}

def ref(state, table, key, optional=False):
    if optional and not key:
        return ''
    if key not in index(state, table):
        raise ValueError(f'Selecione um cadastro válido em {table}.')
    return key

def doctor_specialties(doctor):
    return (doctor.get('especialidades') or doctor['especialidade']).split(',')

def photo_data(value):
    if not value:
        return ''
    prefix = 'data:image/jpeg;base64,'
    if not isinstance(value, str) or len(value) > 30000 or not value.startswith(prefix):
        raise ValueError('Escolha uma foto JPEG menor.')
    try:
        photo = b64decode(value[len(prefix):], validate=True)
    except (ValueError, Base64Error):
        raise ValueError('Foto inválida.') from None
    if not photo.startswith(b'\xff\xd8\xff') or not photo.endswith(b'\xff\xd9'):
        raise ValueError('Foto inválida.')
    return value

def apply(s, table, p):
    if table == 'excluir_cadastro':
        return delete_catalog_entry(s, p.get('cadastro'), p.get('id'))
    if table == 'producao':
        return production(s, p)
    if table == 'excluir_atendimento':
        key = ref(s, 'Atendimentos', p.get('id'))
        s['Atendimentos'] = [row for row in s['Atendimentos'] if row['id'] != key]
        s['Itens'] = [row for row in s['Itens'] if row['atendimento'] != key]
        return key
    if table == 'lote':
        return batch(s, p)
    if table not in ('Especialidades', 'Medicos', 'Modalidades', 'Servicos', 'Tarifas', 'Regras', 'Pagamentos'):
        raise ValueError('Operação desconhecida.')
    key = p.get('id') or add_id()
    if p.get('id'):
        ref(s, table, key)
    row = {'id': key}
    if table in ('Especialidades', 'Medicos', 'Modalidades', 'Servicos'):
        row['nome'] = clean(p.get('nome'), 'nome')
    if table == 'Servicos':
        row['unitario'] = cents(p['unitario']) if p.get('unitario') not in ('', None) else ''
    if table == 'Medicos':
        assigned = p.get('especialidades', [p.get('especialidade')])
        if not isinstance(assigned, list) or not assigned or len(assigned) > 30:
            raise ValueError('Selecione de uma a 30 especialidades.')
        assigned = list(dict.fromkeys(ref(s, 'Especialidades', item) for item in assigned))
        row['especialidade'] = assigned[0]
        row['especialidades'] = ','.join(assigned)
        row['foto'] = photo_data(p.get('foto', index(s, 'Medicos').get(key, {}).get('foto')))
        row['registro'] = clean(p.get('registro'), 'CRM', False)
    if table == 'Modalidades':
        if p.get('tipo') not in TIPOS:
            raise ValueError('Escolha um tipo de modalidade.')
        row['tipo'] = p['tipo']
    if table == 'Tarifas':
        row.update(especialidade=ref(s, 'Especialidades', p.get('especialidade')),
                   modalidade=ref(s, 'Modalidades', p.get('modalidade')),
                   unitario=cents(p.get('unitario')))
        if any(r['id'] != key and r['especialidade'] == row['especialidade'] and
               r['modalidade'] == row['modalidade'] for r in s[table]):
            raise ValueError('Já existe uma tarifa para essa especialidade e modalidade.')
    if table == 'Regras':
        fields = {'medico': 'Medicos', 'modalidade': 'Modalidades', 'servico': 'Servicos'}
        row.update({f: ref(s, t, p.get(f), True) for f, t in fields.items()})
        if any(r['id'] != key and all(r[f] == row[f] for f in fields) for r in s[table]):
            raise ValueError('Já existe uma regra para essa combinação. Edite a regra existente.')
        row['percentual'] = str(decimal(p.get('percentual') or '0').normalize())
        row['fixo'] = cents(p.get('fixo') or '0')
    if table == 'Pagamentos':
        row.update(medico=ref(s, 'Medicos', p.get('medico')), data=day(p.get('data')),
                   valor=cents(p.get('valor')), referencia=clean(p.get('referencia'), 'referência', False))
        if row['valor'] <= 0:
            raise ValueError('O pagamento deve ser maior que zero.')
    s[table] = [r for r in s[table] if r['id'] != key] + [row]
    return key

def delete_catalog_entry(s, table, key):
    references = {
        'Medicos': [('Atendimentos', 'medico'), ('Pagamentos', 'medico'), ('Regras', 'medico')],
        'Especialidades': [('Tarifas', 'especialidade'), ('Atendimentos', 'especialidade')],
        'Modalidades': [('Atendimentos', 'modalidade'), ('Tarifas', 'modalidade'), ('Regras', 'modalidade')],
        'Servicos': [('Itens', 'servico'), ('Regras', 'servico')],
        'Tarifas': [],
    }
    if table not in references:
        raise ValueError('Tipo de cadastro inválido para exclusão.')
    ref(s, table, key)
    linked = any(row.get(field) == key for collection, field in references[table] for row in s[collection])
    if table == 'Especialidades':
        linked = linked or any(key in doctor_specialties(doctor) for doctor in s['Medicos'])
    if linked:
        raise ValueError('Este cadastro está vinculado a outros registros e não pode ser excluído.')
    s[table] = [row for row in s[table] if row['id'] != key]
    return key

def editable_item(previous, items):
    if previous and not previous['paciente'] and len(items) == 1 and items[0]['unitario'] != '':
        return items[0]
    return None

def production_item(s, key):
    return editable_item(index(s, 'Atendimentos').get(key),
                         [item for item in s['Itens'] if item['atendimento'] == key])

def quote(s, p):
    medico = index(s, 'Medicos')[ref(s, 'Medicos', p.get('medico'))]
    specialty_key = p.get('especialidade') or medico['especialidade']
    if specialty_key not in doctor_specialties(medico):
        raise ValueError('Selecione uma especialidade vinculada ao profissional.')
    especialidade = index(s, 'Especialidades')[specialty_key]
    servico = index(s, 'Servicos')[ref(s, 'Servicos', p.get('servico'))]
    tipo = p.get('tipo')
    if tipo not in TIPOS:
        raise ValueError('Selecione o tipo de atendimento.')
    modalidade = None
    if tipo == 'Convênio':
        modalidade = index(s, 'Modalidades')[ref(s, 'Modalidades', p.get('modalidade'))]
        if modalidade['tipo'] != tipo:
            raise ValueError('Selecione um convênio cadastrado.')
    else:
        modalidade = next((r for r in s['Modalidades'] if r['tipo'] == tipo), None)
    tariff = next((r for r in s['Tarifas'] if modalidade and
                   r['especialidade'] == especialidade['id'] and r['modalidade'] == modalidade['id']), None)
    previous_item = production_item(s, p['id']) if p.get('id') else None
    previous = index(s, 'Atendimentos').get(p.get('id'))
    same_insured = (previous_item and tipo == 'Convênio' and previous['tipo'] == tipo
                    and previous['medico'] == medico['id'] and previous['modalidade'] == modalidade['id']
                    and previous_item['servico'] == servico['id']
                    and (previous.get('especialidade') or medico['especialidade']) == specialty_key)
    if same_insured:
        unitario = previous_item['unitario']
    elif tipo in ('Retorno', 'Cortesia'):
        unitario = 0
    elif tipo == 'Particular':
        unitario = cents(p['unitario']) if p.get('unitario') not in ('', None) else None
    elif tariff:
        unitario = tariff['unitario']
    elif s['Tarifas']:
        raise ValueError('Cadastre a tarifa para esta especialidade e modalidade.')
    else:
        unitario = servico['unitario']
    if unitario == '':
        raise ValueError('Cadastre a tarifa ou o valor unitário do procedimento.')
    return dict(medico=medico['id'], especialidade=specialty_key, especialidade_nome=especialidade['nome'], servico=servico['id'],
                servico_nome=servico['nome'], tipo=tipo, modalidade=modalidade['id'] if modalidade else '',
                modalidade_nome=modalidade['nome'] if modalidade else tipo, unitario=unitario)

def production(s, p):
    quoted = quote(s, p)
    quantidade = decimal(p.get('quantidade'))
    if quantidade != quantidade.to_integral_value() or not 1 <= quantidade <= 1000000:
        raise ValueError('A quantidade deve ser um número inteiro de 1 a 1.000.000.')
    quantidade = int(quantidade)
    unitario = quoted['unitario']
    if unitario is None:
        raise ValueError('Informe o valor unitário do atendimento particular.')
    clinica = 0 if quoted['tipo'] in ('Retorno', 'Cortesia') else cents(p.get('clinica_unitario'))
    if clinica > unitario:
        raise ValueError('O valor unitário da clínica deve ser menor ou igual ao preço unitário.')
    data = day(p.get('data'))
    key = p.get('id') or add_id()
    previous_item = production_item(s, key) if p.get('id') else None
    if p.get('id') and not previous_item:
        raise ValueError('Selecione um lançamento de produção válido para alterar.')
    item_key = previous_item['id'] if previous_item else add_id()
    attendance = dict(id=key, paciente='', data=data, quantidade=quantidade,
                                  **{field: quoted[field] for field in ('medico', 'modalidade', 'modalidade_nome',
                                                                         'tipo', 'especialidade_nome', 'especialidade')})
    item = dict(id=item_key, atendimento=key, servico=quoted['servico'],
                          servico_nome=quoted['servico_nome'], valor=quantidade * unitario,
                          percentual='', fixo='', repasse=quantidade * (unitario - clinica),
                          regra='', unitario=unitario, clinica_unitario=clinica)
    s['Atendimentos'] = [row for row in s['Atendimentos'] if row['id'] != key] + [attendance]
    s['Itens'] = [row for row in s['Itens'] if row['id'] != item_key] + [item]
    return key

def batch(s, p):
    medico = ref(s, 'Medicos', p.get('medico'))
    rows, items = [], []
    if not p.get('linhas'):
        raise ValueError('Inclua pelo menos um atendimento.')
    for entry in p['linhas']:
        modalidade = index(s, 'Modalidades').get(entry.get('modalidade'))
        if not modalidade:
            raise ValueError('Selecione a modalidade de cada atendimento.')
        atendimento = add_id()
        rows.append(dict(id=atendimento, medico=medico, paciente=clean(entry.get('paciente'), 'paciente'),
                         data=day(entry.get('data')), modalidade=modalidade['id'],
                         modalidade_nome=modalidade['nome'], tipo=modalidade['tipo'], quantidade=1,
                         especialidade_nome=''))
        if not entry.get('itens'):
            raise ValueError('Inclua um serviço em cada atendimento.')
        for part in entry['itens']:
            servico = index(s, 'Servicos').get(part.get('servico'))
            if not servico:
                raise ValueError('Selecione o serviço de cada item.')
            scope = dict(medico=medico, modalidade=modalidade['id'], servico=servico['id'])
            candidates = [r for r in s['Regras'] if all(not r[k] or r[k] == v for k, v in scope.items())]
            rule = max(candidates, key=lambda r: (bool(r['medico']), bool(r['servico']), bool(r['modalidade'])), default={})
            value = cents(part.get('valor'))
            percent = rule.get('percentual', '0')
            fixed = rule.get('fixo', 0)
            if part.get('percentual') not in (None, ''):
                percent = str(decimal(part['percentual']).normalize())
            if part.get('fixo') not in (None, ''):
                fixed = cents(part['fixo'])
            items.append(dict(id=add_id(), atendimento=atendimento, servico=servico['id'],
                              servico_nome=servico['nome'], valor=value, percentual=percent, fixo=fixed,
                              repasse=rounded(Decimal(value) * Decimal(percent) / 100) + fixed,
                              regra=rule.get('id', ''), unitario='', clinica_unitario=''))
    s['Atendimentos'].extend(rows)
    s['Itens'].extend(items)
    return [row['id'] for row in rows]

def report(s, medico='', inicio='', fim=''):
    if inicio:
        inicio = day(inicio)
    if fim:
        fim = day(fim)
    if inicio and fim and inicio > fim:
        raise ValueError('A data inicial deve ser anterior ou igual à final.')
    def inside(row):
        return ((not medico or row['medico'] == medico) and
                (not inicio or row['data'] >= inicio) and (not fim or row['data'] <= fim))
    items = defaultdict(list)
    for item in s['Itens']:
        items[item['atendimento']].append(item)
    lines, ranking, trend = [], Counter(), defaultdict(lambda: [0, 0, 0])
    count = Counter({t: 0 for t in TIPOS})
    total = repasse = 0
    for row in sorted(s['Atendimentos'], key=lambda r: r['data']):
        if not inside(row):
            continue
        parts = items[row['id']]
        value = sum(p['valor'] for p in parts)
        share = sum(p['repasse'] for p in parts)
        total += value
        repasse += share
        quantity = row.get('quantidade') or 1
        count[row['tipo']] += quantity
        ranking[row['medico']] += quantity
        period = trend[row['data'][:7]]
        period[0] += value
        period[1] += share
        period[2] += value - share
        lines.append(dict(row, quantidade=quantity, valor=value, repasse=share, clinica=value-share,
                          editavel=bool(editable_item(row, parts)),
                          servicos=', '.join(p['servico_nome'] for p in parts)))
    payments = [p for p in s['Pagamentos'] if inside(p)]
    return dict(linhas=lines, total=total, repasse=repasse, clinica=total-repasse,
                pago=sum(p['valor'] for p in payments), pagamentos=payments,
                contagem=dict(count), ranking=ranking.most_common(), evolucao=dict(trend),
                media=rounded(Decimal(total) / sum(count.values())) if lines else 0)

def shared_report(s, medico='', inicio='', fim=''):
    if medico:
        ref(s, 'Medicos', medico)
    r = report(s, medico, inicio, fim)
    doctors = index(s, 'Medicos')
    columns = ('data', 'paciente', 'modalidade_nome', 'tipo', 'servicos', 'quantidade', 'repasse')
    lines, summaries = [], {}
    for row in r['linhas']:
        key = row['medico']
        name = doctors.get(key, {}).get('nome', 'Profissional sem cadastro')
        lines.append(dict({col: row[col] for col in columns}, medico=name))
        summary = summaries.setdefault(key, dict(medico=name, quantidade=0, repasse=0,
                                                 contagem=dict.fromkeys(TIPOS, 0)))
        summary['quantidade'] += row['quantidade']
        summary['repasse'] += row['repasse']
        summary['contagem'][row['tipo']] += row['quantidade']
    return dict(medico=doctors[medico]['nome'] if medico else '',
                quantidade=sum(r['contagem'].values()), repasse=r['repasse'],
                contagem=r['contagem'], linhas=lines,
                profissionais=sorted(summaries.values(), key=lambda row: row['medico'].casefold()))
