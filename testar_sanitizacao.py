from utils.helpers import sanitizar_para_sheets

test_cases = [
    # Caso 1: URL longa com timeout
    ("ERRO: Erro de comunicação com o serviço de rotas: 504 Server Error: Gateway Time-out for url: http://integra.rastergr.com.br:8888/datasnap/rest/TWebService/%22getRotas%22/", "URL longa com timeout"),
    
    # Caso 2: Erro da API com veículo
    ("ERRO: Erro retornado pela API da Raster: O veiculo ja possui SM iniciada ou em aberto", "Erro veículo SM"),
    
    # Caso 3: Código numérico (não deve mudar)
    ("8655953", "Código numérico"),
    
    # Caso 4: Status Cancelada
    ("Cancelada", "Status cancelada"),
    
    # Caso 5: Mensagem curta
    ("Verificar Emissão", "Mensagem curta"),
    
    # Caso 6: Texto normal
    ("CT-e Autorizado", "Texto normal"),
    
    # Caso 7: Valor None/vazio
    (None, "Valor None"),
    ("", "String vazia"),
    
    # Caso 8: URL sem ERRO
    ("http://site.com/api?param=%22test%22", "URL sem ERRO"),
    
    # Caso 9: Multiplas quebras de linha
    ("Erro linha 1\nErro linha 2\rErro linha 3", "Múltiplas quebras"),
    
    # Caso 10: Texto muito longo
    ("A" * 500, "Texto > 300 chars"),
]

print("=" * 80)
print("TESTE DE SANITIZAÇÃO")
print("=" * 80)

for valor, desc in test_cases:
    resultado = sanitizar_para_sheets(valor)
    print(f"\n>>> {desc}")
    print(f"   Input:  {repr(valor)[:80]}{'...' if len(str(valor)) > 80 else ''}")
    print(f"   Output: {repr(resultado)[:80]}{'...' if len(resultado) > 80 else ''}")
    print(f"   Len:    {len(resultado)} chars")

print("\n" + "=" * 80)
print("TESTES CONCLUÍDOS")
print("=" * 80)
