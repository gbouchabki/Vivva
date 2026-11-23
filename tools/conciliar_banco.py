#!/usr/bin/env python3
"""Concilia as vendas "Aguardando extrato" de index.html com o relatório do Banco Mais Todos.

Uso: python3 -I tools/conciliar_banco.py index.html transactions-....csv

Para cada venda aguardando, procura no relatório (só linhas "Aprovada") do mesmo dia, até 5 min
de diferença, uma linha ou um par (cartão + cashback/SOFTNEX) cuja soma seja o preço da venda
(ou o preço + R$ 2 / R$ 5 de desconto patrocinado). Linhas SPONSORED_* no mesmo minuto entram
como desconto pago pelo patrocinador. Valores:
  taxas = soma do MDR, líquido = soma do valor líquido,
  cashback = preço cheio - taxas - líquido (mesma regra das vendas já conciliadas).
Vendas sem par continuam aguardando se forem posteriores ao fim do relatório; se estiverem dentro
do período e não tiverem par, viram "Sem par no banco". Nunca altera vendas já conciliadas.
"""
import csv, json, re, sys
from datetime import datetime
from itertools import combinations

CUSTO = 12.90
FORMA_BANCO = {'PIX': 'Pix', 'SOFTNEX': 'Saldo de cashback'}


def num(s):
    s = (s or '').strip().replace('.', '').replace(',', '.')
    return float(s) if s else 0.0


