## Visão Geral do Projeto

O Jarvis é um bot de Telegram desenvolvido em Python, com um transporte opcional para WhatsApp, projetado para simplificar a gestão financeira pessoal. Diferente de aplicativos tradicionais que exigem inserção manual de dados em formulários, o Jarvis utiliza LLM para interpretar linguagem natural.

O sistema é capaz de processar entradas multimodais (texto, áudio e imagem), convertendo dados não estruturados em registros financeiros organizados em um banco de dados relacional.

O canal padrão é Telegram (`JARVIS_CHANNEL=telegram`), usando o token existente em `TELEGRAM_TOKEN`. A opção WhatsApp (`JARVIS_CHANNEL=whatsapp`) usa um número dedicado conectado via [Evolution API](https://github.com/EvolutionAPI/evolution-api). Cada implantação usa um único canal.

## Arquitetura do Sistema

O projeto segue uma arquitetura baseada em eventos, onde o bot atua como interface entre o usuário e os serviços de processamento.

### Fluxo de Dados

1. **Entrada:** O usuário envia uma mensagem via Telegram ou WhatsApp. Pode ser texto ("Gastei 50 reais no almoço"), uma foto de um comprovante fiscal ou um áudio descrevendo um gasto.
2. **Processamento (Backend Python):** No Telegram, o bot recebe mensagens privadas por long polling. No WhatsApp, a Evolution API entrega o evento `messages.upsert` no webhook Flask, que responde 200 na hora e processa a mensagem em uma thread separada.
3. **Interpretação (AI Layer):**
* Se for **Texto**, é enviado ao modelo configurado em `OPENROUTER_MODEL` via OpenRouter.
* Se for **Imagem**, o arquivo é baixado temporariamente em memória (bytes) e enviado ao modelo com visão.
* Se for **Áudio**, os bytes são enviados em base64 como `input_audio` ao OpenRouter.
* O modelo analisa o conteúdo e retorna um JSON estruturado contendo: `valor`, `categoria`, `descrição`, `método de pagamento` e `tipo` (Entrada/Saída).


4. **Persistência:** Os dados estruturados são validados e salvos em um banco de dados no NeonDB utilizando o ORM SQLAlchemy.
5. **Visualização:** Quando solicitado, o sistema consulta o banco de dados, utiliza a biblioteca Pandas para manipular os dados e Matplotlib para gerar gráficos estáticos, que são enviados de volta ao usuário como imagem.

## Stacks

As tecnologias foram escolhidas visando baixo custo de operação, facilidade de manutenção e alta performance para um usuário único.

* **Linguagem:** Python
* **Interface:** Telegram (long polling); WhatsApp opcional via Evolution API (webhook Flask)
* **Inteligência Artificial:** OpenRouter (modelo configurável por variável de ambiente)
* **Banco de Dados:** NeonDB
* **ORM:** SQLAlchemy
* **Análise de Dados:** Pandas e Matplotlib

## Funcionalidades Principais

### 1. Registro Multimodal

O sistema aceita inputs variados, reduzindo o atrito do usuário ao registrar gastos. A IA é instruída via *System Prompt* a extrair sempre os mesmos campos chaves, independentemente do formato da entrada.

### 2. Dashboard Mensal Dinâmico

O bot gera relatórios visuais sob demanda. A lógica de filtragem foi implementada para isolar transações baseadas no mês e ano correntes, garantindo que o usuário tenha uma visão atualizada da sua saúde financeira.

### 3. Compras Parceladas

Ao registrar algo como "Comprei uma TV em 10x de 200", o sistema agenda todas as parcelas sem somar nada aos gastos do mês da compra — a 1ª parcela só é registrada quando a fatura do cartão fecha (dia configurável com `/fatura [dia]`; sem configuração, assume um mês após a compra). A cada mês, a parcela do período é registrada automaticamente e o usuário recebe um aviso no WhatsApp. Os comandos `/parcelas` e `/cancelarparcela` listam e cancelam parcelamentos ativos.

### 4. Sistema de Metas e Alertas

O usuário pode definir tetos de gastos por categoria. O sistema verifica, a cada nova transação de saída, se o valor acumulado no mês ultrapassou a meta definida, emitindo alertas proativos.

### 5. Exportação de Dados

Para análises mais profundas, o sistema permite a exportação integral do banco de dados para um arquivo Excel (.xlsx), formatado automaticamente com colunas de data e valores monetários.

## Assistente pessoal: lembretes, calendário e resumo diário

Os lembretes e as preferências ficam no banco, em tabelas criadas automaticamente na inicialização. Não é necessário conectar um calendário para usar lembretes e resumo financeiro diário.

### Menu de comandos

Envie `/`, `/menu` ou `/comandos` para receber todos os comandos, agrupados por função e com os argumentos necessários. Envie um prefixo, como `/ca`, para receber as opções correspondentes. Os atalhos `/ajuda`, `/resumo` e `/exportar` também estão disponíveis. O menu não chama a IA. No Telegram, a inicialização registra o catálogo em `setMyCommands` e habilita o botão de comandos: as sugestões aparecem ao digitar `/`, antes de enviar. `/meuid` informa seu identificador numérico para configurar o calendário e a lista de usuários permitidos.

No WhatsApp, o menu aparece **depois de enviar a mensagem**: esta integração recebe mensagens enviadas, sem acesso ao texto que ainda está sendo digitado. As sugestões automáticas no campo de digitação são um recurso nativo de bots do Telegram e não estão disponíveis por esta integração do WhatsApp.

### Lembretes

- Diga: `Me lembra de pagar a internet amanhã às 9h` ou `Me lembra de pagar o aluguel todo dia 5 às 9h`.
- Repetições: diária, semanal, mensal e dias úteis. Informe sempre o horário.
- `/lembretes`: lista os números dos lembretes ativos.
- `/adiar ID MINUTOS`: adia o próximo aviso; em um lembrete recorrente, esse novo horário passa a ser a base das próximas ocorrências.
- `/concluir ID` ou `/cancelarlembrete ID`: encerra o lembrete, inclusive todas as próximas ocorrências de um recorrente.
- `/fuso America/Sao_Paulo`: configura o fuso do usuário. Lembretes já criados mantêm o fuso original. O padrão vem de `JARVIS_TIMEZONE`.

Pedidos incompletos exigem o reenvio do pedido completo com as informações faltantes. Os lembretes são avisos: um lembrete de pagamento não cria uma transação financeira.

### Calendário do iPhone / iCloud

É possível ler e criar compromissos no calendário iCloud por CalDAV. Eles aparecem no iPhone quando a sincronização do calendário iCloud está ativada. Uma agenda salva apenas no aparelho precisa ser transferida para uma conta sincronizada primeiro. Esta integração não acessa o aplicativo Lembretes da Apple: os lembretes do Jarvis chegam pelo chat do canal ativo.

1. Ative a sincronização do Calendário com iCloud no iPhone e confirme que a agenda desejada aparece no iCloud.
2. Com autenticação de dois fatores na conta Apple, gere uma **senha específica de app** em [account.apple.com](https://account.apple.com/). Consulte as [instruções da Apple](https://support.apple.com/pt-br/102654).
3. No `.env` do servidor, configure:

```dotenv
CALENDAR_PROVIDER=icloud
CALENDAR_USER_ID=seu-id-numerico-do-telegram
ICLOUD_USERNAME=seu-email-da-conta-apple
ICLOUD_APP_PASSWORD=senha-especifica-de-app
ICLOUD_CALENDAR_NAME=Pessoal
JARVIS_TIMEZONE=America/Sao_Paulo
```

4. Faça o redeploy do serviço no Render (ou recrie o container, se usar Docker). Envie `/calendarios` pelo usuário definido em `CALENDAR_USER_ID` para descobrir os nomes. Ajuste `ICLOUD_CALENDAR_NAME` com o nome exato de uma única agenda e reinicie o serviço se necessário.
5. Consulte `/agenda` ou pergunte `Qual minha agenda de amanhã?`. Diga `Marca dentista amanhã das 15h às 16h e avisa uma hora antes` para preparar um evento. Confira os horários e use `/confirmar ID` para criar ou `/descartar ID` para desistir. O pedido expira após 15 minutos.

O Jarvis consulta conflitos antes de pedir confirmação. A confirmação permite criar mesmo com conflito. Nesta versão, consulta e criação usam somente a agenda escolhida; edição, exclusão e convite de participantes não estão incluídos. Compromissos existentes também geram avisos no canal ativo 30 minutos antes; eventos criados pelo Jarvis usam a antecedência pedida. Eventos de dia inteiro entram na agenda e no resumo, sem aviso automático no chat.

Consultas simples como `O que tenho para fazer sexta?`, `Qual minha agenda de amanhã?` e `Agenda desta semana` são resolvidas localmente, sem chamada à IA nem consulta ao histórico financeiro. Datas relativas usam o fuso do usuário; `sexta` inclui hoje quando hoje é sexta, enquanto `próxima sexta` indica a seguinte. Pedidos mais complexos continuam disponíveis pela IA. Os eventos são consultados no calendário a cada pedido.

A agenda apresenta o dia da semana e a data, com uma linha por compromisso, horários destacados e agrupamento por dia quando o período é maior. O horário técnico `00:00` do intervalo de busca não aparece no cabeçalho. No Telegram, o negrito é enviado como entidades de formatação, sem exibir os asteriscos.

As credenciais são configuradas no servidor, nunca pelo chat ou em prompts da IA. A integração fica disponível somente ao usuário `CALENDAR_USER_ID`, mesmo se a lista geral de usuários permitidos estiver vazia. No Telegram, use o ID retornado por `/meuid`; no WhatsApp, use o telefone com DDI+DDD+número. Não publique a agenda nem use a senha principal da Apple.

### Google Calendar (alternativa)

1. Ative a Google Calendar API em um projeto Google Cloud, configure a tela de consentimento e crie credenciais OAuth do tipo **Desktop**. Se o aplicativo estiver em teste, adicione sua conta como usuário de teste.
2. Configure `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` no `.env`. No seu computador, com as dependências instaladas, execute `python configure_google_calendar.py`. Autorize no navegador do mesmo computador; o retorno usa `127.0.0.1`. O script salva `GOOGLE_REFRESH_TOKEN` diretamente no `.env`, sem imprimir o token.
3. Configure `CALENDAR_PROVIDER=google`, `CALENDAR_USER_ID` com o ID de `/meuid` e `GOOGLE_CALENDAR_ID=primary` (ou o ID de outra agenda da sua conta). Leve as variáveis para o servidor e faça o redeploy.

O escopo solicitado é `calendar.events`, para consultar e criar eventos. O servidor renova o access token automaticamente. Tokens de aplicativos externos em modo de teste podem expirar em sete dias; configure o status de publicação apropriado no Google antes de depender da integração diariamente. Consulte a [documentação OAuth](https://developers.google.com/identity/protocols/oauth2/native-app) e as [condições de expiração](https://developers.google.com/identity/protocols/oauth2#expiration). Uma conta Google também pode ser adicionada ao aplicativo Calendário do iPhone.

### Resumo diário

- `/resumodiario 08:00`: ativa no horário e fuso configurados para o usuário.
- `/resumodiario off`: desativa e cancela resumos ainda pendentes de envio.
- `/hoje`: consulta imediatamente os compromissos de hoje, lembretes pendentes, parcelas/renovações previstas para hoje e amanhã, registros financeiros do mês e valores restantes nas metas.

O envio automático começa somente após a ativação. Dias sem conteúdo ficam silenciosos. O resumo usa cálculos do banco e dados do calendário, sem uma chamada adicional à IA. Uma falha no calendário é indicada no resumo e não impede os lembretes independentes.

O processo `python run_bot.py` verifica agendamentos a cada minuto. Após reiniciar, recupera lembretes atrasados e o resumo do dia se o horário já passou; não envia resumos dos dias anteriores. Recorrências atrasadas geram um aviso e avançam para a próxima data futura, sem disparar todo o histórico. Falhas de envio ficam pendentes, com novas tentativas de um minuto até uma hora de intervalo. Avisos de calendário e resumos expiram quando deixam de ser úteis.

As chaves únicas e bloqueios de linha evitam a criação duplicada de entregas em operação normal com PostgreSQL. A entrega é **pelo menos uma vez**: se o serviço de mensagens aceitar a mensagem e a resposta se perder, uma nova tentativa pode repetir o aviso. Execute o bot pelo ponto de entrada acima, pois importar o Flask via WSGI não inicia os agendadores.

### Testes do assistente pessoal

`python -m unittest test_brain.py test_personal.py test_calendar.py test_command_menu.py test_telegram.py test_agenda.py` valida interpretação, consultas rápidas, apresentação da agenda, formatação do Telegram, agendamentos, isolamento por usuário, persistência, retentativas, menus e adaptadores de calendário com mocks. Não usa banco de produção, contas reais ou mensagens reais. A conexão real precisa ser validada após configurar as credenciais.

## Estrutura financeira do banco de dados

O banco de dados possui, principalmente, a tabela `transacoes` com a seguinte estrutura simplificada:

* **id:** Inteiro (Chave Primária)
* **user_id:** Inteiro (ID numérico do Telegram ou, no transporte WhatsApp, telefone DDI+DDD+número sem o sufixo do JID)
* **valor:** Float (Valor monetário)
* **descricao:** Texto (Detalhe do gasto)
* **categoria:** Texto (Ex: Alimentação, Transporte)
* **tipo:** Texto (Entrada ou Saída)
* **metodo_pagamento:** Texto (Ex: Crédito, Pix)
* **data:** DateTime (Timestamp do registro)

## Instalação e Execução

### Telegram no Render (configuração atual)

Use um Web Service com Build Command `pip install -r requirements.txt` e Start Command `python run_bot.py`. O Flask escuta a porta `PORT` fornecida pelo Render e o Telegram usa polling em uma thread separada. Não use `gunicorn run_bot:servidor`: essa forma não inicia o polling nem os agendadores. Execute apenas uma instância com esse token; encerre um bot local que esteja usando o mesmo token. Se houver um webhook Telegram ativo, remova-o antes de usar polling.

Mantenha `DATABASE_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` e `TELEGRAM_TOKEN` existentes. Acrescente:

```dotenv
JARVIS_CHANNEL=telegram
JARVIS_TIMEZONE=America/Sao_Paulo
```

`TELEGRAM_USER_IDS` é opcional: IDs numéricos separados por vírgula para restringir quem pode conversar com o bot. Use `/meuid` para descobrir seu ID. Para conectar calendário, configure também `CALENDAR_PROVIDER`, `CALENDAR_USER_ID` e as credenciais iCloud ou Google descritas acima. Sem calendário, deixe essas variáveis ausentes ou vazias. Depois do deploy, ative o resumo com `/resumodiario 08:00`; lembretes e resumos não precisam de outras chaves.

Um serviço gratuito do Render pode dormir após 15 minutos sem tráfego de entrada. O polling de saída não garante que o serviço permaneça acordado, e os agendadores não executam enquanto ele estiver suspenso. Para avisos pontuais, use uma instância que permaneça em execução. Veja as [limitações do plano gratuito](https://render.com/docs/free).

### WhatsApp com Docker (opcional)

O projeto roda em Docker: Evolution API, Postgres e Redis (para a sessão do WhatsApp) mais o próprio bot.

1. Clonar o repositório em um VPS com Docker.
2. Copiar `.env.example` para `.env` e preencher as variáveis (gere os segredos com `openssl rand -hex 32`).
3. Subir tudo: `docker compose up -d --build`.
4. Abrir a Evolution em `http://localhost:8080/manager` (via túnel SSH: `ssh -L 8080:localhost:8080 usuario@vps`), criar a instância com o nome de `EVOLUTION_INSTANCE` e ler o QR code com o número dedicado.
5. Mandar `/start` para o número — as tabelas do banco são criadas automaticamente.

O webhook global já aponta para `http://jarvis:8000/webhook/$WEBHOOK_TOKEN` na rede interna do compose; a porta do bot não é publicada.

### Testes

`python -m unittest test_brain.py` valida a integração OpenRouter sem rede, chave ou banco de dados.

`python test_parcelamento.py` e `python test_webhook.py` (precisam de `DATABASE_URL` no `.env`).

### OpenRouter no Render

Em **Environment** do serviço, configure `OPENROUTER_API_KEY` com sua chave e `OPENROUTER_MODEL` com o identificador completo do modelo (`provedor/modelo`). Salve as variáveis e faça o redeploy. A variável `GROQ_API_KEY` não é mais utilizada.

O mesmo modelo atende texto, imagens e áudio: escolha um que suporte as modalidades utilizadas, áudio OGG/Opus para mensagens de voz e respostas JSON. Consulte os formatos aceitos pelo modelo na [documentação de áudio do OpenRouter](https://openrouter.ai/docs/guides/overview/multimodal/audio).
