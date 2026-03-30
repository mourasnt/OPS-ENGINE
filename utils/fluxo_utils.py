import time
import datetime
import re
from playwright.sync_api import Locator, TimeoutError, Page, expect
from fluxos.fluxo_login import fluxo_login
from typing import List, Dict
from loguru import logger
import json
import os

# Carrega configurações de timeout
config_path = os.path.join(os.path.dirname(__file__), "config.json")
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

PAGE_RELOAD_TIMEOUT = config.get("timeout_settings", {}).get("page_reload_ms", 45000)

def garantir_pagina_consulta(
    page: Page,
    url_alvo: str,
    seletor_chave: str,
    url_login_parcial: str = "login",
    max_tentativas: int = 3,
    espera_entre_tentativas: int = 5
) -> bool:
    """Valida e recupera a página-alvo, fazendo login se necessário."""
    for tentativa in range(1, max_tentativas + 1):
        try:
            url_atual = page.url
            if not url_atual.startswith(url_alvo):
                logger.debug(f"Robô não está na página alvo. URL atual: {url_atual}. Corrigindo...")
                if url_login_parcial in url_atual:
                    logger.debug("Detectada página de login. Executando login...")
                    if not fluxo_login(page):
                        raise Exception("Falha no login durante a recuperação de estado.")
                
                logger.debug(f"Navegando para a página alvo: {url_alvo}")
                page.goto(url_alvo)

            expect(page.locator(seletor_chave)).to_be_visible(timeout=30000)
            
            if tentativa > 1:
                logger.debug(f"Página recuperada com sucesso na tentativa {tentativa}.")
            return True

        except Exception as e:
            logger.debug(f"Tentativa {tentativa}/{max_tentativas} falhou ao validar a página. Erro: {e}")
            if tentativa == max_tentativas:
                break 
            
            logger.debug(f"Tentando recuperar... Aguardando {espera_entre_tentativas} segundos.")
            time.sleep(espera_entre_tentativas)
            
            # Tenta ir direto para a URL nas tentativas subsequentes
            if tentativa > 1:
                page.goto(url_alvo)
            else:
                try:
                    page.reload(timeout=PAGE_RELOAD_TIMEOUT, wait_until="domcontentloaded") # Apenas recarrega na primeira falha
                except Exception as reload_err:
                    logger.error(f"Falha ao recarregar página: {reload_err}")

    logger.critical(f"Não foi possível validar ou recuperar a página '{url_alvo}' após {max_tentativas} tentativas.")
    return False


def goto_cards(page):
    """Navega para a aba 'Cards' de emissão."""
    # Garante que estamos na página de emissão
    garantir_pagina_consulta(page, "https://portal.emiteai.com.br/#/emissor", '[role="tab"]:has-text("Cards")')

    # Fecha modal de cookies ou popups se existirem
    if page.locator("text=Aceitar").count() > 0:
        logger.info("Fechando modal de cookies...")
        page.locator("text=Aceitar").click()

    if page.get_by_role("button", name="close").count() > 0:
        logger.info("Fechando modal de cookies...")
        page.get_by_role("button", name="close").click()

    # Clica na aba Cards com segurança
    cards_tab = page.get_by_role("tab", name="Cards")
    cards_tab.scroll_into_view_if_needed()
    cards_tab.click(force=True)

    # Aguarda a aba Cards estar ativa
    page.wait_for_function(
        'document.querySelector("[role=tab][aria-selected=true]")?.textContent.includes("Cards")'
    )
    logger.debug("Aba 'Cards' carregada com sucesso.")


