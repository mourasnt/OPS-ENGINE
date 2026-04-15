import os
import json
import requests
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request

with open('utils/config.json', 'r') as f:
    config = json.load(f)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('dados/credentials.json', scopes=SCOPES)
creds.refresh(Request())

spreadsheet_id = config['main_sheet']['spreadsheet_id']
worksheet_name = config['main_sheet']['worksheet_name']

url = f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}?includeGridData=false'
headers = {'Authorization': f'Bearer {creds.token}'}

resp = requests.get(url, headers=headers)
data = resp.json()

print('Planilha:', data.get('properties', {}).get('title'))
print('\nAbas:')
for sheet in data.get('sheets', []):
    props = sheet.get('properties', {})
    title = props.get('title', 'N/A')
    sheet_id = props.get('sheetId', 'N/A')
    print(f'  - {title} (ID: {sheet_id})')

print('\n' + '='*50)
print(f'Verificando proteção na aba: {worksheet_name}')
print('='*50)

for sheet in data.get('sheets', []):
    if sheet.get('properties', {}).get('title') == worksheet_name:
        props = sheet.get('properties', {})
        sheet_id = props.get('sheetId')
        
        print(f'\nSheet ID: {sheet_id}')
        
        if 'protectedRanges' in sheet:
            print('PROTEÇÕES ENCONTRADAS:')
            for pr in sheet.get('protectedRanges', []):
                print(f'  - Nome: {pr.get("name")}')
                print(f'    Range: {pr.get("range")}')
                print(f'    Editors: {pr.get("editors")}')
        else:
            print('Nenhuma proteção encontrada nesta aba.')
        
        break
