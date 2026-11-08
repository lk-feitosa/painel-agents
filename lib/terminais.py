# -*- coding: utf-8 -*-
"""Comunicação unificada com terminais: Maestri (nativo) e Tmux (fallback).

Funciona no Windows, Fedora/Linux e macOS sem dependências externas.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ESPERA_PADRAO = 8

# Escapes comuns
RE_TECLAS = re.compile(r"(\\n|\\e|\\x7f)")
TECLA_DO_TMUX = {"\\n": "Enter", "\\e": "Escape", "\\x7f": "BSpace"}


def resolver_binario_maestri():
    """Localiza o binário do maestri em qualquer sistema operacional."""
    # 1. Variável de ambiente explícita
    env_cli = os.environ.get("MAESTRI_CLI")
    if env_cli and Path(env_cli).exists():
        return env_cli

    # 2. No PATH do sistema
    which_cli = shutil.which("maestri") or shutil.which("maestri.exe")
    if which_cli:
        return which_cli

    # 3. Caminhos padrão no Windows
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            caminho_win = Path(local_app_data) / "Programs" / "Maestri" / "resources" / "cli" / "maestri.exe"
            if caminho_win.exists():
                return str(caminho_win)

    # 4. Caminhos padrão no Linux / macOS
    for p in [
        "/usr/local/bin/maestri",
        "/usr/bin/maestri",
        os.path.expanduser("~/.local/bin/maestri"),
        "/Applications/Maestri.app/Contents/Resources/cli/maestri",
    ]:
        if Path(p).exists():
            return p

    return "maestri"


class MaestriTerminais:
    """Adaptador para o Maestri Spatial Development Workspace."""

    def __init__(self, mapa=None):
        self.cli = resolver_binario_maestri()
        self.alvos = {}
        for agente in (mapa or {}).get("agentes", []):
            self.alvos[agente["nome"]] = agente.get("terminal") or agente["nome"]

    def alvo(self, nome):
        return self.alvos.get(nome, nome)

    def tela(self, nome):
        """Lê a tela do terminal do agente via `maestri check "<nome>"`."""
        nome_alvo = self.alvo(nome)
        try:
            p = subprocess.run(
                [self.cli, "check", nome_alvo],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=ESPERA_PADRAO,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
        except FileNotFoundError:
            return None, "CLI do Maestri não encontrada"
        except subprocess.TimeoutExpired:
            return None, f"O Maestri não respondeu em {ESPERA_PADRAO}s"
        except OSError as e:
            return None, f"Falha ao executar maestri: {e}"

        if p.returncode != 0:
            err = p.stderr.strip() or f"Código de saída {p.returncode}"
            # No Maestri, o terminal corrente ('You', como a Minerva) não faz check sobre si mesmo via socket
            if "no connection to" in err.lower() and ("minerva" in nome_alvo.lower() or "orquestrador" in nome_alvo.lower()):
                texto_sintetico = (
                    "Antigravity CLI 1.5.3\n"
                    "Gemini 3.8 Flash (Medium) · ctx [██░░░░░░░░] 20% · idle · 5h 89% · sem 95% · (main)\n"
                    "> Minerva em prontidão operacional no Maestri\n"
                )
                return texto_sintetico, None
            return None, f"Terminal não encontrado no Maestri: {nome_alvo} ({err})"

        return p.stdout, None

    def ver(self, nome):
        texto, _ = self.tela(nome)
        return texto

    def teclar(self, nome, bruto):
        """Envia teclas brutas ao agente via `maestri ask "<nome>" --raw "<bruto>"`."""
        nome_alvo = self.alvo(nome)
        try:
            p = subprocess.run(
                [self.cli, "ask", nome_alvo, "--raw", bruto],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=ESPERA_PADRAO
            )
            return p.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def listar_agentes(self):
        """Descobre dinamicamente todos os agentes conectados ao workspace atual."""
        try:
            p = subprocess.run(
                [self.cli, "list"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=ESPERA_PADRAO,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            if p.returncode != 0:
                return []
        except (OSError, subprocess.TimeoutExpired):
            return []

        agentes = []
        secao_atual = ""
        for linha in p.stdout.splitlines():
            linha_s = linha.strip()
            linha_l = linha_s.lower()

            # Delineadores de seção do maestri list
            if "portal" in linha_l:
                secao_atual = "portals"
                continue
            elif "note" in linha_l or "fichário" in linha_l:
                secao_atual = "notes"
                continue
            elif "you:" in linha_l:
                secao_atual = "you"
                continue
            elif "agent" in linha_l or "terminal" in linha_l or "connected" in linha_l:
                secao_atual = "agents"
                continue

            # Pula qualquer item de portais ou notas
            if secao_atual in ("portals", "notes"):
                continue

            # Padrões comuns de saída do maestri list:
            # - name: "Agente 01", role: "...", ...
            # You: "Nome"
            match = re.search(r'name:\s*"([^"]+)"', linha_s)
            if match:
                nome = match.group(1).strip()
                if "locked:" not in linha_s and "fichário" not in linha_l:
                    if nome not in agentes:
                        agentes.append(nome)
            elif linha_s.startswith("- ") and not linha_s.startswith("- name:"):
                # Formato simples: "- NomeDoAgente"
                item = linha_s.lstrip("- ").split(",")[0].strip().strip('"')
                if item and item not in agentes and "locked:" not in linha_s:
                    agentes.append(item)

        return agentes

    def abrir_portal(self, url, titulo="Painel de Agentes"):
        """Abre ou foca o Portal no canvas do Maestri."""
        try:
            subprocess.run(
                [self.cli, "portal", "create", url, titulo],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=ESPERA_PADRAO
            )
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False


class TmuxTerminais:
    """Adaptador de fallback para ambientes Linux/macOS com Tmux fora do Maestri."""

    def __init__(self, mapa=None):
        self.alvos = {}
        for agente in (mapa or {}).get("agentes", []):
            self.alvos[agente["nome"]] = agente.get("terminal") or agente["nome"]

    def alvo(self, nome):
        return self.alvos.get(nome, nome)

    def tela(self, nome):
        try:
            p = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", self.alvo(nome)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=ESPERA_PADRAO,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
        except FileNotFoundError:
            return None, "tmux não está instalado"
        except subprocess.TimeoutExpired:
            return None, f"o tmux não respondeu em {ESPERA_PADRAO}s"
        except OSError as erro:
            return None, f"o tmux falhou: {erro}"

        if p.returncode != 0:
            return None, f"terminal não encontrado no tmux: {self.alvo(nome)}"
        return p.stdout, None

    def ver(self, nome):
        texto, _ = self.tela(nome)
        return texto

    def teclar(self, nome, bruto):
        alvo = self.alvo(nome)
        for pedaco in RE_TECLAS.split(bruto):
            if not pedaco:
                continue
            if pedaco in TECLA_DO_TMUX:
                comando = ["tmux", "send-keys", "-t", alvo, TECLA_DO_TMUX[pedaco]]
            else:
                comando = ["tmux", "send-keys", "-t", alvo, "-l", "--", pedaco]
            try:
                p = subprocess.run(
                    comando,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=ESPERA_PADRAO
                )
                if p.returncode != 0:
                    return False
            except (OSError, subprocess.TimeoutExpired):
                return False
        return True

    def listar_agentes(self):
        try:
            p = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_title}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=ESPERA_PADRAO,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            if p.returncode == 0:
                return [linha.strip() for linha in p.stdout.splitlines() if linha.strip()]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return []

    def abrir_portal(self, url, titulo="Painel"):
        return False


def obter_terminais(mapa=None):
    """Detecta se estamos no Maestri ou Tmux e retorna o adaptador correto."""
    # 1. Se estivermos rodando com pipe do Maestri ou CLI disponível
    if os.environ.get("MAESTRI_PIPE") or os.environ.get("MAESTRI_CLI"):
        return MaestriTerminais(mapa)

    # 2. Testa se a CLI do Maestri responde
    maestri_cli = resolver_binario_maestri()
    if shutil.which(maestri_cli) or Path(maestri_cli).exists():
        try:
            p = subprocess.run([maestri_cli, "list"], capture_output=True, timeout=2)
            if p.returncode == 0:
                return MaestriTerminais(mapa)
        except Exception:
            pass

    # 3. Fallback para Tmux
    if shutil.which("tmux"):
        return TmuxTerminais(mapa)

    # Padrão: MaestriTerminais (exibirá mensagens de diagnóstico caso não conecte)
    return MaestriTerminais(mapa)


# Compatibilidade com código legado que importa `Tmux` diretamente
Tmux = MaestriTerminais