def identificar_tipo_card(card: Locator) -> str | None:
    """Verifica se o card é do tipo 'cte' ou 'nfs'."""
    cte_block = card.locator("div", has_text=re.compile(r"^\s*CT-e\s*$"))
    if cte_block.count() > 0:
        cte_container = cte_block.first.locator("xpath=..")
        spans = cte_container.locator("button span")
        for i in range(spans.count()):
            text = spans.nth(i).inner_text().strip()
            if text.isdigit() and int(text) > 0:
                return "cte"

    nfs_block = card.locator("div", has_text=re.compile(r"^\s*NFS-e\s*$"))
    if nfs_block.count() > 0:
        nfs_container = nfs_block.first.locator("xpath=..")
        spans = nfs_container.locator("button span")
        for i in range(spans.count()):
            text = spans.nth(i).inner_text().strip()
            if text.isdigit() and int(text) > 0:
                return "nfs"
    return None


def obter_status_principal_card(card: Locator) -> str | None:
    """Extrai o status principal do card (ex: 'ag._revisão')."""
    try:
        menu_button = card.locator('button:has-text("more_vert")')
        menu_button.wait_for(state="visible", timeout=5000)

        status_locator = menu_button.locator("xpath=preceding-sibling::div[1]")
        status_texto = status_locator.inner_text().strip()
        
        return status_texto.lower().replace(" ", "_")
        
    except TimeoutError:
        logger.debug(f"Não foi possível encontrar o botão de menu ('more_vert') no card.")
        return "nao_encontrado"
    except Exception as e:
        logger.error(f"Erro ao extrair status principal do card via âncora de botão: {e}")
        return None


def verificar_status_cte(card) -> str | None:
    """Verifica os contadores de status do CT-e (Autorizado, Pendente, etc.)."""
    try:
        cte_label = card.locator("div", has_text=re.compile(r"^\s*CT-e\s*$"))
        if cte_label.count() == 0:
            return None # Sem seção CT-e

        cte_row_container = cte_label.first.locator("xpath=..")
        spans = cte_row_container.locator("button div span")
        
        if spans.count() != 4:
            logger.warning(f"Esperava 4 contadores para CT-e, mas encontrou {spans.count()}.")
            return None

        status_counts = {
            "autorizado": int(spans.nth(0).inner_text().strip()),
            "pendente":   int(spans.nth(1).inner_text().strip()),
            "rejeitado":  int(spans.nth(2).inner_text().strip()),
            "cancelado":  int(spans.nth(3).inner_text().strip()),
        }
        logger.debug(f"Status CT-e encontrados: {status_counts}")

        # Lógica de decisão
        if status_counts["rejeitado"] > 0:
            return "rejeitado"
        if status_counts["pendente"] > 0:
            return "pendente"
        if status_counts["cancelado"] > 0 and status_counts["autorizado"] == 0:
            return "cancelado"
        if status_counts["autorizado"] > 0:
            return "autorizado"
        if all(value == 0 for value in status_counts.values()):
            return "vazio"
        
        return "misto"

    except (ValueError, TypeError) as e:
        logger.error(f"Não foi possível converter um status de CT-e para número: {e}")
        return None
    except Exception as e:
        logger.error(f"Ocorreu um erro inesperado ao verificar status do CT-e: {e}")
        return None


def verificar_status_mdfe(card: Locator) -> str | None:
    """Extrai o status textual do MDF-e (ex: 'autorizado', 'encerrado')."""
    try:
        mdfe_label = card.locator("span", has_text=re.compile(r"^\s*MDF-e\s*$"))
        if mdfe_label.count() == 0:
            return None # Sem seção MDF-e

        mdfe_row_container = mdfe_label.first.locator("xpath=../..")
        status_button = mdfe_row_container.locator("button")
        
        if status_button.count() > 0:
            status_texto = status_button.first.inner_text().strip()
            return status_texto.lower().replace(" ", "_")

        else:
            logger.warning("Rótulo 'MDF-e' encontrado, mas o botão de status não foi localizado.")
            return "status_nao_encontrado"

    except Exception as e:
        logger.error(f"Ocorreu um erro inesperado ao verificar status do MDF-e: {e}")
        return None

