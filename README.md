## Visão Geral do Projeto

O Jarvis é um assistente pessoal no Telegram desenvolvido em Python, com um transporte opcional para WhatsApp. Usa um modelo de linguagem para conversar com contexto, organizar finanças e agenda, lembrar de preferências e avaliar quando vale a pena enviar uma sugestão espontânea.

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

O comando `/resumo` envia uma leitura curta dos registros do mês e um painel em formato retrato. O período respeita o fuso configurado pelo usuário, inclusive nas viradas de mês.

O painel mostra entradas, saídas e resultado; a participação dos gastos por categoria; a evolução acumulada por dia; os limites de gastos com valores disponíveis ou excedidos; e a composição por forma de pagamento e origem das entradas. Títulos e legendas explicam o que cada gráfico mede. Categorias adicionais são agrupadas, preservando os totais. O resultado representa entradas menos saídas registradas, e não um saldo bancário consultado.

### Padrão das respostas

Confirmações, listas, lembretes, avisos, comandos, erros e o resumo diário usam títulos curtos, espaços entre assuntos e uma ação clara quando necessária. Valores seguem o formato `R$ 1.234,56`, com nomes legíveis como Alimentação e Crédito. O resumo diário separa agenda, lembretes, cobranças, finanças e metas. As conversas geradas pela IA também recebem instruções para seguir esse estilo, sem tabelas ou excesso de emojis.

### Continuidade da conversa

O Jarvis envia à IA o histórico recente do usuário, incluindo as respostas efetivamente enviadas. Assim, um pedido pode ser completado em mensagens separadas: `Me lembra de pagar a internet amanhã` → `A que horas?` → `Às 9h`. O prompt orienta o modelo a perguntar apenas pelo detalhe que falta, acompanhar mudanças de assunto e ajudar com planejamento e dúvidas sem exigir comandos.

O histórico textual fica na tabela `mensagens_conversa`, criada automaticamente na inicialização, e sobrevive ao reinício do bot. Cada usuário tem até 24 mensagens, de até 2.000 caracteres cada; o contexto enviado tem no máximo 18.000 caracteres de histórico. Mensagens com mais de 24 horas deixam de ser usadas e são apagadas na próxima gravação. Os bytes de fotos e áudios não são armazenados nesse histórico; uma mídia que ainda dependa de interpretação pode precisar ser reenviada. O histórico recente é enviado ao modelo configurado no OpenRouter junto com a nova mensagem.

Atalhos locais correspondem a pedidos completos, como `resumo` ou `me dê um resumo`, quando não há histórico recente. Com histórico, a IA interpreta o texto para que respostas como `hoje` possam completar um pedido anterior. Uma palavra isolada dentro de outra frase, como `não quero resumo`, não aciona o relatório. Comandos explícitos como `/resumo` e `/exportar` continuam disponíveis sem IA. Consultas completas de agenda reconhecidas localmente também mantêm o caminho direto. Pedidos do mesmo usuário são processados em sequência dentro do processo do bot para preservar a ordem da conversa.

Além desse histórico temporário, o Jarvis guarda memórias explícitas sobre preferências, rotina e objetivos na tabela `memorias_pessoais`. São até 20 informações por usuário, de até 500 caracteres cada, sem expiração automática. Cada informação precisa ser um trecho literal da mensagem atual; o sistema rejeita trechos que o modelo inventar. O modelo é orientado a selecionar apenas afirmações pessoais úteis e a não guardar hipóteses, perguntas ou credenciais. Correções usam a mesma chave e objetivos podem ser marcados como concluídos. `/memorias` mostra essas informações; `/esquecer ID` apaga uma delas, limpa o histórico recente e cancela sugestões pendentes para que o contexto antigo não seja reaproveitado.

Nas conversas, a IA recebe essas memórias, lembretes ativos, dados financeiros e, quando conectado, o calendário das próximas 24 horas. Uma falha no calendário é indicada como indisponibilidade, sem concluir que a agenda está livre. A interpretação e as relações entre esses dados dependem do modelo escolhido. Ações concluídas continuam sendo confirmadas pelos serviços, e a criação de compromissos mantém uma prévia para confirmação por conversa ou `/confirmar ID`.

### Iniciativa do assistente

