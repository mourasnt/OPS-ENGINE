from utils.helpers import sanitizar_para_sheets

erro_original = "ERRO: Erro de comunicação com o serviço de rotas: 504 Server Error: Gateway Time-out for url: http://integra.rastergr.com.br:8888/datasnap/rest/TWebService/%22getRotas%22/"

print("=" * 80)
print("TESTE DO ERRO ORIGINAL DA COLUNA 16")
print("=" * 80)
print(f"\nOriginal ({len(erro_original)} chars):\n{erro_original}")
print(f"\nSanitizado ({len(sanitizar_para_sheets(erro_original))} chars):\n{sanitizar_para_sheets(erro_original)}")

print("\n" + "=" * 80)
print("TESTES DE VALIDAÇÃO")
print("=" * 80)

testes = [
    ("8655953", "Código numérico da coluna 53"),
    ("9315590", "Código numérico da coluna 16"),
    ("Cancelada", "Status Cancelada"),
    ("Verificar Emissão", "Status Emissão"),
    ("OK", "Status SM OK"),
]

all_pass = True
for valor, desc in testes:
    result = sanitizar_para_sheets(valor)
    status = "OK" if result == str(valor) else "MODIFICADO"
    print(f"  {status}: {desc} -> '{result}'")
    if result != str(valor):
        all_pass = False

print("\n" + "=" * 80)
if all_pass:
    print("TODOS OS VALORES NORMAIS FORAM MANTIDOS!")
else:
    print("ALGUNS VALORES FORAM MODIFICADOS!")
