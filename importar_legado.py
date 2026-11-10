"""Convert a legacy workbook into a new clinic database without executing VBA."""
import argparse
from datetime import date, datetime
from pathlib import Path
from openpyxl import load_workbook
from core import Database, SCHEMA, add_id, apply, cents, decimal

def import_legacy(source, destination):
    db = Database(destination)
    if db.path.exists():
        raise ValueError('Escolha um arquivo de destino novo para preservar a base existente.')
    state = {table: [] for table in SCHEMA}
    workbook = load_workbook(source, read_only=True, data_only=True)
    def label(value):
        return str(value or '').strip()
    def specialty(value):
        name = label(value).upper()
        return {'PSQUIATRA': 'PSIQUIATRA'}.get(name, name)
    def get(table, name, **fields):
        existing = next((r for r in state[table] if r['nome'].casefold() == name.casefold()), None)
        return existing['id'] if existing else apply(state, table, dict(nome=name, **fields))
    def modality(name):
        kind = {'PARTICULAR': 'Particular', 'RETORNO': 'Retorno',
                'CORTESIA': 'Cortesia', 'CORTEZIA': 'Cortesia'}.get(name.upper(), 'Convênio')
        return get('Modalidades', name if kind == 'Convênio' else kind, tipo=kind)
    try:
        tables = list(workbook['TABELAS'].iter_rows(min_row=4, max_col=20, values_only=True))
        for row in tables:
            if label(row[18]):
                modality(label(row[18]))
            if label(row[19]):
                get('Servicos', label(row[19]))
        modality('CORTESIA')
        for row in tables[1:]:
            if label(row[1]) and label(row[2]):
                get('Medicos', label(row[1]), especialidade=get('Especialidades', specialty(row[2])))
            if not label(row[5]):
                continue
            spec = get('Especialidades', specialty(row[5]))
            for name, value in zip(tables[0][6:17], row[6:17]):
                if label(name) and value is not None:
                    apply(state, 'Tarifas', dict(especialidade=spec, modalidade=modality(label(name)), unitario=value))
        for number, row in enumerate(workbook['DADOS'].iter_rows(min_row=4, max_col=10, values_only=True), 4):
            if not any(value is not None for value in row):
                continue
            name, spec, day, mode, procedure, quantity, unit, gross, clinic, share = row
            if not all(label(x) for x in (name, spec, mode, procedure)) or not isinstance(day, (date, datetime)):
                raise ValueError(f'DADOS, linha {number}, contém identificação ou data incompleta.')
            qty = decimal(quantity)
            if qty < 1 or qty != qty.to_integral_value():
                raise ValueError(f'DADOS, linha {number}, contém quantidade inválida.')
            qty = int(qty)
            unit, gross, clinic, share = map(cents, (unit, gross, clinic, share))
            if unit * qty != gross or clinic + share != gross:
                raise ValueError(f'DADOS, linha {number}, contém valores que não fecham.')
            spec_name = specialty(spec)
            doctor = get('Medicos', label(name), especialidade=get('Especialidades', spec_name))
            mode_id = modality(label(mode))
            kind = next(r['tipo'] for r in state['Modalidades'] if r['id'] == mode_id)
            service = get('Servicos', label(procedure))
            key = add_id()
            state['Atendimentos'].append(dict(id=key, medico=doctor, paciente='',
                data=day.strftime('%Y-%m-%d'), modalidade=mode_id, modalidade_nome=label(mode),
                tipo=kind, quantidade=qty, especialidade_nome=spec_name))
            state['Itens'].append(dict(id=add_id(), atendimento=key, servico=service,
                servico_nome=label(procedure), valor=gross, percentual='', fixo='', repasse=share,
                regra='', unitario=unit, clinica_unitario=clinic//qty if clinic % qty == 0 else ''))
    finally:
        workbook.close()
    db.path.parent.mkdir(parents=True, exist_ok=True)
    db.save(state)
    return {table: len(state[table]) for table in ('Medicos', 'Especialidades', 'Modalidades', 'Servicos', 'Tarifas', 'Atendimentos')}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origem', type=Path)
    parser.add_argument('destino', type=Path)
    args = parser.parse_args()
    try:
        counts = import_legacy(args.origem, args.destino)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f'Falha na importação: {error}\n')
    print(counts)