Após o primeiro contato de cada usuário, o Jarvis habilita sugestões espontâneas no perfil persistente `perfis_assistente`. Uma thread independente verifica a possibilidade de análise a cada minuto, sem bloquear o recebimento de mensagens ou o disparo de lembretes. Cada usuário pode ser analisado no máximo uma vez a cada 30 minutos. Se não houver sinais relevantes, não há chamada à IA.

Os sinais disponíveis são conflitos entre compromissos nas próximas 24 horas, cobranças previstas nas próximas 24 horas, gastos registrados que chegam a 80% ou 100% de um limite configurado, lembretes enviados há pelo menos duas horas que ainda aguardam conclusão e objetivos explícitos sem atualização há pelo menos três dias. O Jarvis não presume que um lembrete sem confirmação esteja realmente pendente fora da conversa. Objetivos concluídos deixam de gerar sinais.

A IA recebe os sinais, as memórias e a conversa recente para decidir se deve ficar em silêncio ou escolher um assunto e sugerir um próximo passo. O aviso combina o fato calculado pelos serviços com uma sugestão curta do modelo. Isso permite relacionar situações à rotina da pessoa, dentro dos assuntos para os quais existem dados. O assistente não busca notícias nem acompanha fontes externas além do calendário conectado.

Por padrão, as sugestões só chegam entre **08:00 e 21:00 no fuso do usuário**, com no máximo **duas por dia** e pelo menos **quatro horas de intervalo**. Ele também espera dez minutos após uma mensagem do usuário para não interromper uma conversa em curso. O mesmo sinal tem uma chave persistente para evitar repetições após reiniciar. Há retentativas em caso de falha de envio e os fatos são verificados novamente antes da entrega; sugestões vencidas ou sem fundamento atual são descartadas. Uma falha de sincronização do calendário suspende a entrega de sugestões sobre conflitos.

Esses horários e limites se aplicam às sugestões espontâneas. Lembretes solicitados, avisos de calendário, recorrências financeiras e resumo diário continuam com suas configurações próprias. `/proatividade` mostra o estado, `/proatividade off` desliga as sugestões e `/proatividade on` religa. Também é possível conversar: `Só me mande sugestões entre 10h e 19h, no máximo uma por dia` ou `Não quero receber sugestões espontâneas`.

As tabelas novas são criadas pela inicialização normal; é necessário reiniciar ou reimplantar o bot para ativar o código. A avaliação depende de `OPENROUTER_API_KEY` e `OPENROUTER_MODEL`, e os dados de contexto e memórias são enviados ao modelo configurado. Sem essa configuração, a iniciativa fica em silêncio e os comandos locais continuam disponíveis.

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
5. Consulte `/agenda` ou pergunte `Qual minha agenda de amanhã?`. Diga `Marca dentista amanhã das 15h às 16h e avisa uma hora antes` para preparar um evento. Confira os horários e responda `pode criar` ou `pode descartar`; os comandos `/confirmar ID` e `/descartar ID` também funcionam. Uma resposta curta só age sobre o pedido exibido na última resposta. O pedido expira após 15 minutos.

O Jarvis consulta conflitos antes de pedir confirmação. A confirmação permite criar mesmo com conflito. Consulta e criação usam somente a agenda escolhida. No iCloud, a conversa também permite mover e cancelar ocorrências existentes, com as limitações descritas abaixo. Convites de participantes não são gerenciados. Compromissos existentes também geram avisos no canal ativo 30 minutos antes; eventos criados pelo Jarvis usam a antecedência pedida. Eventos de dia inteiro entram na agenda e no resumo, sem aviso automático no chat.

A criação no iCloud usa um caminho estável por UID, consulta por GET e gravação por PUT com `If-None-Match: *`. Isso preserva a proteção contra duplicação/sobrescrita sem depender das consultas CalDAV REPORT por UID realizadas por `get_event_by_uid` e `save_event(no_overwrite=True)`. A leitura de agenda e a verificação de conflitos continuam usando consultas por período. Falhas de autenticação, rede e consultas REPORT têm mensagens distintas; um REPORT rejeitado não é apresentado automaticamente como credencial inválida. Os logs registram operação, tipo de erro e código HTTP quando disponível, sem incluir o corpo dos eventos ou credenciais.

#### Mudanças de agenda pelo contexto

