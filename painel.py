#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Painel de Agentes - Monitor em tempo real para o Maestri e terminais.

Multiplataforma: Windows, Fedora/Linux e macOS.
"""
import argparse
import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# Adiciona a pasta lib/ ao sys.path
RAIZ = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(RAIZ / "lib"))

import comum
import cotas
import leitor
from terminais import obter_terminais


def achar_porta_livre(porta_inicial=3333, max_tentativas=20):
    """Encontra uma porta TCP livre a partir de porta_inicial."""
    for porta in range(porta_inicial, porta_inicial + max_tentativas):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", porta))
                return porta
            except OSError:
                continue
    return porta_inicial


class HandlerEstatico(http.server.SimpleHTTPRequestHandler):
    """Serve os arquivos de publico/ com CORS e sem cache para live-update."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(RAIZ / "publico"), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, format, *args):
        # Silencia logs de requisição HTTP no terminal para manter a tela limpa
        pass


def iniciar_servidor(porta):
    """Inicia o servidor HTTP em uma thread dedicada."""
    try:
        # Permitir reuso rápido da porta
        socketserver.TCPServer.allow_reuse_address = True
        servidor = socketserver.TCPServer(("127.0.0.1", porta), HandlerEstatico)
        t = threading.Thread(target=servidor.serve_forever, daemon=True)
        t.start()
        return servidor
    except Exception as e:
        print(f"[-] Erro ao subir servidor HTTP na porta {porta}: {e}")
        return None


def ciclo_coleta(terminais, pasta_saida):
    """Executa uma rodada de leitura dos agentes e salva publico/dados.json."""
    mapa = comum.carregar_mapa()
    agentes_cfg = mapa.get("agentes", [])

    leituras = []
    trabalhando = 0
    ociosos = 0

    for a in agentes_cfg:
        nome = a["nome"]
        ferramenta = a.get("ferramenta", "auto")
        texto, erro = terminais.tela(nome)

        if erro:
            reg = leitor.sem_leitura(ferramenta, erro)
        else:
            reg = leitor.ler_tela(texto, ferramenta)

        reg["nome"] = nome
        if reg.get("estado") == "trabalhando":
            trabalhando += 1
        elif reg.get("estado") == "ocioso":
            ociosos += 1

        leituras.append(reg)

    agora_iso = comum.iso(comum.agora())
    agora_epoch = time.time()

    # Carrega ou monta o quadro e estrutura esperada pelo index.html
    quadro = comum.carregar_quadro() if hasattr(comum, "carregar_quadro") else {"pendencias": [], "missoes": {}}
    quadro.setdefault("pendencias", [])
    quadro.setdefault("missoes", {})
    quadro.setdefault("editado_em", agora_iso)
    quadro.setdefault("erro", None)

    prs = {
        "consultado": False,
        "erro": None,
        "lido_em_epoch": None,
        "intervalo_s": 300,
        "sem_dono": []
    }

    dados = {
        "versao": "2.0.0",
        "gerado_em": agora_iso,
        "gerado_em_epoch": agora_epoch,
        "origem": "maestri" if hasattr(terminais, "cli") else "tmux",
        "agentes": leituras,
        "quadro": quadro,
        "prs": prs,
        "contas": [],
        "resumo": {
            "total": len(leituras),
            "trabalhando": trabalhando,
            "ociosos": ociosos
        }
    }

    # Gravação atômica em publico/dados.json e dados.js
    pasta_saida.mkdir(parents=True, exist_ok=True)
    json_str = json.dumps(dados, ensure_ascii=False)
    comum.gravar_atomico(pasta_saida / "dados.json", json.dumps(dados, ensure_ascii=False, indent=2))
    
    js_payload = f"""(function() {{
  var d = {json_str};
  window.DADOS_DO_PAINEL = d;
  if (typeof window.__painelDados === "function") {{
    window.__painelDados(d);
  }}
}})();"""
    comum.gravar_atomico(pasta_saida / "dados.js", js_payload)

    return dados


def main():
    parser = argparse.ArgumentParser(
        prog="painel",
        description="Painel de Agentes - Monitor em tempo real de tarefas e cotas para o Maestri (e Tmux)."
    )
    parser.add_argument("--porta", type=int, default=3333, help="Porta HTTP local (padrão: 3333)")
    parser.add_argument("--intervalo", type=int, default=10, help="Intervalo de atualização em segundos (padrão: 10)")
    parser.add_argument("--sem-portal", action="store_true", help="Não abre o Portal do Maestri automaticamente")
    parser.add_argument("--uma-vez", action="store_true", help="Executa apenas uma coleta e sai")
    args = parser.parse_args()

    print("=" * 60)
    print("   🤖 PAINEL DE AGENTES (MAESTRI & MULTIPLATAFORMA)")
    print("=" * 60)

    terminais = obter_terminais()
    tipo = "Maestri" if hasattr(terminais, "cli") else "Tmux"
    print(f"[+] Modo detectado: {tipo}")

    pasta_publica = RAIZ / "publico"

    # Rodada inicial de coleta
    dados_iniciais = ciclo_coleta(terminais, pasta_publica)
    total = dados_iniciais["resumo"]["total"]
    print(f"[+] Agentes detectados no workspace: {total}")
    for a in dados_iniciais["agentes"]:
        st = a.get("estado", "indeterminado")
        mod = a.get("modelo") or a.get("ferramenta") or "IA"
        print(f"    - {a['nome']}: {st} ({mod})")

    if args.uma_vez:
        print("[+] Coleta única concluída.")
        return

    # Inicia o servidor web local
    porta = achar_porta_livre(args.porta)
    servidor = iniciar_servidor(porta)
    url_painel = f"http://localhost:{porta}"
    print(f"[+] Servidor web ativo em: {url_painel}")

    # Abre automaticamente como Portal no Maestri se aplicável
    if not args.sem_portal and hasattr(terminais, "abrir_portal"):
        print("[+] Abrindo Portal no canvas do Maestri...")
        if terminais.abrir_portal(url_painel, "Painel de Agentes"):
            print("  ✔ Portal criado com sucesso no seu workspace!")
        else:
            print("  (Aviso: Para abrir manualmente, adicione um Portal apontando para a URL acima)")

    print(f"\n[+] Monitorando a cada {args.intervalo}s. Pressione Ctrl+C para encerrar.\n")

    try:
        while True:
            time.sleep(args.intervalo)
            d = ciclo_coleta(terminais, pasta_publica)
            r = d["resumo"]
            hora = datetime.now().strftime("%H:%M:%S")
            print(f"[{hora}] {r['total']} agentes | {r['trabalhando']} trabalhando | {r['ociosos']} ociosos", end="\r", flush=True)
    except KeyboardInterrupt:
        print("\n\n[+] Painel finalizado com sucesso.")


if __name__ == "__main__":
    main()