def main(html_path, csv_path):
    h = open(html_path, encoding='utf-8').read()
    m = re.search(r'^const D = (.*);$', h, re.M)
    D = json.loads(m.group(1))
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8-sig'), delimiter=';'))
    bank = []
    for r in rows:
        if (r.get('Situação') or '').strip() != 'Aprovada':
            continue
        t = datetime.strptime(r['Data Venda'].strip(), '%d/%m/%Y %H:%M')
        bank.append(dict(id=r['ID Venda'].strip(), t=t, band=r['Bandeira'].strip(), valor=num(r['Valor Venda']),
                         mdr=num(r['Mdr']), liq=num(r['Valor líquido'])))
    fim = max(b['t'] for b in bank)
    used = {i for c in D['conciliacao'] for i in c.get('ids', [])}

    feitas, sem_par, seguem = [], [], []
    for c in D['conciliacao']:
        if c['situacao'] != 'Aguardando extrato':
            continue
        t = datetime.strptime(c['data'] + ' ' + c['hora'], '%Y-%m-%d %H:%M')
        near = lambda b: b['id'] not in used and b['t'].date() == t.date() and abs((b['t'] - t).total_seconds()) <= 300
        cand = [b for b in bank if near(b) and not b['band'].startswith('SPONSORED')]
        alvo = [c['bruto'], c['bruto'] + 2, c['bruto'] + 5]
        best = None
        for k in (1, 2):
            for g in combinations(cand, k):
                if k == 2 and len({x['t'] for x in g}) > 1:
                    continue
                s = round(sum(x['valor'] for x in g), 2)
                if any(abs(s - a) < 0.015 for a in alvo):
                    d = sum(abs((x['t'] - t).total_seconds()) for x in g) / k
                    if best is None or d < best[0]:
                        best = (d, g)
            if best:
                break
        if not best:
            (sem_par if t <= fim else seguem).append(c)
            continue
        g = list(best[1])
        tg = g[0]['t']
        g += [b for b in bank if near(b) and b['band'].startswith('SPONSORED') and abs((b['t'] - tg).total_seconds()) <= 60][:1]
        bruto = round(sum(x['valor'] for x in g if not x['band'].startswith('SPONSORED')), 2)
        taxas = round(sum(x['mdr'] for x in g), 2)
        liq = round(sum(x['liq'] for x in g), 2)
        bands = {x['band'] for x in g if not x['band'].startswith('SPONSORED')}
        if c['forma'] == 'Não informada':
            if bands == {'PIX'}:
                c['forma'] = 'Pix'
            elif bands == {'SOFTNEX'}:
                c['forma'] = 'Saldo de cashback'
            elif 'SOFTNEX' in bands:
                c['forma'] = 'Saldo de cashback + Cartão de crédito'
            else:
                c['forma'] = 'Cartão de crédito'
        c.update(bruto=bruto, taxas=taxas, liquido=liq, cashback=round(bruto - taxas - liq, 2),
                 situacao='Conciliada', ids=[x['id'] for x in g])
        used.update(c['ids'])
        feitas.append(c)
    for c in sem_par:
        c['situacao'] = 'Sem par no banco'

    # recalcula tudo a partir da lista venda a venda
    C = D['conciliacao']
    R = D['resumo']
    ok = [c for c in C if c['situacao'] == 'Conciliada']
    ag = [c for c in C if c['situacao'] == 'Aguardando extrato']
    R['pedidos'] = len(C)
    R['marmitas'] = sum(c['marmitas'] for c in C)
    R['bruto'] = round(sum(c['bruto'] for c in C), 2)
    R['ticket'] = round(R['bruto'] / R['pedidos'], 2)
    R['conciliadas'] = len(ok)
    R['marmitas_conc'] = sum(c['marmitas'] for c in ok)
    R['bruto_conc'] = round(sum(c['bruto'] for c in ok), 2)
    R['taxas'] = round(sum(c['taxas'] for c in ok), 2)
    R['cashback'] = round(sum(c['cashback'] for c in ok), 2)
    R['liquido'] = round(sum(c['liquido'] for c in ok), 2)
    R['cmv'] = round(R['marmitas_conc'] * CUSTO, 2)
    R['resultado'] = round(R['liquido'] - R['cmv'], 2)
    R['margem'] = R['resultado'] / R['bruto_conc']
    R['liquido_marmita'] = round(R['liquido'] / R['marmitas_conc'], 2)
    R['aguardando'] = len(ag)
    R['aguardando_bruto'] = round(sum(c['bruto'] for c in ag), 2)
    R['sem_par'] = sum(1 for c in C if c['situacao'] == 'Sem par no banco')

    dias = {}
    for c in C:
        r = dias.setdefault(c['data'], dict(dia=c['data'], liquido_conc=0, bruto=0, vendas=0))
        r['bruto'] = round(r['bruto'] + c['bruto'], 2)
        r['vendas'] += 1
        if c['situacao'] == 'Conciliada':
            r['liquido_conc'] = round(r['liquido_conc'] + c['liquido'], 2)
        if c['situacao'] == 'Aguardando extrato':
            r['pend'] = round(r.get('pend', 0) + c['bruto'], 2)
    D['por_dia'] = sorted(dias.values(), key=lambda r: r['dia'])
    pag = {}
    for c in C:
        q = pag.setdefault(c['forma'], dict(forma=c['forma'], bruto=0, vendas=0))
        q['bruto'] = round(q['bruto'] + c['bruto'], 2)
        q['vendas'] += 1
    D['por_pagamento'] = sorted(pag.values(), key=lambda r: -r['bruto'])
    ate_ant = D.get('ate_banco')
    D['ate_banco'] = fim.strftime('%d/%m %H:%M')

    # linhas do banco no período das vendas que não casaram com nenhuma venda da planilha (só informativo)
    ini = datetime.strptime(f"{ate_ant}/{fim.year}", '%d/%m %H:%M/%Y') if ate_ant else min(b['t'] for b in bank)
    sobras = [f"{b['t']:%d/%m %H:%M} {b['band']} R$ {b['valor']:.2f}" for b in bank
              if b['t'] > ini and b['id'] not in used and not b['band'].startswith('SPONSORED')]

    h = h.replace(m.group(0), 'const D = ' + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';')
    open(html_path, 'w', encoding='utf-8').write(h)
    print(json.dumps({
        'conciliadas_agora': [f"{c['data']} {c['hora']} {c['forma']} bruto {c['bruto']} líquido {c['liquido']}" for c in feitas],
        'sem_par': [f"{c['data']} {c['hora']} {c['forma']} {c['bruto']}" for c in sem_par],
        'seguem_aguardando': [f"{c['data']} {c['hora']} {c['bruto']}" for c in seguem],
        'banco_ate': D['ate_banco'], 'liquido': R['liquido'], 'resultado': R['resultado'],
        'margem': round(R['margem'], 4), 'conciliadas': R['conciliadas'], 'pedidos': R['pedidos'],
        'linhas_do_banco_sem_venda_na_planilha': sobras}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
