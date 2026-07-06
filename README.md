## Visão Geral do Projeto

O Jarvis é um bot de Telegram desenvolvido em Python, projetado para simplificar a gestão financeira pessoal. Diferente de aplicativos tradicionais que exigem inserção manual de dados em formulários, o Jarvis utiliza LLM para interpretar linguagem natural.

O sistema é capaz de processar entradas multimodais (texto, áudio e imagem), convertendo dados não estruturados em registros financeiros organizados em um banco de dados relacional.

**[Acessar Jarvis na Nuvem](https://t.me/JarvisFinancial_Bot)**

## Arquitetura do Sistema

O projeto segue uma arquitetura baseada em eventos, onde o bot atua como interface entre o usuário e os serviços de processamento.

### Fluxo de Dados

1. **Entrada:** O usuário envia uma mensagem via Telegram. Pode ser texto ("Gastei 50 reais no almoço"), uma foto de um comprovante fiscal ou um áudio descrevendo um gasto.
2. **Processamento (Backend Python):** O script principal recebe o objeto `Update` da API do Telegram.
3. **Interpretação (AI Layer):**
* Se for **Texto**, é enviado diretamente ao modelo de linguagem (Llama 4 Scout via Groq).
* Se for **Imagem**, o arquivo é baixado temporariamente em memória (bytes) e enviado ao modelo com visão.
* Se for **Áudio**, ele é transcrito pelo Whisper (via Groq) e o texto resultante segue o fluxo normal.
* O modelo analisa o conteúdo e retorna um JSON estruturado contendo: `valor`, `categoria`, `descrição`, `método de pagamento` e `tipo` (Entrada/Saída).


4. **Persistência:** Os dados estruturados são validados e salvos em um banco de dados no NeonDB utilizando o ORM SQLAlchemy.
5. **Visualização:** Quando solicitado, o sistema consulta o banco de dados, utiliza a biblioteca Pandas para manipular os dados e Matplotlib para gerar gráficos estáticos, que são enviados de volta ao usuário como imagem.

## Stacks

As tecnologias foram escolhidas visando baixo custo de operação, facilidade de manutenção e alta performance para um usuário único.

* **Linguagem:** Python
* **Interface:** Telegram Bot API
* **Inteligência Artificial:** Llama 4 Scout + Whisper (via Groq)
* **Banco de Dados:** NeonDB
* **ORM:** SQLAlchemy
* **Análise de Dados:** Pandas e Matplotlib

## Funcionalidades Principais

### 1. Registro Multimodal

O sistema aceita inputs variados, reduzindo o atrito do usuário ao registrar gastos. A IA é instruída via *System Prompt* a extrair sempre os mesmos campos chaves, independentemente do formato da entrada.

### 2. Dashboard Mensal Dinâmico

O bot gera relatórios visuais sob demanda. A lógica de filtragem foi implementada para isolar transações baseadas no mês e ano correntes, garantindo que o usuário tenha uma visão atualizada da sua saúde financeira.

### 3. Compras Parceladas

Ao registrar algo como "Comprei uma TV em 10x de 200", o sistema grava a primeira parcela e agenda as demais: todo mês a parcela do período é registrada automaticamente e o usuário recebe um aviso no Telegram. Os comandos `/parcelas` e `/cancelarparcela` listam e cancelam parcelamentos ativos.

### 4. Sistema de Metas e Alertas

O usuário pode definir tetos de gastos por categoria. O sistema verifica, a cada nova transação de saída, se o valor acumulado no mês ultrapassou a meta definida, emitindo alertas proativos.

### 5. Exportação de Dados

Para análises mais profundas, o sistema permite a exportação integral do banco de dados para um arquivo Excel (.xlsx), formatado automaticamente com colunas de data e valores monetários.

## Estrutura do Banco de Dados

O banco de dados possui, principalmente, a tabela `transacoes` com a seguinte estrutura simplificada:

* **id:** Inteiro (Chave Primária)
* **user_id:** Inteiro (Identificador do Telegram)
* **valor:** Float (Valor monetário)
* **descricao:** Texto (Detalhe do gasto)
* **categoria:** Texto (Ex: Alimentação, Transporte)
* **tipo:** Texto (Entrada ou Saída)
* **metodo_pagamento:** Texto (Ex: Crédito, Pix)
* **data:** DateTime (Timestamp do registro)

## Instalação e Execução

O projeto utiliza um ambiente virtual para gerenciamento de dependências.

1. Clonar o repositório.
2. Criar o arquivo .env na raiz do projeto.
3. Configurar as variáveis de ambiente no arquivo `.env` (`TELEGRAM_TOKEN`, `GROQ_API_KEY` e `DATABASE_URL`).
4. Instalar dependências: `pip install -r requirements.txt`.
5. Executar o bot: `python run_bot.py` (as tabelas do banco são criadas automaticamente).
