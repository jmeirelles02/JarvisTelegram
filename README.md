# Jarvis Telegram 

Bem-vindo ao **Jarvis**! Este projeto conecta o seu Telegram à inteligência do Google Gemini. A ideia é ter um assistente pessoal capaz de "ver" suas imagens, "ouvir" seus áudios e conversar com você naturalmente, tudo direto pelo chat.

## O que ele faz?

* **Conversa Natural:** Bate um papo sobre qualquer assunto.
* **Visão Computacional:** Você manda uma foto e ele analisa o que tem nela.
* **Audição:** Mandou um áudio? Ele escuta, entende e te responde (sem precisar transcrever manualmente).

---

## Como rodar o projeto

Siga estes passos simples para colocar o Jarvis de pé.

### 1. Preparando o ambiente
Primeiro, baixe o projeto e entre na pasta. Depois, para manter tudo organizado, crie e ative seu ambiente virtual:

**No Windows:**
```powershell
python -m venv venv
.\venv\Scripts\activate
2. Instalando as dependências
Com o ambiente ativado, você só precisa rodar um comando para instalar tudo o que o robô precisa (Google Gemini, Telegram Bot, etc):

Bash
pip install -r requirements.txt
3. Configurando as chaves (Segurança)
O bot precisa das chaves de acesso para funcionar. Crie um arquivo chamado .env na raiz do projeto e cole suas credenciais lá dentro:

Code snippet
GEMINI_API_KEY=sua_chave_do_google_aqui
TELEGRAM_TOKEN=seu_token_do_telegram_aqui
4. Ligando o robô
Tudo pronto! Agora é só iniciar o script principal:

Bash
python main.py
🛠️ Estrutura dos Arquivos
main.py: O cérebro do bot.

list_models.py: Um script extra para você ver quais versões do Gemini estão disponíveis na sua conta.

requirements.txt: Lista de tudo que precisa ser instalado.

.env: Onde seus segredos (senhas) ficam guardados.

Divirta-se conversando com seu novo assistente!
