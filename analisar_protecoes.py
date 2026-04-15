import os
import json
import requests
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request

with open('utils/config.json', 'r') as f:
    config = json.load(f)

def col_index_para_letra(n):
    resultado = ""
    while n > 0:
        n -= 1
        resultado = chr(65 + (n % 26)) + resultado
        n //= 26
    return resultado

def letra_para_col_index(s):
    resultado = 0
    for c in s.upper():
        resultado = resultado * 26 + (ord(c) - ord('A') + 1)
    return resultado

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('dados/credentials.json', scopes=SCOPES)
creds.refresh(Request())

spreadsheet_id = config['main_sheet']['spreadsheet_id']
worksheet_name = config['main_sheet']['worksheet_name']

url = f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}?includeGridData=false'
headers = {'Authorization': f'Bearer {creds.token}'}

resp = requests.get(url, headers=headers)
data = resp.json()

print('='*70)
print('PROTEÇÕES ENCONTRADAS NA ABA SHOPEE')
print('='*70)
print()
print('NOTA: Índices do Google API são 0-based, mas colunas do gspread são 1-based')
print()

for sheet in data.get('sheets', []):
    if sheet.get('properties', {}).get('title') == worksheet_name:
        protected_ranges = sheet.get('protectedRanges', [])
        
        if not protected_ranges:
            print('Nenhuma proteção encontrada.')
        else:
            print(f'Total de proteções: {len(protected_ranges)}')
            print()
            
            colunas_protegidas = set()
            linhas_protegidas = set()
            
            for i, pr in enumerate(protected_ranges, 1):
                rng = pr.get('range', {})
                start_row = rng.get('startRowIndex', 0)
                end_row = rng.get('endRowIndex', 0)
                start_col = rng.get('startColumnIndex', 0)
                end_col = rng.get('endColumnIndex', 0)
                
                print(f'Proteção #{i}:')
                
                # Converter colunas
                if start_col is not None and end_col is not None:
                    cols = []
                    for c in range(start_col, end_col):
                        col_letter = col_index_para_letra(c + 1)  # +1 para 1-based
                        cols.append(col_letter)
                        colunas_protegidas.add(c + 1)
                    
                    if len(cols) > 0:
                        if len(cols) == 1:
                            print(f'  Coluna: {cols[0]}')
                        else:
                            print(f'  Colunas: {cols[0]} a {cols[-1]} ({len(cols)} colunas)')
                
                # Converter linhas
                if start_row is not None and end_row is not None:
                    linhas = []
                    for r in range(start_row, end_row):
                        linhas.append(r + 1)  # +1 para 1-based
                        linhas_protegidas.add(r + 1)
                    
                    if len(linhas) > 0:
                        if len(linhas) == 1:
                            print(f'  Linha: {linhas[0]}')
                        else:
                            print(f'  Linhas: {linhas[0]} a {linhas[-1]} ({len(linhas)} linhas)')
                
                print()
        
        break

print('='*70)
print('RESUMO')
print('='*70)

if colunas_protegidas:
    cols_sorted = sorted(colunas_protegidas)
    cols_letras = [col_index_para_letra(c) for c in cols_sorted]
    print(f'Colunas protegidas ({len(cols_sorted)}): {", ".join(cols_letras)}')
    
    # Verificar se alguma das nossas colunas está protegida
    cols_relevantes = {
        'PRÉ SM': 16,
        'SM EFET.': 17,
        'COD SM': 53,
        'Status de emissão': 35,
        'CTE': 39,
        '$ Transportado': 36
    }
    
    print()
    print('Cols. relevantes protegidas:')
    for nome, idx in cols_relevantes.items():
        if idx in colunas_protegidas:
            letra = col_index_para_letra(idx)
            print(f'  [PROTEGIDA] {nome} (Coluna {letra}, índice {idx})')
        else:
            letra = col_index_para_letra(idx)
            print(f'  [OK] {nome} (Coluna {letra}, índice {idx})')
else:
    print('Nenhuma coluna protegida.')

if linhas_protegidas:
    print(f'\nLinhas protegidas ({len(linhas_protegidas)}): {sorted(linhas_protegidas)}')