def analisar_status_emissao(page: Page, numero_lt: str) -> dict | None:
    """Orquestra a análise completa de um card de LT."""
    try:
        card_locator = page.locator(".MuiGrid-root.MuiGrid-item.MuiGrid-grid-xs-12.MuiGrid-grid-sm-6").filter(
            has_text=re.compile(rf"DT:\s*{re.escape(numero_lt)}")
        )

        if card_locator.count() == 0:
            return None
        
        card = card_locator.first

        # Chama as funções especialistas
        status_principal = obter_status_principal_card(card)
        status_cte_detalhado = verificar_status_cte(card)
        status_mdfe_detalhado = verificar_status_mdfe(card)

        resultado = {
            "status_card": status_principal,
            "status_cte": status_cte_detalhado,
            "status_mdfe": status_mdfe_detalhado,
            "card": card # Passa o locator para o worker usar
        }
        
        logger.success(f"Análise da LT {numero_lt} concluída")
        return resultado

    except Exception as e:
        logger.critical(f"Erro inesperado ao analisar o card da LT {numero_lt}: {e}")
        return None

def obter_status_lt(page: Page, numero_lt: str) -> str:
    """Procura a LT na tabela e retorna o Status."""
    logger.debug(f"[obter_status_lt] Iniciando busca do status da LT {numero_lt}...")
    try:
        time.sleep(5)  # CORRIGIDO: Era 5000 segundos (83 min!) - Agora 2 segundos
        logger.debug(f"[obter_status_lt] Buscando linha na tabela para LT {numero_lt}...")
        linha_alvo = page.locator("table tbody tr", has_text=numero_lt).first
        
        if linha_alvo.count() == 0:
            logger.info(f"[obter_status_lt] LT {numero_lt} não encontrada na tabela.")
            return "não encontrado"

        logger.debug(f"[obter_status_lt] Linha encontrada, extraindo status...")
        status = linha_alvo.locator("td").nth(3).inner_text().strip()
        logger.debug(f"[obter_status_lt] Status extraído para LT {numero_lt}: '{status}'")
        return status

    except TimeoutError:
        logger.warning(f"[obter_status_lt] Timeout ao localizar a linha da LT {numero_lt} na tabela.")
        return "desconhecido"
    except Exception as e:
        logger.error(f"[obter_status_lt] Erro ao extrair Status da LT {numero_lt}: {e}")
        return "desconhecido"


def extrair_dados_dos_cards_cte(page: Page, numero_lt_esperado: str) -> List[Dict[str, any]]:
    """Extrai os dados de N° e Valor de todos os cards de CT-e para uma LT específica."""
    dados_dos_ctes = []
    logger.info(f"Iniciando extração de DADOS para a LT: {numero_lt_esperado}")

    try:
        container_principal = page.locator("div.MuiGrid-container[class*='css-h13rzo']")
        container_principal.wait_for(state="visible", timeout=120000)
        logger.debug("Container principal de CT-e encontrado.")

        cards_cte = container_principal.locator("div.MuiStack-root[class*='css-11jo4c7']")
        cards_cte.first.wait_for(state="visible", timeout=120000)
        total_cards = cards_cte.count()

        if total_cards == 0:
            return []

        logger.debug(f"{total_cards} cards de CT-e encontrados. Iniciando validação...")

        for i in range(total_cards):
            card = cards_cte.nth(i)
            
            try:
                # 1. Extrai a DT
                dt_locator = card.locator('p:has-text("DT:")')
                if dt_locator.count() == 0:
                    logger.warning(f"Card {i+1} ignorado. Não foi possível encontrar a DT.")
                    continue
                dt_extraido = dt_locator.first.inner_text().replace("DT:", "").strip()

                # 2. Validação da DT
                if dt_extraido != numero_lt_esperado:
                    logger.info(f"Card {i+1} ignorado. DT '{dt_extraido}' não corresponde à esperada '{numero_lt_esperado}'.")
                    continue
                
                logger.success(f"Card {i+1} validado para a LT '{dt_extraido}'.")

                # 3. Extrai o NÚMERO
                numero_locator = card.locator('p:has-text("Nº:")')
                numero_cte = numero_locator.first.inner_text().replace("Nº:", "").strip()

                # 4. Extrai o VALOR
                valor_locator = card.locator('p:has-text("Valor:")')
                valor_str = valor_locator.first.inner_text() # Ex: "Valor: 3068.70"
                
                valor_limpo_str = valor_str.split(":")[-1].replace("R$", "").replace("\xa0", "").strip()
                valor_cte = float(valor_limpo_str.replace(".", "").replace(",", "."))

                dados_dos_ctes.append({"numero": numero_cte, "valor": valor_cte})
    
            except Exception as e_card:
                logger.error(f"Erro ao processar o card {i+1}: {e_card}")
                continue

        logger.success(f"Extração finalizada. Total de CT-es validados: {len(dados_dos_ctes)}")
        return dados_dos_ctes

    except Exception as e:
        logger.critical(f"Erro inesperado ao extrair dados dos CT-es: {e}")
        return []


