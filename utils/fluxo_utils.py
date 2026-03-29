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


# ===================================================================
# GERENCIADOR DE THREAD POOL DINÂMICO
# ===================================================================
import threading
from math import ceil
from typing import Callable, Optional, Dict, Any
import redis


class ThreadPoolManager:
    """
    Gerencia um pool de threads dinâmico que escala baseado na quantidade
    de jobs pendentes nas filas Redis.
    
    Fórmula de escaling: ceil(jobs_pendentes / 50) threads por tipo de job
    
    Exemplo:
      - 322 jobs de conferência → ceil(322/50) = 7 threads
      - 3 jobs de emissão → ceil(3/50) = 1 thread
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        config: Dict[str, Any],
        ejecutor_function: Callable,
        usuario: str,
        senha: str,
        rebalance_interval: int = 60,
        max_threads_per_type: int = 10,
        max_total_threads: int = 20,
        status_display = None,  # Opcional: StatusDisplay para atualizar status em tempo real
    ):
        """
        Args:
            redis_client: Cliente Redis para leitura de tamanho das filas
            config: Configuração da aplicação
            ejecutor_function: Função que executa o fluxo (ex: executar_fluxo)
            usuario: Usuário RPA
            senha: Senha RPA
            rebalance_interval: Intervalo em segundos para verificar e ajustar threads (default: 60s)
            max_threads_per_type: Limite máximo de threads por tipo (conferência/emissão)
            max_total_threads: Limite máximo total de threads
        """
        self.redis_client = redis_client
        self.config = config
        self.ejecutor_function = ejecutor_function
        self.usuario = usuario
        self.senha = senha
        self.rebalance_interval = rebalance_interval
        self.max_threads_per_type = max_threads_per_type
        self.max_total_threads = max_total_threads
        self.status_display = status_display  # Display de status em tempo real (opcional)
        
        # Carregar configurações de thread pool do config.json
        thread_pool_cfg = config.get("thread_pool_settings", {})
        self.min_threads_per_type = thread_pool_cfg.get("min_threads_per_type", 1)
        self.jobs_per_thread_ratio = thread_pool_cfg.get("jobs_per_thread_ratio", 50)
        
        # Dicionário para rastrear threads ativas por tipo
        # {"conferencia": [t1, t2, ...], "emissao": [t3, t4, ...], "manifesto": [t5, ...]}
        self.threads: Dict[str, list] = {
            "conferencia": [],
            "emissao": [],
            "manifesto": []
        }
        
        # Dicionário para rastrear threads marcadas para morte por tipo
        # {"conferencia": set([t1, t2]), "emissao": set([t3]), "manifesto": set([t4])}
        self.__threads_marked_to_die: Dict[str, set] = {
            "conferencia": set(),
            "emissao": set(),
            "manifesto": set()
        }
        
        # Lock para operações thread-safe
        self.lock = threading.Lock()
        
        # Flag para controlar se o gerenciador está rodando
        self.running = True
    
    def calcular_threads_necessarias(self, tipo_job: str) -> int:
        """
        Calcula quantas threads são necessárias para o tipo de job.
        
        Fórmula: ceil(jobs_pendentes / jobs_per_thread_ratio)
        Mínimo: min_threads_per_type (configurável em thread_pool_settings) - SEMPRE respeitado
        Máximo: max_threads_per_type
        """
        fila_key = f"fila:{tipo_job}"
        try:
            jobs_pendentes = self.redis_client.llen(fila_key)
            
            if jobs_pendentes == 0:
                # Mesmo sem jobs, mantém o mínimo de threads configurado
                return self.min_threads_per_type
            
            threads_necessarias = ceil(jobs_pendentes / self.jobs_per_thread_ratio)
            threads_necessarias = max(threads_necessarias, self.min_threads_per_type)
            threads_necessarias = min(threads_necessarias, self.max_threads_per_type)
            
            return threads_necessarias
        except Exception as e:
            logger.error(f"Erro ao contar jobs em fila:{tipo_job}: {e}")
            return self.min_threads_per_type  # Em caso de erro, retorna o mínimo
    
    def _marcar_thread_para_morte(self, tipo_job: str, thread: threading.Thread):
        """
        Marca uma thread para ser encerrada graciosamente após terminar seu job atual.
        
        A thread receberá um sinal via estrutura compartilhada e encerrará seu loop
        de consumo de jobs quando completar o job em execução.
        """
        try:
            self.__threads_marked_to_die[tipo_job].add(thread)
            logger.info(
                f"[DOWNSCALE] Thread '{thread.name}' marcada para morrer após completar job atual."
            )
        except Exception as e:
            logger.error(f"Erro ao marcar thread para morte: {e}")
    
    def _matar_threads_excedentes(self, tipo_job: str):
        """
        Marca threads excedentes para morte quando a demanda diminui.
        
        Estratégia:
        1. Calcula threads necessárias baseado em jobs pendentes
        2. Compara com threads atuais (vivas)
        3. Se excedentes: marca as últimas (mais novas) para morte graceful
        4. Threads marcadas finalizam seu job atual e encerram
        
        Args:
            tipo_job: Tipo de job ("conferencia" ou "emissao")
        """
        try:
            # Limpar threads já marcadas que morreram
            self.__threads_marked_to_die[tipo_job] = {
                t for t in self.__threads_marked_to_die[tipo_job] if t.is_alive()
            }
            
            # Threads que estão realmente vivas e NÃO estão marcadas para morte
            threads_vivas_ativas = [
                t for t in self.threads[tipo_job]
                if t.is_alive() and t not in self.__threads_marked_to_die[tipo_job]
            ]
            
            threads_necessarias = self.calcular_threads_necessarias(tipo_job)
            threads_atuais = len(threads_vivas_ativas)
            
            if threads_necessarias < threads_atuais:
                threads_para_matar = threads_atuais - threads_necessarias
                
                # Marca as últimas (mais novas) threads para morte
                # Reversed para matar as mais novas primeiro
                for thread in reversed(threads_vivas_ativas[-threads_para_matar:]):
                    self._marcar_thread_para_morte(tipo_job, thread)
                
                logger.warning(
                    f"[DOWNSCALE] {tipo_job}: Marcando {threads_para_matar} thread(s) para morte. "
                    f"({threads_atuais} → {threads_necessarias} necessárias)"
                )
        
        except Exception as e:
            logger.error(f"Erro ao matar threads excedentes de {tipo_job}: {e}")
    
    def thread_deve_morrer(self, tipo_job: str) -> bool:
        """
        Verifica se a thread atual foi marcada para morte.
        
        Deve ser chamada pelo worker para saber se deve encerrar após terminar o job atual.
        
        Returns:
            True se a thread deve morrer, False caso contrário
        """
        try:
            thread_atual = threading.current_thread()
            return thread_atual in self.__threads_marked_to_die.get(tipo_job, set())
        except Exception as e:
            logger.error(f"Erro ao verificar se thread deve morrer: {e}")
            return False
    
    def _atualizar_status_display(self):
        """Atualiza o display de status com o número atual de threads por tipo."""
        if self.status_display:
            try:
                with self.lock:
                    self.status_display.atualizar_threads(
                        "conferencia",
                        len([t for t in self.threads["conferencia"] if t.is_alive()])
                    )
                    self.status_display.atualizar_threads(
                        "emissao",
                        len([t for t in self.threads["emissao"] if t.is_alive()])
                    )
                    self.status_display.atualizar_threads(
                        "manifesto",
                        len([t for t in self.threads["manifesto"] if t.is_alive()])
                    )
            except Exception as e:
                logger.error(f"Erro ao atualizar status display: {e}")
    
    def criar_thread_worker(self, tipo_job: str, nome_worker: str) -> threading.Thread:
        """Cria e retorna uma nova thread para executar o worker."""
        # Importa aqui para evitar imports circulares
        from workers.fluxo_conferencia import fluxo_conferencia_worker
        from workers.fluxo_verificar_emissao import fluxo_verificar_emissao_worker
        from workers.fluxo_encerrar_manifesto import fluxo_encerrar_manifesto_worker

        worker_map = {
            "conferencia": fluxo_conferencia_worker,
            "emissao": fluxo_verificar_emissao_worker,
            "manifesto": fluxo_encerrar_manifesto_worker,
        }

        worker_func = worker_map.get(tipo_job)
        if not worker_func:
            logger.error(f"Worker desconhecido: {tipo_job}")
            return None

        thread = threading.Thread(
            target=self.ejecutor_function,
            args=(nome_worker, worker_func, self.config),
            daemon=True,
            name=f"Worker-{tipo_job}-{len(self.threads[tipo_job])+1}"
        )
        return thread
    
    def rebalancear_threads(self):
        """
        Verifica a quantidade de jobs pendentes e ajusta o número de threads.
        
        - Se jobs aumentam: cria novas threads
        - Se jobs diminuem: finaliza threads em excesso graciosamente
        """
        with self.lock:
            for tipo_job in ["conferencia", "emissao", "manifesto"]:
                threads_atuais = len(self.threads[tipo_job])
                threads_necessarias = self.calcular_threads_necessarias(tipo_job)
                # Limpa threads mortas
                self.threads[tipo_job] = [t for t in self.threads[tipo_job] if t.is_alive()]
                threads_atuais = len(self.threads[tipo_job])
                fila_key = f"fila:{tipo_job}"
                jobs_pendentes = self.redis_client.llen(fila_key)
                if threads_necessarias > threads_atuais:
                    # ESCALAR: Criar novas threads
                    diferenca = threads_necessarias - threads_atuais
                    logger.info(
                        f"[ESCALAR] {tipo_job}: {jobs_pendentes} jobs → "
                        f"criando {diferenca} thread(s) (total: {threads_atuais} → {threads_necessarias})"
                    )
                    for i in range(diferenca):
                        try:
                            thread_num = threads_atuais + i + 1
                            nome_worker = f"{tipo_job}_worker_{thread_num}"
                            nova_thread = self.criar_thread_worker(tipo_job, nome_worker)
                            if nova_thread:
                                nova_thread.start()
                                self.threads[tipo_job].append(nova_thread)
                                logger.success(
                                    f"Thread '{nova_thread.name}' iniciada. "
                                    f"Total de {tipo_job}: {len(self.threads[tipo_job])}"
                                )
                        except Exception as e:
                            logger.error(f"Erro ao criar thread de {tipo_job}: {e}")
                elif threads_necessarias < threads_atuais:
                    # DOWNSCALE: Marcar threads excedentes para morte graceful
                    diferenca = threads_atuais - threads_necessarias
                    logger.warning(
                        f"[DOWNSCALE] {tipo_job}: {jobs_pendentes} jobs → "
                        f"marcando {diferenca} thread(s) para morrer (total: {threads_atuais} → {threads_necessarias})"
                    )
                    self._matar_threads_excedentes(tipo_job)
                else:
                    # Sem mudança
                    if threads_atuais > 0:
                        logger.debug(
                            f"[EQUILIBRIO] {tipo_job}: {jobs_pendentes} jobs → "
                            f"{threads_atuais} thread(s) ativa(s). Sem mudanças."
                        )
        # Atualizar display de status após rebalanceamento
        self._atualizar_status_display()
    
    def monitorar_rebalanceamento(self):
        """
        Loop que monitora periodicamente e rebalanceia threads.
        Roda em sua própria thread daemon.
        """
        logger.info(
            f"Monitor de rebalanceamento iniciado. "
            f"Verificando a cada {self.rebalance_interval}s. "
            f"Limites: {self.max_threads_per_type} por tipo, {self.max_total_threads} total."
        )
        
        while self.running:
            try:
                time.sleep(self.rebalance_interval)
                
                if not self.running:
                    break
                
                self.rebalancear_threads()
                
            except Exception as e:
                logger.error(f"Erro no monitor de rebalanceamento: {e}")
    
    def iniciar(self):
        """Inicia o gerenciador de thread pool."""
        logger.info("Iniciando ThreadPoolManager...")
        # Cria threads iniciais (SEMPRE 1 de cada tipo, independente de jobs)
        with self.lock:
            for tipo_job in ["conferencia", "emissao", "manifesto"]:
                try:
                    nome_worker = f"{tipo_job}_worker_1"
                    nova_thread = self.criar_thread_worker(tipo_job, nome_worker)
                    if nova_thread:
                        nova_thread.start()
                        self.threads[tipo_job].append(nova_thread)
                        logger.success(f"Thread inicial '{nova_thread.name}' iniciada.")
                except Exception as e:
                    logger.error(f"Erro ao criar thread inicial de {tipo_job}: {e}")
        # Inicia thread de monitoramento
        thread_monitor = threading.Thread(
            target=self.monitorar_rebalanceamento,
            daemon=True,
            name="ThreadPoolMonitor"
        )
        thread_monitor.start()
        logger.success("ThreadPoolManager iniciado com sucesso.")
    
    def aguardar_encerramento(self):
        """
        Monitora threads de workers e recria as que morreram inesperadamente.
        Também verifica kill signals do Watchdog e cria threads de reposição.
        IMPORTANTE: Threads mortas por downscaling intencional NÃO são recriadas.
        """
        logger.info("Monitorando threads de workers...")
        while self.running:
            # Verificar kill signals pendentes e criar threads de reposição
            self._processar_kill_signals()
            with self.lock:
                for tipo_job in ["conferencia", "emissao", "manifesto"]:
                    # Separar threads vivas de mortas
                    threads_vivas = []
                    threads_mortas_inesperadamente = []
                    for t in self.threads[tipo_job]:
                        if t.is_alive():
                            threads_vivas.append(t)
                        else:
                            # Thread morreu - verificar se foi intencional (downscaling)
                            if t in self.__threads_marked_to_die[tipo_job]:
                                # Morte intencional (downscaling) - remover do registro e NÃO recriar
                                self.__threads_marked_to_die[tipo_job].discard(t)
                                logger.info(
                                    f"[DOWNSCALE] Thread '{t.name}' encerrada gracefully por downscaling."
                                )
                            else:
                                # Morte inesperada (crash) - precisa ser recriada
                                threads_mortas_inesperadamente.append(t)
                    # Recriar apenas threads que morreram inesperadamente
                    if threads_mortas_inesperadamente:
                        logger.warning(
                            f"[RECUPERAR] {tipo_job}: {len(threads_mortas_inesperadamente)} thread(s) morreu(morreram) inesperadamente! "
                            f"Recriando..."
                        )
                        for thread_morta in threads_mortas_inesperadamente:
                            try:
                                nome_worker = f"{tipo_job}_worker_recovery_{int(time.time())}"
                                nova_thread = self.criar_thread_worker(tipo_job, nome_worker)
                                if nova_thread:
                                    nova_thread.start()
                                    threads_vivas.append(nova_thread)
                                    logger.success(f"Thread de recuperação '{nova_thread.name}' iniciada.")
                            except Exception as e:
                                logger.error(f"Erro ao recriar thread de {tipo_job}: {e}")
                    self.threads[tipo_job] = threads_vivas
                    # Verifica se precisa criar mais threads por falta
                    threads_atuais = len(threads_vivas)
                    fila_key = f"fila:{tipo_job}"
                    try:
                        jobs_pendentes = self.redis_client.llen(fila_key)
                        # Se tem jobs mas 0 threads, cria pelo menos 1
                        if threads_atuais == 0 and jobs_pendentes > 0:
                            logger.info(
                                f"[RECRIAR] {tipo_job}: 0 threads mas {jobs_pendentes} jobs. "
                                f"Criando thread de recuperação..."
                            )
                            nome_worker = f"{tipo_job}_worker_recovery_{int(time.time())}"
                            nova_thread = self.criar_thread_worker(tipo_job, nome_worker)
                            if nova_thread:
                                nova_thread.start()
                                self.threads[tipo_job].append(nova_thread)
                                logger.success(f"Thread de recuperação '{nova_thread.name}' iniciada.")
                    except Exception as e:
                        logger.error(f"Erro ao verificar fila {tipo_job}: {e}")
            # Conta total de threads vivas
            threads_total = sum(
                1 for threads_list in self.threads.values()
                for thread in threads_list
                if thread.is_alive()
            )
            if threads_total == 0:
                # Verifica se realmente não há jobs antes de sair
                jobs_conferencia = self.redis_client.llen("fila:conferencia")
                jobs_emissao = self.redis_client.llen("fila:emissao")
                jobs_manifesto = self.redis_client.llen("fila:manifesto")
                if jobs_conferencia == 0 and jobs_emissao == 0 and jobs_manifesto == 0:
                    logger.info(
                        "Todas as threads terminaram e não há jobs pendentes. "
                        "Sistema entrando em modo de espera..."
                    )
                else:
                    logger.warning(
                        f"Threads morreram com jobs pendentes! "
                        f"Conferência: {jobs_conferencia}, Emissão: {jobs_emissao}, Manifesto: {jobs_manifesto}. "
                        f"Aguardando rebalanceamento..."
                    )
            logger.debug(f"{threads_total} thread(s) ativa(s)...")
            time.sleep(10)
    
    def _processar_kill_signals(self):
        """
        Verifica kill signals do Watchdog e cria threads de reposição.
        Quando o Watchdog detecta um job travado, ele envia um kill signal.
        Esta função lê esses sinais e cria novas threads para substituir as travadas.
        """
        import json
        try:
            kill_signals = self.redis_client.smembers("watchdog:kill_workers")
            if not kill_signals:
                return
            for signal_json in kill_signals:
                try:
                    signal = json.loads(signal_json)
                    tipo_job = signal.get("tipo", "conferencia")
                    job_id = signal.get("job_id", "desconhecido")
                    if tipo_job not in ["conferencia", "emissao", "manifesto"]:
                        logger.error(f"Kill signal recebido para tipo desconhecido: {tipo_job}. Ignorando.")
                        self.redis_client.srem("watchdog:kill_workers", signal_json)
                        continue
                    logger.warning(
                        f"[KILL SIGNAL] Detectado travamento do job '{job_id}' ({tipo_job}). "
                        f"Criando thread de substituição..."
                    )
                    # Criar uma nova thread imediatamente (não espera a antiga morrer)
                    with self.lock:
                        nome_worker = f"{tipo_job}_worker_replace_{int(time.time())}"
                        nova_thread = self.criar_thread_worker(tipo_job, nome_worker)
                        if nova_thread:
                            nova_thread.start()
                            self.threads[tipo_job].append(nova_thread)
                            logger.success(
                                f"Thread de substituição '{nova_thread.name}' iniciada para {tipo_job}. "
                                f"Thread travada será descartada quando morrer."
                            )
                    # Remove o kill signal após processamento
                    self.redis_client.srem("watchdog:kill_workers", signal_json)
                except json.JSONDecodeError:
                    # Remove signals inválidos
                    self.redis_client.srem("watchdog:kill_workers", signal_json)
                except Exception as e:
                    logger.error(f"Erro ao processar kill signal: {e}")
        except Exception as e:
            logger.error(f"Erro ao verificar kill signals: {e}")
    
    def parar(self):
        """Para o gerenciador (sinaliza o fim, threads daemon encerram com a app)."""
        logger.info("Parando ThreadPoolManager...")
        self.running = False
        logger.info("ThreadPoolManager parado. Threads daemon encerrarão com a aplicação.")