O Jarvis interpreta se a pessoa está comunicando uma remarcação, um adiamento, uma antecipação ou um cancelamento. Exemplos: `Troquei meu psicólogo de dia essa semana, para quinta às 10h`, `Minha consulta ficou uma hora mais tarde` e `Consegui antecipar em meia hora`. O primeiro passo identifica a necessidade de consultar a agenda; um segundo planejamento recebe os compromissos reais do período, o fuso e a conversa recente para relacionar a frase ao evento certo. A busca padrão cobre esta semana, e pedidos sobre outras datas podem fornecer outro intervalo, de até 31 dias.

Quando a mudança é clara e identifica uma ocorrência, o serviço executa e só então confirma o resultado. Mantém a duração existente; não exige que o usuário repita o título, o horário original ou a duração quando esses dados estão na agenda. Se faltar um detalhe, houver dois compromissos plausíveis ou o novo horário estiver ocupado, pergunta pelo que falta. Hipóteses, mudanças fixas na rotina e alterações da série inteira não são tratadas como uma alteração isolada. O executor atual suporta uma ocorrência por operação; alterações de todas as semanas ou de todas as próximas ainda exigem o aplicativo do calendário.

No iCloud, uma remarcação recorrente grava uma exceção com o mesmo UID e o `RECURRENCE-ID` do horário original. Não exclui a série e não muda a regra semanal: a sexta desta semana pode passar à quinta, enquanto as outras sextas permanecem iguais. Cancelar uma ocorrência usa uma exceção com `STATUS:CANCELLED`. A gravação ocorre uma vez no recurso original, condicionada ao ETag (`If-Match`). Se outra pessoa ou aplicativo modificar o evento no intervalo, a gravação é recusada e o Jarvis pede uma nova consulta. Retentativas da mesma operação têm um identificador no recurso. Eventos com participantes e eventos de dia inteiro não são alterados por esse executor.

#### Diagnóstico CalDAV

`/diagnosticocalendario` faz apenas leituras no iCloud e separa conexão, consulta por período e busca por UID. Não cria nem modifica compromissos. Se a busca da biblioteca por período produzir `ReportError`, a leitura tenta um REPORT mínimo com intervalo de tempo e expande as recorrências localmente. Se essa consulta também for recusada, o erro informa seu código HTTP. Uma falha antiga registrada apenas como `ReportError` não permite concluir retroativamente qual resposta o servidor deu; o diagnóstico precisa ser executado na instância configurada para verificar o problema atual.

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

`python -m unittest test_brain.py test_personal.py test_calendar.py test_command_menu.py test_telegram.py test_agenda.py test_reports.py` valida interpretação, consultas rápidas, apresentação das respostas, formatação do Telegram, cálculos e renderização do resumo, agendamentos, isolamento por usuário, persistência, retentativas, menus e adaptadores de calendário com mocks. Não usa banco de produção, contas reais ou mensagens reais. A conexão real precisa ser validada após configurar as credenciais.

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

`python -m unittest test_assistant test_conversation test_proactivity` valida memória, continuidade, seleção de avisos, horários, limites, deduplicação, correções de contexto e retentativas com SQLite em memória e IA/transporte simulados. Nenhuma mensagem real é enviada.

`python -m unittest test_calendar_changes test_agenda_reasoning` verifica a preservação das outras semanas, exceções existentes, duração, alarmes, horário de verão, gravação condicional e execução de planos de mudança. O planejador é simulado nesses testes; a qualidade de interpretação do modelo escolhido precisa ser validada com ele configurado.

`python test_parcelamento.py` e `python test_webhook.py` (precisam de `DATABASE_URL` no `.env`).

### OpenRouter no Render

Em **Environment** do serviço, configure `OPENROUTER_API_KEY` com sua chave e `OPENROUTER_MODEL` com o identificador completo do modelo (`provedor/modelo`). Salve as variáveis e faça o redeploy. A variável `GROQ_API_KEY` não é mais utilizada.

O mesmo modelo atende texto, imagens e áudio: escolha um que suporte as modalidades utilizadas, áudio OGG/Opus para mensagens de voz e respostas JSON. Consulte os formatos aceitos pelo modelo na [documentação de áudio do OpenRouter](https://openrouter.ai/docs/guides/overview/multimodal/audio).
