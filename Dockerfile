# =========================================================================
# OPS_ENGINE - Dockerfile Otimizado e Seguro (Non-Root User)
# =========================================================================
FROM python:3.13-slim

# Variáveis de ambiente para otimizar Python e Playwright
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers

WORKDIR /app

# 1. Cria um usuário e grupo não-root dedicado para a aplicação
# Usamos o UID 1000 para evitar conflitos de permissão nos volumes mapeados (ex: ./logs)
RUN groupadd -g 1000 opsgroup && \
    useradd -u 1000 -g opsgroup -s /bin/bash -m opsuser

# 2. Copia as dependências e instala os pacotes Python (como root)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Instala as dependências do SO, o Firefox e limpa o cache do Linux (como root)
RUN playwright install-deps firefox && \
    playwright install firefox && \
    rm -rf /var/lib/apt/lists/*

# 4. Copia os arquivos da aplicação já transferindo a posse para o 'opsuser'
COPY --chown=opsuser:opsgroup dados/ ./dados/
COPY --chown=opsuser:opsgroup fluxos/ ./fluxos/
COPY --chown=opsuser:opsgroup utils/ ./utils/
COPY --chown=opsuser:opsgroup workers/ ./workers/
COPY --chown=opsuser:opsgroup main.py poller.py writer.py ./

# 5. Cria a pasta de logs e garante que TODO o diretório /app pertence ao 'opsuser'
# Isso é crucial para o Playwright conseguir ler o Firefox e o Writer salvar arquivos de erro
RUN mkdir -p logs && \
    chown -R opsuser:opsgroup /app && \
    chmod -R 777 /app/logs

# 6. Troca definitivamente para o usuário seguro
USER opsuser

# 7. Comando padrão (O docker-compose sobrescreve isso com poller.py e writer.py)
CMD ["python", "main.py"]