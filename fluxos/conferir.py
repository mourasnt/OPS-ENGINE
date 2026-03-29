# -*- coding: utf-8 -*-

from playwright.sync_api import TimeoutError, Page, expect
import time
import re
from unidecode import unidecode
from loguru import logger
from rapidfuzz import process, fuzz # <--- Usa a busca robusta
from dados.dataclass import Carga
from utils.watchdog import TimeoutDetector


def normalizar_texto(texto: str) -> str:
    if not isinstance(texto, str):
        return ""
    texto_sem_underscore = texto.replace('_', ' ')
    texto_base = unidecode(texto_sem_underscore.lower())
    texto_limpo = ' '.join(texto_base.split())
    return texto_limpo


def escolher_opcao_mais_parecida(page: Page, texto_busca: str):
    try:
        page.wait_for_selector("[role='option']", timeout=20000)  # Reduzido de 120000ms para 20s 
    except TimeoutError:
        logger.warning(f"[Worker Conferência] Dropdown de opções não carregou (timeout) para '{texto_busca}'")
        return False

    texto_busca_norm = normalizar_texto(texto_busca)

    opcoes_locator = page.locator("[role='option']")
    try:
        todos_os_textos = opcoes_locator.all_inner_texts()
        if not todos_os_textos:
            logger.warning(f"[Worker Conferência] Dropdown de opções está visível, mas vazio para '{texto_busca}'.")
            return False
    except Exception as e:
        logger.warning(f"[Worker Conferência] Não foi possível extrair textos das opções para '{texto_busca}': {e}")
        return False

    mapa_de_textos = {normalizar_texto(t): t for t in todos_os_textos}

    melhor_match = process.extractOne(
        texto_busca_norm,
        mapa_de_textos.keys(),
        scorer=fuzz.WRatio, 
        score_cutoff=30
    )

    if melhor_match:
        texto_normalizado_encontrado, score, _ = melhor_match
        texto_original_da_opcao = mapa_de_textos[texto_normalizado_encontrado]

        logger.debug(f"[Worker Conferência] Match para '{texto_busca}': '{texto_original_da_opcao}' (Score: {score:.2f})")
        opcoes_locator.get_by_text(texto_original_da_opcao, exact=True).click()
        return True
    else:
        logger.warning(f"[Worker Conferência] Nenhuma opção correspondente encontrada para '{texto_busca_norm}' (Score < 30).")
        return False


def clicar_option(page: Page, name: str, exact: bool = True, timeout: int = 7000, max_attempts: int = 6) -> bool:
    """Tenta clicar em uma opção de dropdown com retries e backoff para evitar interceptação de pointer."""
    for attempt in range(1, max_attempts + 1):
        option_locator = page.get_by_role("option", name=name, exact=exact)
        try:
            expect(option_locator).to_be_visible(timeout=min(2000, timeout))
            option_locator.click(timeout=timeout)
            return True
        except Exception as exc:
            if attempt == max_attempts:
                logger.error(
                    f"[Worker Conferência] Falha ao clicar na opção '{name}' após {max_attempts} tentativas: {exc}"
                )
                return False
            wait_ms = 120 * attempt
            logger.warning(
                f"[Worker Conferência] Tentativa {attempt}/{max_attempts} para clicar em opção '{name}' falhou: {exc}. Aguardando {wait_ms}ms e tentando de novo."
            )
            page.wait_for_timeout(wait_ms)
            # garante que a dropdown ainda está aberta
            try:
                page.wait_for_selector("[role='option']", timeout=2000)
            except TimeoutError:
                logger.debug(f"[Worker Conferência] Dropdown fechou ao tentar selecionar '{name}'.")
                return False
    return False


def selecionar_mui_select(page: Page, select_selector: str, option_name: str, timeout: int = 7000) -> bool:
    """Clicka no controle MUI select e depois seleciona uma opção via clicar_option."""
    try:
        page.locator(select_selector).click(timeout=timeout)
    except Exception as exc:
        logger.warning(f"[Worker Conferência] Falha no clique initial do seletor '{select_selector}': {exc}")
        try:
            page.eval_on_selector(select_selector, "el => el.click()")
        except Exception as exc2:
            logger.error(f"[Worker Conferência] Falha ao forçar clique no seletor '{select_selector}': {exc2}")
            return False
        page.wait_for_timeout(150)

    if not clicar_option(page, option_name, exact=True, timeout=timeout):
        logger.error(f"[Worker Conferência] Não conseguiu selecionar '{option_name}' no select '{select_selector}'.")
        return False

    return True


