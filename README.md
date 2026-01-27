# Jarvis Telegram 

Bem-vindo ao **Jarvis**! Este projeto conecta o seu Telegram à inteligência do Google Gemini. A ideia é ter um assistente pessoal capaz de "ver" suas imagens, "ouvir" seus áudios e conversar com você naturalmente, tudo direto pelo chat.

## O que ele faz?

* **Controle de Gastos:** Você envia uma mensagem falando em que você gastou e ele salva em um banco de dados.
* **Visão Computacional:** Você manda uma foto e ele analisa o que tem nela.
* **Audição:** Mandou um áudio? Ele escuta, entende e te responde (sem precisar transcrever manualmente).

---
## ☁️ Quer usar sem instalar nada?

Se você não quer rodar o código na sua máquina e só quer testar o bot funcionando agora mesmo, é só clicar no link abaixo:

👉 **[Acessar Jarvis na Nuvem](https://t.me/JarvisFinancial_Bot)**

---
## Como rodar o projeto

Siga estes passos simples para colocar o Jarvis de pé.

### 1. Preparando o ambiente
Primeiro, baixe o projeto e entre na pasta. Depois, para manter tudo organizado, crie e ative seu ambiente virtual:

**No Windows:**
```
powershell
python -m venv venv
.\venv\Scripts\activate
```
2. Instalando as dependências
Com o ambiente ativado, você só precisa rodar um comando para instalar tudo o que o robô precisa (Google Gemini, Telegram Bot, etc):
```
Bash
pip install -r requirements.txt
```
3. Configurando as chaves (Segurança)
O bot precisa das chaves de acesso para funcionar. Crie um arquivo chamado .env na raiz do projeto e cole suas credenciais lá dentro:
```
GEMINI_API_KEY=sua_chave_do_google_aqui
TELEGRAM_TOKEN=seu_token_do_telegram_aqui
```
4. Ligando o robô
Tudo pronto! Agora é só iniciar o script principal:
```
Bash
python run_bot.py
```
🛠️ Estrutura dos Arquivos
brain.py: O cérebro do bot.

run_bot.py: o coração do bot.

requirements.txt: Lista de tudo que precisa ser instalado.

.env: Onde seus segredos (senhas) ficam guardados.

Divirta-se conversando com seu novo assistente!
