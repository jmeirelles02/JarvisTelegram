import base64
import hashlib
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv, set_key


def main():
    load_dotenv()
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise SystemExit("Preencha GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET no .env com credenciais OAuth de aplicativo Desktop.")
    estado = secrets.token_urlsafe(32)
    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    resultado = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            parametros = parse_qs(urlparse(self.path).query)
            valido = parametros.get("state", [""])[0] == estado
            if valido:
                resultado.update({chave: valores[0] for chave, valores in parametros.items()})
            self.send_response(200 if valido else 400)
            self.end_headers()
            self.wfile.write(b"Autorizacao recebida. Volte ao terminal." if valido else b"Resposta invalida.")

        def log_message(self, formato, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Callback) as servidor:
        servidor.timeout = 1
        redirect_uri = f"http://127.0.0.1:{servidor.server_port}/"
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": "https://www.googleapis.com/auth/calendar.events", "access_type": "offline",
            "prompt": "consent", "state": estado, "code_challenge": desafio, "code_challenge_method": "S256",
        })
        print("Abra este endereço no navegador deste computador para autorizar o Google Calendar:\n" + url)
        webbrowser.open(url)
        prazo = time.monotonic() + 180
        while not resultado and time.monotonic() < prazo:
            servidor.handle_request()
    if "code" not in resultado:
        raise SystemExit("Autorização não concluída. Execute novamente.")
    resposta = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id, "client_secret": client_secret, "code": resultado["code"],
        "code_verifier": verificador, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }, timeout=20)
    if not resposta.ok or not resposta.json().get("refresh_token"):
        raise SystemExit("Não foi possível obter o refresh token. Confira o consentimento e tente novamente.")
    set_key(".env", "GOOGLE_REFRESH_TOKEN", resposta.json()["refresh_token"])
    print("GOOGLE_REFRESH_TOKEN salvo no .env. Configure CALENDAR_PROVIDER=google e CALENDAR_USER_ID com o ID de /meuid antes de iniciar o bot.")


if __name__ == "__main__":
    main()