def selecionar_mui_select_por_label(page: Page, label_text: str, option_name: str, timeout: int = 7000) -> bool:
    """Encontra um Select do MUI pela Label visível e seleciona a opção."""
    try:
        # Encontra o "bloco" do campo que contém a label exata
        # e pega o elemento clicável (role="button") dentro desse bloco
        dropdown = page.locator(".MuiFormControl-root").filter(has_text=re.compile(fr"^{label_text}")).get_by_role("button")
        
        dropdown.click(timeout=timeout)
        page.wait_for_timeout(200) # Pequena pausa para a animação do MUI
        
    except Exception as exc:
        logger.warning(f"[Worker Conferência] Falha ao abrir o dropdown da label '{label_text}': {exc}")
        return False

    # Usa a sua função robusta para clicar na opção da lista
    if not clicar_option(page, option_name, exact=True, timeout=timeout):
        logger.error(f"[Worker Conferência] Não conseguiu selecionar '{option_name}' na label '{label_text}'.")
        return False

    return True


def conferir_lt(page: Page, carga: Carga) -> dict:
    """
    Executa o RPA de conferência.
    Retorna um dicionário com o resultado:
    - {"status": "sucesso"}
    - {"status": "falha_cadastro", "motivo": "..."}
    - {"status": "falha_rpa", "motivo": "..."}
    """

    def cancelar_e_sair(campo: str, valor: str, tipo_erro: str = "falha_cadastro") -> dict:
        try:
            for _ in range(3):
                page.keyboard.press("Escape")
                time.sleep(0.2)
            expect(page.get_by_role("textbox", name="Placa principal")).to_be_hidden(timeout=5000)
        except Exception:
            page.reload(wait_until="networkidle")
        
        resultado = {"status": tipo_erro, "campo": campo, "valor": valor}
        # Adiciona 'motivo' para compatibilidade com o worker
        if tipo_erro == "falha_rpa":
            resultado["motivo"] = f"{campo}: {valor}"
        return resultado

    # Bloco try principal para o RPA
    try:
        for tentativa in range(1, 3):
            try:
                # ETAPA 1: Encontrar a linha da LT na tabela
                with TimeoutDetector("Encontrar LT na tabela", max_seconds=10, job_id=carga.numero_lt):
                    row_locator = page.locator(f"tr:has-text('{carga.numero_lt}')")
                    expect(row_locator).to_be_visible(timeout=10000)

                # ETAPA 2: Clicar no botão de edição da linha
                with TimeoutDetector("Clicar botão edição", max_seconds=5, job_id=carga.numero_lt):
                    page.get_by_role("checkbox").get_by_role("button").first.click()

                # ETAPA 3: Aguardar formulário de edição abrir
                with TimeoutDetector("Aguardar formulário", max_seconds=10, job_id=carga.numero_lt):
                    expect(page.get_by_role("textbox", name="Placa principal")).to_be_visible(timeout=10000)

                break
            except TimeoutError as e:
                if tentativa < 2:
                    logger.warning(
                        f"[Worker Conferência] Formulário não abriu (tentativa {tentativa}/2). Recarregando..."
                    )
                    page.reload(wait_until="networkidle")
                    continue
                raise

    except TimeoutError as e:
        motivo = f"Não foi possível encontrar ou clicar no botão de edição para a LT {carga.numero_lt}."
        logger.error(f"[Worker Conferência] {motivo} Detalhe: {e}")
        return {"status": "falha_rpa", "motivo": motivo}

    try:
        # ETAPA 4: Preenchimento do formulário
        
        # Placa Principal
        with TimeoutDetector("Preencher Placa Principal", max_seconds=15, job_id=carga.numero_lt):
            try:
                if not carga.placa: raise ValueError("Placa principal não fornecida.")
                principal_input = page.get_by_role("textbox", name="Placa principal")
                principal_input.fill("")
                principal_input.type(carga.placa, delay=50)
                page.get_by_role("option", name=carga.placa).click(timeout=7000)
            except (TimeoutError, ValueError):
                return cancelar_e_sair(campo="Placa Principal", valor=carga.placa)

        # Placa Secundária
        if carga.perfil == "CARRETA":
            with TimeoutDetector("Preencher Placa Secundária", max_seconds=20, job_id=carga.numero_lt):
                try:
                    if not carga.placa2: raise ValueError("Perfil CARRETA exige placa2.")
                    if page.get_by_role("textbox", name="Placas").input_value() != carga.placa2:
                        page.get_by_role("textbox", name="Placas").click()
                        expect(page.get_by_role("textbox", name="Placa", exact=True)).to_be_visible()
                        placa2_input = page.get_by_role("textbox", name="Placa", exact=True)
                        placa2_input.fill("")
                        placa2_input.type(carga.placa2, delay=50)
                        page.get_by_role("option", name=carga.placa2).click(timeout=120000)
                        page.get_by_role("button", name="Salvar").click()
                except (TimeoutError, ValueError):
                    return cancelar_e_sair(campo="Placa Secundária", valor=carga.placa2)

        # Expedidor
        with TimeoutDetector("Preencher Expedidor", max_seconds=20, job_id=carga.numero_lt):
            try:
                expedidor_input = page.get_by_role("textbox", name="Expedidor")
                expedidor_input.fill("")
                expedidor_input.type(carga.origem, delay=50)
                if not escolher_opcao_mais_parecida(page, carga.origem): # Tentativa 1
                    logger.warning(f"[Worker Conferência] Primeira tentativa de 'Expedidor' falhou. Tentando nome limpo.")
                    expedidor_input.fill("")
                    nome_limpo = carga.origem.rsplit("_")[-1].rsplit("-")[-1].strip()
                    expedidor_input.type(nome_limpo, delay=50)
                    if not escolher_opcao_mais_parecida(page, nome_limpo): # Tentativa 2
                        raise ValueError("Opção de expedidor não encontrada após 2 tentativas.")
            except (TimeoutError, ValueError) as e:
                return cancelar_e_sair(campo="Expedidor", valor=carga.origem)

        # Tomador
        with TimeoutDetector("Preencher Tomador", max_seconds=20, job_id=carga.numero_lt):
            try:
                tomador_input = page.get_by_role("textbox", name="Tomador")
                tomador_input.fill("")
                tomador_input.type(carga.origem, delay=50)
                if not escolher_opcao_mais_parecida(page, carga.origem): # Tentativa 1
                    logger.warning(f"[Worker Conferência] Primeira tentativa de 'Tomador' falhou. Tentando nome limpo.")
                    tomador_input.fill("")
                    nome_limpo = carga.origem.rsplit("_")[-1].rsplit("-")[-1].strip()
                    tomador_input.type(nome_limpo, delay=50)
                    if not escolher_opcao_mais_parecida(page, nome_limpo): # Tentativa 2
                        raise ValueError("Opção de tomador não encontrada após 2 tentativas.")
            except (TimeoutError, ValueError) as e:
                return cancelar_e_sair(campo="Tomador", valor=carga.origem)

        # Recebedor
        with TimeoutDetector("Preencher Recebedor", max_seconds=20, job_id=carga.numero_lt):
            try:
                recebedor_input = page.get_by_role("textbox", name="Recebedor")
                recebedor_input.fill("")
                recebedor_input.type(carga.destino, delay=50)
                if not escolher_opcao_mais_parecida(page, carga.destino): # Tentativa 1
                    logger.warning(f"[Worker Conferência] Primeira tentativa de 'Recebedor' falhou. Tentando nome limpo.")
                    recebedor_input.fill("")
                    nome_limpo = carga.destino.rsplit("_")[-1].rsplit("-")[-1].strip()
                    recebedor_input.type(nome_limpo, delay=50)
                    if not escolher_opcao_mais_parecida(page, nome_limpo): # Tentativa 2
                        raise ValueError("Opção de recebedor não encontrada após 2 tentativas.")
            except (TimeoutError, ValueError) as e:
                return cancelar_e_sair(campo="Recebedor", valor=carga.destino)
        
        # Motorista
        with TimeoutDetector("Preencher Motorista", max_seconds=15, job_id=carga.numero_lt):
            try:
                if not carga.motorista: raise ValueError("Motorista não fornecido.")
                motorista_input = page.get_by_role("textbox", name="Motoristas")
                motorista_input.fill("")
                motorista_input.type(carga.motorista, delay=50)
                page.get_by_role("option").first.click(timeout=7000)
            except (TimeoutError, ValueError):
                return cancelar_e_sair(campo="Motorista", valor=carga.motorista)
        
        # --- (Restante do preenchimento do formulário) ---
        with TimeoutDetector("Preencher campos restantes", max_seconds=25, job_id=carga.numero_lt):
            
            # ATUALIZADO: Usando label para Tipos de CT-e
            if not selecionar_mui_select_por_label(page, "Tipos de CT-e a Gerar\*", "Redespacho Intermediário", timeout=8000):
                logger.error(f"[Worker Conferência] Opção 'Redespacho Intermediário' não encontrada - LT {carga.numero_lt}.")

            # ATUALIZADO: Usando label para Tipo de Tomador
            if not selecionar_mui_select_por_label(page, "Tipo de Tomador", "Remetente", timeout=8000):
                logger.error(f"[Worker Conferência] Opção 'Remetente' não encontrada - LT {carga.numero_lt}.")

            total = carga.frete + carga.pedagio
            valor_ciot = total - 100
            valor_formatado = f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            valor_ciot_formatado = f"{valor_ciot:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            page.locator("div").filter(has_text=re.compile(r"^R\$Valor$")).get_by_placeholder("0,00").fill(valor_formatado)
            page.locator("div").filter(has_text=re.compile(r"^R\$Valor CIOT$")).get_by_placeholder("0,00").fill(valor_ciot_formatado)
            ciot_input = page.locator("input[name=\"percAdiantamentoCiot\"]")
            if ciot_input.is_enabled():
                ciot_input.click()
                ciot_input.type("70,00")
            else:
                motivo = "Campo CIOT desabilitado (provavel campo obrigatorio nao preenchido)"
                logger.error(f"[Worker Conferência] {motivo} - LT {carga.numero_lt}.")
                return cancelar_e_sair(campo="CIOT", valor=motivo, tipo_erro="falha_rpa")
            
            page.get_by_role("checkbox", name="Emitir Averbação").uncheck()

            # ATUALIZADO: Usando label para Geração MDF-e
            opcao_gerar = "Gera vinculado à CT-e emitido" if carga.status not in ["ENTREGA FINALIZADA", "AGUARDANDO DESCARGA"] else "Não gera"
            if not selecionar_mui_select_por_label(page, "Geração MDF-e", opcao_gerar, timeout=8000):
                logger.error(f"[Worker Conferência] Opção '{opcao_gerar}' não encontrada - LT {carga.numero_lt}.")

            page.get_by_role("textbox", name="Número DT").fill(carga.numero_lt)

            page.get_by_role("button", name="Line Haul").click()
            if not clicar_option(page, "Line Haul", exact=True, timeout=8000):
                logger.error(f"[Worker Conferência] Opção 'Line Haul' não encontrada - LT {carga.numero_lt}.")

            transportadora_input = page.get_by_role("textbox", name="Transportadora*")
            transportadora_input.clear()
            transportadora_input.type("3ZX", delay=50)
            if not clicar_option(page, "34.790.798/0001-34 - 3ZX SP", exact=True, timeout=12000):
                logger.error(f"[Worker Conferência] Opção transportadora 3ZX não encontrada - LT {carga.numero_lt}.")

            tipo_veiculo_input = page.get_by_role("textbox", name="Tipo de Veículo")
            tipo_veiculo_input.clear()
            tipo_veiculo_input.type(carga.perfil, delay=50)
            if not clicar_option(page, carga.perfil, exact=True, timeout=12000):
                logger.error(f"[Worker Conferência] Opção de perfil de veículo '{carga.perfil}' não encontrada - LT {carga.numero_lt}.")
        # --- (Fim do preenchimento) ---

        # ETAPA 5: Finalização
        with TimeoutDetector("Submeter formulário EmiteAí", max_seconds=30, job_id=carga.numero_lt):
            page.get_by_role("button", name="EmiteAí").click()
            time.sleep(2)
            page.get_by_role("button", name="Sim").click()
        time.sleep(5) # Espera o processamento
        
        logger.success(f"[Worker Conferência] Conferência da LT {carga.numero_lt} concluída com sucesso (RPA).")
        return {"status": "sucesso"}

    except Exception as e:
        # Pega qualquer erro não esperado durante o preenchimento
        return cancelar_e_sair(campo="Preenchimento", valor=str(e), tipo_erro="falha_rpa")