def extrair_dados_dos_cards_mdfe(page: Page) -> List[Dict[str, str]]:
    """Extrai N° e Chave de todos os cards de MDF-e na página."""
    dados_dos_mdfes = []
    logger.info("Iniciando extração interativa de dados dos cards de MDF-e...")

    try:
        container_principal = page.locator("div.MuiGrid-container[class*='css-h13rzo']")
        container_principal.wait_for(state="visible", timeout=120000)

        cards_mdfe = container_principal.locator("div.MuiStack-root[class*='css-11jo4c7']")
        cards_mdfe.first.wait_for(state="visible", timeout=120000)
        total_cards = cards_mdfe.count()

        if total_cards == 0:

            return []

        logger.debug(f"{total_cards} cards de MDF-e encontrados. Iniciando ciclo interativo...")

        for i in range(total_cards):
            card = cards_mdfe.nth(i)
            numero_mdfe = None
            chave_acesso = None

            try:
                # 4. Extrai o número do manifesto
                numero_locator = card.locator('p:has-text("Nº:")')
                if numero_locator.count() == 0:
                    logger.warning(f"Não foi possível encontrar o número do MDF-e no card {i+1}. Pulando.")
                    continue
                numero_mdfe = numero_locator.first.inner_text().replace("Nº:", "").strip()

                # 5. AÇÃO: Clica em "Detalhes"
                detalhes_button = card.locator('button:has(span[aria-label="Detalhes"])')
                detalhes_button.click()

                # 6. ESPERA: Aguarda o painel lateral (Drawer)
                drawer = page.locator("div.MuiDrawer-paperAnchorRight")
                drawer.wait_for(state="visible", timeout=10000)

                # 7. EXTRAÇÃO DA CHAVE
                chave_locator = drawer.locator('p:has-text("Chave de Acesso") + p')
                chave_acesso = chave_locator.inner_text().strip()

                # 8. AÇÃO: Fecha o drawer
                close_button = drawer.locator('button:has(svg[data-testid="CloseIcon"])')
                close_button.click()
                drawer.wait_for(state="hidden", timeout=5000)

                if numero_mdfe and chave_acesso:
                    dados_dos_mdfes.append({"numero": numero_mdfe, "chave": chave_acesso})

            except Exception as e_card:
                logger.error(f"Erro ao processar o card {i+1} (MDF-e nº {numero_mdfe}): {e_card}")
                if page.locator("div.MuiDrawer-paperAnchorRight").is_visible():
                    page.keyboard.press("Escape") # Tenta fechar o painel
                continue

        logger.debug(f"Extração concluída. Total de MDF-es processados: {len(dados_dos_mdfes)}")
        return dados_dos_mdfes

    except Exception as e:
        logger.critical(f"Erro inesperado durante a extração dos MDF-es: {e}")
        return []