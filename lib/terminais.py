# -*- coding: utf-8 -*-
"""Comunicação unificada com terminais: Maestri (nativo) e Tmux (fallback).

Funciona no Windows, Fedora/Linux e macOS sem dependências externas.
"""
import os
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ESPERA_PADRAO = 8
FLAGS_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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

    # 3. AppImage montado em /tmp/.mount_Maestr* no Linux
    try:
        candidatos = sorted(
            Path("/tmp").glob(".mount_Maestr*/resources/cli/maestri"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidatos:
            return str(candidatos[0])
    except Exception:
        pass

    # 4. Caminhos padrão no Windows
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            caminho_win = Path(local_app_data) / "Programs" / "Maestri" / "resources" / "cli" / "maestri.exe"
            if caminho_win.exists():
                return str(caminho_win)

    # 5. Caminhos padrão no Linux / macOS
    for p in [
        "/usr/local/bin/maestri",
        "/usr/bin/maestri",
        os.path.expanduser("~/.local/bin/maestri"),
        "/Applications/Maestri.app/Contents/Resources/cli/maestri",
    ]:
        if Path(p).exists():
            return p

    return "maestri"


def resolver_socket_maestri():
    """Localiza o socket Unix do Maestri no Linux/macOS caso não esteja no ambiente."""
    env_sock = os.environ.get("MAESTRI_SOCKET")
    if env_sock and Path(env_sock).exists():
        return env_sock
    try:
        socks = sorted(
            Path("/tmp").glob("maestri-*/maestri.sock"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if socks:
            return str(socks[0])
    except Exception:
        pass
    return env_sock


MAPA_AGENT_TYPE = {
    "antigravity_cli": "agy",
    "codex": "codex",
    "claude_code": "claude",
}


def inspecionar_workspace_maestri():
    """Lê ~/.maestri/app-state.json e o workspace.json ativo para descobrir terminais reais e conexões (ropes)."""
    maestri_dir = Path(os.environ.get("MAESTRI_DATA_DIR") or (Path.home() / ".maestri"))
    ws_id = os.environ.get("MAESTRI_WORKSPACE_ID")
    if not ws_id:
        app_state = maestri_dir / "app-state.json"
        if app_state.exists():
            try:
                dados_app = json.loads(app_state.read_text(encoding="utf-8"))
                ws_id = (dados_app.get("payload") or {}).get("activeWorkspaceId")
            except Exception:
                pass

    ws_file = None
    if ws_id:
        candidato = maestri_dir / "workspaces" / ws_id / "workspace.json"
        if candidato.exists():
            ws_file = candidato
    if not ws_file:
        try:
            arquivos = sorted(
                (maestri_dir / "workspaces").glob("*/workspace.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if arquivos:
                ws_file = arquivos[0]
        except Exception:
            pass

    if not ws_file or not ws_file.exists():
        return {"terminais": [], "peers_por_nome": {}, "hub_id": None}

    try:
        payload = (json.loads(ws_file.read_text(encoding="utf-8")).get("payload") or {})
    except Exception:
        return {"terminais": [], "peers_por_nome": {}, "hub_id": None}

    id_para_terminal = {}
    for node in payload.get("nodes") or []:
        t = ((node.get("content") or {}).get("terminal") or {}).get("_0")
        if not t or t.get("isUnloaded"):
            continue
        tid = str(t.get("id") or node.get("id") or "").strip()
        nome = str(t.get("name") or "").strip()
        if not tid or not nome:
            continue
        agent_type = str(t.get("agentType") or "").strip()
        id_para_terminal[tid] = {
            "id": tid,
            "nome": nome,
            "terminal": nome,
            "ferramenta": MAPA_AGENT_TYPE.get(agent_type, "auto"),
            "agent_type": agent_type,
            "status": str(t.get("status") or ""),
            "is_manager": bool(t.get("isManager")),
        }

    peers_por_id = {tid: [] for tid in id_para_terminal}
    for conn in payload.get("connections") or []:
        a = str(conn.get("terminalIdA") or "").strip()
        b = str(conn.get("terminalIdB") or "").strip()
        if a in id_para_terminal and b in id_para_terminal:
            if b not in peers_por_id[a]:
                peers_por_id[a].append(b)
            if a not in peers_por_id[b]:
                peers_por_id[b].append(a)

    conectados_ids = {tid for tid, peers in peers_por_id.items() if peers}
    terminais_lista = []
    for tid, info in id_para_terminal.items():
        # Se houver uma árvore conectada no workspace, ignora terminais soltos que estão 'stopped'
        if conectados_ids and tid not in conectados_ids and info.get("status") != "running":
            continue
        terminais_lista.append(info)

    peers_por_nome = {}
    for tid, info in id_para_terminal.items():
        peers_por_nome[info["nome"]] = peers_por_id.get(tid, [])

    hub_id = None
    if peers_por_id:
        hub_id = max(peers_por_id.keys(), key=lambda k: len(peers_por_id[k]), default=None)

    return {
        "terminais": terminais_lista,
        "peers_por_nome": peers_por_nome,
        "hub_id": hub_id,
    }


class MaestriTerminais:
    """Adaptador para o Maestri Spatial Development Workspace."""

    def __init__(self, mapa=None):
        self.cli = resolver_binario_maestri()
        self.alvos = {}
        for agente in (mapa or {}).get("agentes", []):
            self.alvos[agente["nome"]] = agente.get("terminal") or agente["nome"]

    def _env_para_alvo(self, nome_alvo=None):
        env = os.environ.copy()
        sock = resolver_socket_maestri()
        if sock:
            env["MAESTRI_SOCKET"] = sock
        ws = inspecionar_workspace_maestri()
        if nome_alvo:
            peers = ws["peers_por_nome"].get(nome_alvo) or []
            if peers:
                env["MAESTRI_TERMINAL_ID"] = peers[0]
            elif ws.get("hub_id"):
                env["MAESTRI_TERMINAL_ID"] = ws["hub_id"]
        elif ws.get("hub_id") and not env.get("MAESTRI_TERMINAL_ID"):
            env["MAESTRI_TERMINAL_ID"] = ws["hub_id"]
        return env

    def alvo(self, nome):
        return self.alvos.get(nome, nome)

    def tela(self, nome):
        """Lê a tela do terminal do agente via `maestri check "<nome>"`."""
        self.cli = resolver_binario_maestri()
        nome_alvo = self.alvo(nome)
        env = self._env_para_alvo(nome_alvo)
        try:
            p = subprocess.run(
                [self.cli, "check", nome_alvo],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=ESPERA_PADRAO,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=FLAGS_SEM_JANELA,
            )
        except FileNotFoundError:
            return None, "CLI do Maestri não encontrada"
        except subprocess.TimeoutExpired:
            return None, f"O Maestri não respondeu em {ESPERA_PADRAO}s"
        except OSError as e:
            return None, f"Falha ao executar maestri: {e}"

        if p.returncode != 0:
            err = p.stderr.strip() or f"Código de saída {p.returncode}"
            return None, f"Terminal não encontrado no Maestri: {nome_alvo} ({err})"

        return p.stdout, None

    def ver(self, nome):
        texto, _ = self.tela(nome)
        return texto

    def teclar(self, nome, bruto):
        """Envia teclas brutas ao agente via `maestri ask "<nome>" --raw "<bruto>"`."""
        self.cli = resolver_binario_maestri()
        nome_alvo = self.alvo(nome)
        env = self._env_para_alvo(nome_alvo)
        try:
            p = subprocess.run(
                [self.cli, "ask", nome_alvo, "--raw", bruto],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=ESPERA_PADRAO,
                env=env,
                creationflags=FLAGS_SEM_JANELA,
            )
            return p.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def listar_agentes_detalhado(self):
        """Retorna lista de dicts {'nome', 'ferramenta', 'terminal'} do workspace ativo do Maestri."""
        ws = inspecionar_workspace_maestri()
        if ws["terminais"]:
            return [
                {
                    "nome": t["nome"],
                    "ferramenta": t.get("ferramenta") or "auto",
                    "terminal": t["nome"],
                }
                for t in ws["terminais"]
            ]
        return [{"nome": n, "ferramenta": "auto", "terminal": n} for n in self.listar_agentes()]

    def listar_agentes(self):
        """Descobre dinamicamente todos os agentes conectados ao workspace atual."""
        ws = inspecionar_workspace_maestri()
        if ws["terminais"]:
            return [t["nome"] for t in ws["terminais"]]

        p_stdout = ""
        if os.environ.get("MAESTRI_PIPE") or resolver_socket_maestri():
            try:
                p = subprocess.run(
                    [self.cli, "list"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=ESPERA_PADRAO,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=self._env_para_alvo(),
                    creationflags=FLAGS_SEM_JANELA,
                )
                if p.returncode == 0:
                    p_stdout = p.stdout
            except (OSError, subprocess.TimeoutExpired):
                pass

        agentes = []
        secao_atual = ""
        for linha in p_stdout.splitlines():
            linha_s = linha.strip()
            linha_l = linha_s.lower()

            # Cabeçalhos de seção terminam com ':' ou não começam com '-'
            if not linha_s.startswith("-"):
                if "portal" in linha_l:
                    secao_atual = "portals"
                elif "note" in linha_l or "fichário" in linha_l:
                    secao_atual = "notes"
                elif "you" in linha_l:
                    secao_atual = "you"
                elif "agent" in linha_l or "terminal" in linha_l:
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

        # Fallback de autodescoberta: inspeciona pastas de roles do Maestri se a CLI não listar
        if not agentes:
            for pasta_base in [Path.cwd(), Path("S:/DEV/test-maestri"), Path.home()]:
                pasta_roles = pasta_base / ".maestri" / "roles"
                if pasta_roles.is_dir():
                    for r_dir in pasta_roles.iterdir():
                        if r_dir.is_dir() and (r_dir / "role.json").exists():
                            try:
                                with open(r_dir / "role.json", encoding="utf-8") as f:
                                    r_data = json.load(f)
                                    r_name = r_data.get("name") or r_dir.name
                                    nome_ag = "Minerva" if "minerva" in r_name.lower() else r_name
                                    if nome_ag not in agentes:
                                        agentes.append(nome_ag)
                            except Exception:
                                pass

        return agentes

    def abrir_portal(self, url, titulo="Painel de Agentes"):
        """Abre ou foca o Portal no canvas do Maestri."""
        try:
            subprocess.run(
                [self.cli, "portal", "create", url, titulo],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=ESPERA_PADRAO,
                creationflags=FLAGS_SEM_JANELA,
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
                errors="replace",
                creationflags=FLAGS_SEM_JANELA,
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
                    timeout=ESPERA_PADRAO,
                    creationflags=FLAGS_SEM_JANELA,
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
                errors="replace",
                creationflags=FLAGS_SEM_JANELA,
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
    # 1. Se estivermos rodando com pipe/socket do Maestri, CLI disponível ou pasta ~/.maestri presente
    if os.environ.get("MAESTRI_PIPE") or os.environ.get("MAESTRI_CLI") or resolver_socket_maestri():
        return MaestriTerminais(mapa)

    # 2. Quando houver pasta .maestri no projeto ou no home do usuário
    if sys.platform == "win32" or (Path.cwd() / ".maestri").is_dir() or (Path.home() / ".maestri").is_dir():
        return MaestriTerminais(mapa)

    # 3. Testa se a CLI do Maestri responde
    maestri_cli = resolver_binario_maestri()
    if shutil.which(maestri_cli) or Path(maestri_cli).exists():
        try:
            p = subprocess.run([maestri_cli, "list"], capture_output=True, timeout=2, creationflags=FLAGS_SEM_JANELA)
            if p.returncode == 0:
                return MaestriTerminais(mapa)
        except Exception:
            pass

    # 4. Fallback para Tmux (Linux / macOS puro)
    if shutil.which("tmux"):
        return TmuxTerminais(mapa)

    # Padrão: MaestriTerminais
    return MaestriTerminais(mapa)


# Compatibilidade com código legado que importa `Tmux` diretamente
Tmux = MaestriTerminais
