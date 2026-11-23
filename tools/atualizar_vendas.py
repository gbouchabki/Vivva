#!/usr/bin/env python3
"""Atualiza index.html com as vendas novas da planilha "Vendas Vivvá - Alimentação de Verdade".

Uso: python3 -I tools/atualizar_vendas.py index.html vendas.json
  vendas.json = lista de linhas da aba Vendas (com cabeçalho), como devolvida pelo Google Sheets.

Vendas que ainda não estão na página (chave data + HH:MM) entram como "Aguardando extrato":
contam em vendas, marmitas e preço cheio, mas não no líquido/resultado, que só muda com a
conciliação do relatório do Banco Mais Todos. Nunca altera vendas já conciliadas.
Imprime um resumo em JSON; "novas": 0 significa que nada foi alterado.
"""
import json, re, sys

PRECO = 17.90
FORMAS = {
    'pix': 'Pix',
    'cartão de crédito': 'Cartão de crédito',
    'saldo + cartão': 'Saldo de cashback + Cartão de crédito',
    'saldo de cashback': 'Saldo de cashback',
}


def brl(s):
    s = (s or '').replace('R$', '').strip().replace('.', '').replace(',', '.')
    return float(s) if s else 0.0


def main(html_path, json_path):
    h = open(html_path, encoding='utf-8').read()
    m = re.search(r'^const D = (.*);$', h, re.M)
    D = json.loads(m.group(1))
    rows = json.load(open(json_path, encoding='utf-8'))
    if isinstance(rows, dict):
        rows = rows.get('values', [])
    head = rows[0]
    col = {n: i for i, n in enumerate(head)}
    need = ['Data', 'Hora', 'Forma de pagamento', 'Total (R$)']
    for n in need:
        if n not in col:
            sys.exit(f'Coluna "{n}" não encontrada no cabeçalho: {head}')
    get = lambda r, n: r[col[n]].strip() if col.get(n) is not None and col[n] < len(r) else ''

    have = {(c['data'], c['hora']) for c in D['conciliacao']}
    novas, avisos, ultima = [], [], None
    for r in rows[1:]:
        data = get(r, 'Data')
        if not re.fullmatch(r'\d\d/\d\d/\d{4}', data):
            continue
        d, mo, y = data.split('/')
        iso = f'{y}-{mo}-{d}'
        ultima = max(ultima or iso, iso)
        hora = get(r, 'Hora')[:5]
        if (iso, hora) in have:
            continue
        bruto = round(brl(get(r, 'Total (R$)')), 2)
        if bruto <= 0:
            avisos.append(f'{data} {hora}: total zerado, ignorada')
            continue
        forma = FORMAS.get(get(r, 'Forma de pagamento').lower(), get(r, 'Forma de pagamento') or 'Não informada')
        n = max(1, round(bruto / PRECO))
        D['conciliacao'].append(dict(data=iso, hora=hora, forma=forma, marmitas=n, bruto=bruto,
                                     taxas=None, cashback=None, liquido=None, situacao='Aguardando extrato'))
        have.add((iso, hora))
        novas.append(f'{data} {hora} {forma} R$ {bruto:.2f} ({n} marm.)')
        obs = get(r, 'Observações')
        if obs:
            avisos.append(f'{data} {hora}: {obs}')

    if not novas:
        print(json.dumps({'novas': 0, 'ultima_venda': ultima}, ensure_ascii=False))
        return

    # recalcula agregados de volume a partir da lista venda a venda
    C = D['conciliacao']
    C.sort(key=lambda c: (c['data'], c['hora']))
    R = D['resumo']
    R['pedidos'] = len(C)
    R['marmitas'] = sum(c['marmitas'] for c in C)
    R['bruto'] = round(sum(c['bruto'] for c in C), 2)
    R['ticket'] = round(R['bruto'] / R['pedidos'], 2)
    ag = [c for c in C if c['situacao'] == 'Aguardando extrato']
    R['aguardando'] = len(ag)
    R['aguardando_bruto'] = round(sum(c['bruto'] for c in ag), 2)

    old = {r['dia']: r for r in D['por_dia']}
    dias = {}
    for c in C:
        r = dias.setdefault(c['data'], dict(dia=c['data'], liquido_conc=old.get(c['data'], {}).get('liquido_conc', 0), bruto=0, vendas=0))
        r['bruto'] = round(r['bruto'] + c['bruto'], 2)
        r['vendas'] += 1
        if c['situacao'] == 'Aguardando extrato':
            r['pend'] = round(r.get('pend', 0) + c['bruto'], 2)
    D['por_dia'] = sorted(dias.values(), key=lambda r: r['dia'])

    pag = {}
    for c in C:
        q = pag.setdefault(c['forma'], dict(forma=c['forma'], bruto=0, vendas=0))
        q['bruto'] = round(q['bruto'] + c['bruto'], 2)
        q['vendas'] += 1
    D['por_pagamento'] = sorted(pag.values(), key=lambda r: -r['bruto'])

    fim = D['por_dia'][-1]['dia']
    D['ate'] = f'{fim[8:]}/{fim[5:7]}'
    D.setdefault('ate_banco', D['ate'])

    h = h.replace(m.group(0), 'const D = ' + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';')
    ini = D['por_dia'][0]['dia']
    h = re.sub(r'de \d\d/\d\d a \d\d/\d\d</p>', f'de {ini[8:]}/{ini[5:7]} a {D["ate"]}</p>', h, count=1)
    open(html_path, 'w', encoding='utf-8').write(h)
    print(json.dumps({'novas': len(novas), 'vendas': novas, 'avisos': avisos, 'ate': D['ate'],
                      'pedidos': R['pedidos'], 'bruto': R['bruto'], 'aguardando': R['aguardando'],
                      'aguardando_bruto': R['aguardando_bruto']}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
