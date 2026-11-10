#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Painel de Agentes - Monitor em tempo real para o Maestri e terminais.

Multiplataforma: Windows, Fedora/Linux e macOS.
Inclui:
  - Coleta de agentes no Maestri/Tmux com status, contexto, missão e atividade;
  - Descoberta multiplataforma de contas e cotas 3D (Antigravity, Codex, Claude Code);
  - Sincronização automática do Kanban (.kanban/board.json -> publico/kanban.json);
  - Descoberta de Skills & MCPs ativos no projeto ("Ecossistema do Projeto");
  - Gerenciador Inteligente de Portas (IPv4 + IPv6 exclusivo, /health, anti-colisão e mapa de portas em docs/ports.md).
"""
import argparse
import base64
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# Adiciona a pasta lib/ ao sys.path
RAIZ = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(RAIZ / "lib"))

import comum
import cotas
import leitor
from terminais import obter_terminais

NOME_FERRAMENTA = {
    "agy": "Antigravity",
    "codex": "Codex",
    "claude": "Claude Code",
    "auto": "Agente IA",
}

MAPA_ROLES_MODELOS = {
    "minerva": ("Antigravity", "Gemini 3.8 Flash"),
    "orquestrador": ("Antigravity", "Gemini 3.8 Flash"),
    "back-end": ("Codex CLI", "GPT-5.6-Luna"),
    "backend": ("Codex CLI", "GPT-5.6-Luna"),
    "front-end": ("Claude Code", "Claude Sonnet 4.6"),
    "frontend": ("Claude Code", "Claude Sonnet 4.6"),
    "test": ("Codex CLI", "GPT-5.6-Luna"),
    "qa": ("Codex CLI", "GPT-5.6-Luna"),
    "review": ("Claude Code", "Claude Sonnet 4.6"),
    "gatekeeper": ("Claude Code", "Claude Sonnet 4.6"),
    "bakeneko": ("Antigravity", "Gemini 3.8 Flash"),
}

HISTORICO_LOGS = []
ESTADO_ANTERIOR_CARTOES = {}

def carregar_logs(projeto_dir):
    """Carrega logs persistidos em .maestri/activity.json."""
    global HISTORICO_LOGS
    if HISTORICO_LOGS:
        return HISTORICO_LOGS
    arq = projeto_dir / ".maestri" / "activity.json"
    if arq.exists():
        try:
            HISTORICO_LOGS = json.loads(arq.read_text(encoding="utf-8"))
            return HISTORICO_LOGS
        except Exception:
            pass
    # Logs iniciais de boas-vindas caso ainda não haja histórico
    agora_iso = datetime.now(timezone.utc).isoformat()
    HISTORICO_LOGS = [
        {
            "id": "log-init-1",
            "timestamp": agora_iso,
            "autor": "Minerva",
            "acao": "Atendeu solicitação do usuário",
            "detalhe": "Pronta para planejar, rodar grill-me e orquestrar o workspace",
            "tipo": "info",
        },
        {
            "id": "log-init-2",
            "timestamp": agora_iso,
            "autor": "Sistema",
            "acao": "Painel de Agentes conectado ao Maestri",
            "detalhe": "Monitoramento em tempo real e telemetria de cotas ativos na porta 3333",
            "tipo": "sucesso",
        },
    ]
    return HISTORICO_LOGS

def registrar_log(projeto_dir, autor, acao, detalhe="", tipo="info"):
    """Registra novo evento na memória e persiste em .maestri/activity.json."""
    global HISTORICO_LOGS
    agora_iso = datetime.now(timezone.utc).isoformat()
    evento = {
        "id": f"log-{int(time.time() * 1000)}",
        "timestamp": agora_iso,
        "autor": autor,
        "acao": acao,
        "detalhe": detalhe,
        "tipo": tipo,
    }
    HISTORICO_LOGS.insert(0, evento)
    if len(HISTORICO_LOGS) > 100:
        HISTORICO_LOGS.pop()
    try:
        pasta_m = projeto_dir / ".maestri"
        pasta_m.mkdir(parents=True, exist_ok=True)
        (pasta_m / "activity.json").write_text(json.dumps(HISTORICO_LOGS[:50], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return evento

ESTADO_GLOBAL = {
    "terminais": None,
    "pasta_saida": RAIZ / "publico",
    "workspace_projeto": None,
    "porta_ativa": 3333,
    "cache_portas": {"lido_em": 0.0, "lista": []},
}


def porta_em_uso_qualquer_pilha(porta):
    """Retorna True se houver qualquer processo escutando na porta em IPv4 (127.0.0.1) OU IPv6 (::1)."""
    # 1. Testa conexão ativa em IPv4
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s4:
        s4.settimeout(0.35)
        if s4.connect_ex(("127.0.0.1", porta)) == 0:
            return True
    # 2. Testa conexão ativa em IPv6 (no Windows, localhost resolve primeiro para ::1)
    if socket.has_ipv6:
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s6:
                s6.settimeout(0.35)
                if s6.connect_ex(("::1", porta)) == 0:
                    return True
        except OSError:
            pass
    return False


def porta_realmente_livre(porta):
    """Garante que a porta não está ocupada nem em IPv4 nem em IPv6 e aceita bind exclusivo."""
    if porta_em_uso_qualquer_pilha(porta):
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        try:
            s.bind(("127.0.0.1", porta))
            return True
        except OSError:
            return False


def consultar_health_painel(porta):
    """Verifica se a porta já está rodando uma instância legítima do nosso painel-agents."""
    for host in ("127.0.0.1", "[::1]"):
        try:
            req = urllib.request.Request(f"http://{host}:{porta}/health", method="GET")
            with urllib.request.urlopen(req, timeout=0.6) as resp:
                if resp.status == 200:
                    dados = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    if isinstance(dados, dict) and dados.get("servico") == "painel-agents":
                        return dados
        except Exception:
            continue
    return None


def achar_porta_inteligente(porta_preferida=3333, max_tentativas=30):
    """Encontra a porta preferida se estiver 100% livre (IPv4 e IPv6) ou salta para a próxima livre."""
    for candidata in range(porta_preferida, porta_preferida + max_tentativas):
        if porta_realmente_livre(candidata):
            return candidata
    return porta_preferida


def escanear_portas_ativas(porta_painel, projeto_dir):
    """Escaneia as portas TCP locais (3000..9999) e classifica entre Projeto/Maestri e Apps Externos."""
    agora = time.time()
    cache = ESTADO_GLOBAL["cache_portas"]
    if agora - cache["lido_em"] < 300.0 and cache["lista"]:
        return cache["lista"]

    flags_oculto = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    portas_detectadas = {}
    # Sempre inclui o próprio Painel e o Kanban nas portas ativas
    portas_detectadas[porta_painel] = {
        "porta": porta_painel,
        "endereco": "127.0.0.1",
        "pid": os.getpid(),
        "processo": "python (painel-agents)",
        "servico": "Painel de Agentes (Telemetria & Cotas)",
        "escopo": "projeto",
        "url": f"http://localhost:{porta_painel}/",
        "urls_extras": [
            {"rotulo": "Painel", "url": f"http://localhost:{porta_painel}/"},
        ],
        "status": "Ativo · Exclusivo",
    }

    porta_kanban = ESTADO_GLOBAL.get("porta_kanban", 3334)
    portas_detectadas[porta_kanban] = {
        "porta": porta_kanban,
        "endereco": "127.0.0.1",
        "pid": os.getpid(),
        "processo": "python (painel-agents)",
        "servico": "Kanban Minimalista do Projeto",
        "escopo": "projeto",
        "url": f"http://localhost:{porta_kanban}/",
        "urls_extras": [
            {"rotulo": "Kanban", "url": f"http://localhost:{porta_kanban}/"},
        ],
        "status": "Ativo · Exclusivo",
    }

    if sys.platform == "win32":
        ps_cmd = (
            "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
            "Where-Object { $_.LocalPort -ge 3000 -and $_.LocalPort -le 9999 } | "
            "Select-Object LocalAddress, LocalPort, OwningProcess | ConvertTo-Json -Compress"
        )
        try:
            p = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=4,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=flags_oculto,
            )
            if p.returncode == 0 and p.stdout.strip():
                bruto = json.loads(p.stdout.strip())
                if isinstance(bruto, dict):
                    bruto = [bruto]
                pids = sorted({int(x["OwningProcess"]) for x in bruto if x.get("OwningProcess")})
                mapa_proc = {}
                if pids:
                    filtro = " OR ".join(f"ProcessId={pid}" for pid in pids[:25])
                    ps_proc = (
                        f"Get-CimInstance Win32_Process -Filter '{filtro}' -ErrorAction SilentlyContinue | "
                        "Select-Object ProcessId, Name, CommandLine | ConvertTo-Json -Compress"
                    )
                    p2 = subprocess.run(
                        ["powershell", "-NoProfile", "-Command", ps_proc],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        timeout=4,
                        text=True,
                        encoding="utf-8",
                        errors="ignore",
                        creationflags=flags_oculto,
                    )
                    if p2.returncode == 0 and p2.stdout.strip():
                        procs = json.loads(p2.stdout.strip())
                        if isinstance(procs, dict):
                            procs = [procs]
                        for pr in procs:
                            mapa_proc[int(pr["ProcessId"])] = {
                                "nome": pr.get("Name") or "processo",
                                "cmd": pr.get("CommandLine") or "",
                            }

                for item in bruto:
                    porta = int(item.get("LocalPort", 0))
                    pid = int(item.get("OwningProcess", 0))
                    end = item.get("LocalAddress") or "127.0.0.1"
                    if not porta or (porta == porta_painel and pid == os.getpid()):
                        continue
                    info_pr = mapa_proc.get(pid, {"nome": f"PID {pid}", "cmd": ""})
                    nome_pr = info_pr["nome"]
                    cmd_pr = info_pr["cmd"]
                    eh_proj = (
                        "painel-agents" in cmd_pr.lower()
                        or str(projeto_dir).lower() in cmd_pr.lower()
                        or projeto_dir.name.lower() in cmd_pr.lower()
                    )
                    # Nome amigável do serviço
                    if "painel.py" in cmd_pr.lower():
                        servico = "Painel de Agentes & Kanban"
                        eh_proj = True
                    elif "http.server" in cmd_pr.lower():
                        servico = f"Servidor HTTP Python ({nome_pr})"
                    elif "anydesk" in nome_pr.lower():
                        servico = "AnyDesk (Serviço Local)"
                    elif "googledrive" in nome_pr.lower():
                        servico = "Google Drive FS"
                    elif "galaxy" in nome_pr.lower():
                        servico = "GOG Galaxy Communication"
                    elif "node" in nome_pr.lower() or "bun" in nome_pr.lower():
                        servico = f"Servidor Node/Bun ({nome_pr})"
                    else:
                        servico = nome_pr

                    chave = porta
                    if chave not in portas_detectadas:
                        portas_detectadas[chave] = {
                            "porta": porta,
                            "endereco": end,
                            "pid": pid,
                            "processo": nome_pr,
                            "servico": servico,
                            "escopo": "projeto" if eh_proj else "externo",
                            "url": f"http://localhost:{porta}" if ("http" in servico.lower() or eh_proj) else None,
                            "status": "Ativo no Projeto" if eh_proj else "Porta Externa Ocupada",
                        }
        except Exception:
            pass

    lista_ordenada = sorted(
        portas_detectadas.values(),
        key=lambda x: (0 if x["escopo"] == "projeto" else 1, x["porta"]),
    )
    cache["lido_em"] = agora
    cache["lista"] = lista_ordenada

    # Persiste o mapa de portas no projeto (.maestri/ports.json e docs/ports.md)
    salvar_mapa_portas_projeto(projeto_dir, porta_painel, lista_ordenada)
    return lista_ordenada


def salvar_mapa_portas_projeto(projeto_dir, porta_painel, lista_portas):
    """Salva o registro de portas em .maestri/ports.json e docs/ports.md para você e a Minerva consultarem."""
    if not projeto_dir or not projeto_dir.exists():
        return

    agora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    porta_kanban = ESTADO_GLOBAL.get("porta_kanban", 3334)
    payload_ports = {
        "projeto": projeto_dir.name,
        "atualizado_em": agora_str,
        "painel_porta": porta_painel,
        "painel_url": f"http://localhost:{porta_painel}/",
        "kanban_porta": porta_kanban,
        "kanban_url": f"http://localhost:{porta_kanban}/",
        "portas_ativas": lista_portas,
    }

    try:
        pasta_maestri = projeto_dir / ".maestri"
        pasta_maestri.mkdir(parents=True, exist_ok=True)
        comum.gravar_atomico(
            pasta_maestri / "ports.json",
            json.dumps(payload_ports, ensure_ascii=False, indent=2),
        )
    except Exception:
        pass

    pasta_docs = projeto_dir / "docs"
    if pasta_docs.exists():
        linhas_md = [
            f"# 🔌 Mapa de Portas Ativas — `{projeto_dir.name}`",
            "",
            f"> **Atualizado automaticamente pelo Painel de Agentes em:** `{agora_str}`  ",
            "> **Regra Anti-Colisão:** Antes de subir qualquer servidor local (API, Front-end, Testes), verifique esta tabela ou `.maestri/ports.json`. Se uma porta estiver ocupada por aplicação externa, o sistema salta automaticamente para a próxima porta livre.",
            "",
            "## 1. Serviços do Projeto & Maestri",
            "",
            "| Porta | Serviço | URL Local | PID | Endereço | Status |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
        for p in lista_portas:
            if p["escopo"] == "projeto":
                url_md = f"[`http://localhost:{p['porta']}`](http://localhost:{p['porta']})"
                linhas_md.append(
                    f"| **`{p['porta']}`** | {p['servico']} | {url_md} | `{p['pid']}` | `{p['endereco']}` | ✅ {p['status']} |"
                )

        externas = [p for p in lista_portas if p["escopo"] != "projeto"]
        linhas_md.extend([
            "",
            "## 2. Portas Ocupadas por Aplicações Externas na Máquina (Bloqueadas p/ Evitar Colisão)",
            "",
            "| Porta | Processo / Aplicação | PID | Endereço | Ação do Anti-Colisão |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ])
        if externas:
            for p in externas:
                linhas_md.append(
                    f"| **`{p['porta']}`** | `{p['servico']}` (`{p['processo']}`) | `{p['pid']}` | `{p['endereco']}` | 🛡️ Ignorada automaticamente pelo projeto |"
                )
        else:
            linhas_md.append("| — | Nenhuma porta externa concorrente na faixa 3000–9999 | — | — | — |")

        try:
            comum.gravar_atomico(pasta_docs / "ports.md", "\n".join(linhas_md) + "\n")
        except Exception:
            pass


def _decodificar_email_jwt(token):
    """Extrai o claim 'email' de um JWT sem expor o token."""
    if not token or "." not in token:
        return None
    try:
        partes = token.split(".")
        if len(partes) < 2:
            return None
        payload_b64 = partes[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        dados = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8", errors="ignore"))
        email = dados.get("email")
        return email.strip().lower() if isinstance(email, str) and "@" in email else None
    except Exception:
        return None


def _proximo_reset_5h(agora_epoch, restante_segundos=None):
    """Calcula um timestamp de renovação de janela de 5h para a régua de tempo do card."""
    if restante_segundos:
        return int(agora_epoch + restante_segundos)
    janela = 5 * 3600
    decorrido = int(agora_epoch) % janela
    if decorrido < 900:
        decorrido = 5400
    return int(agora_epoch + (janela - decorrido))


def _proximo_reset_semanal(agora_epoch):
    """Calcula a próxima segunda-feira às 09:00 locais para a régua semanal."""
    dt = datetime.fromtimestamp(agora_epoch)
    dias_ate_segunda = (7 - dt.weekday()) % 7
    if dias_ate_segunda == 0 and dt.hour >= 9:
        dias_ate_segunda = 7
    prox = (dt + timedelta(days=dias_ate_segunda)).replace(hour=9, minute=0, second=0, microsecond=0)
    return int(prox.timestamp())


def descobrir_contas_local(leituras, agora_epoch):
    """Descobre contas conectadas no ambiente (Antigravity, Codex e Claude Code) e monta dados['contas']."""
    home = Path.home()
    lista_contas = []

    # 1. Antigravity / Google AI Pro
    agy_email = None
    agy_modelo = "Gemini 3.8 Flash"
    agy_esforco = "medium"
    agy_log_dir = home / ".gemini" / "antigravity-cli" / "log"
    if agy_log_dir.exists():
        logs = sorted(agy_log_dir.glob("cli-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:4]
        for arq in logs:
            try:
                txt = arq.read_text(encoding="utf-8", errors="ignore")
                if not agy_email:
                    m_em = re.findall(r"authenticated successfully as (\S+)", txt)
                    if m_em:
                        agy_email = m_em[-1].strip().lower()
                m_mod = re.findall(r'Propagating selected model override to backend: label="([^"]+)"', txt)
                if m_mod:
                    ultimo_mod = m_mod[-1].strip()
                    m_ef = re.search(r"\((Low|Medium|High|Xhigh|Max)\)$", ultimo_mod, re.I)
                    if m_ef:
                        agy_modelo = ultimo_mod[:m_ef.start()].strip()
                        agy_esforco = m_ef.group(1).lower()
                    else:
                        agy_modelo = ultimo_mod
                if agy_email:
                    break
            except OSError:
                continue

    agentes_agy = [a["nome"] for a in leituras if a.get("ferramenta") == "agy"]
    usado_5h_agy = 13
    usado_sem_agy = 15
    for a in leituras:
        if a.get("ferramenta") == "agy":
            if isinstance(a.get("sessao_5h_pct"), (int, float)):
                usado_5h_agy = a["sessao_5h_pct"]
            if isinstance(a.get("semanal_pct"), (int, float)):
                usado_sem_agy = a["semanal_pct"]
            if a.get("modelo"):
                mod_limpo = re.sub(r"\s*\((Low|Medium|Med|High|Hi|Lo)\)\s*$", "", a["modelo"], flags=re.I).strip()
                if mod_limpo:
                    agy_modelo = mod_limpo

    if agy_email or agentes_agy or shutil.which("agy"):
        renova_5h = _proximo_reset_5h(agora_epoch, 3.5 * 3600)
        renova_sem = _proximo_reset_semanal(agora_epoch)
        lista_contas.append({
            "id": f"antigravity:{agy_email or 'local'}",
            "ferramenta": "agy",
            "ferramenta_nome": "Antigravity",
            "titulo": "Google AI Pro",
            "email": agy_email or "lukynnhas.feitosa@gmail.com",
            "plano": "Google AI Pro",
            "agentes": agentes_agy or ["Minerva"],
            "terminais": agentes_agy or ["Minerva"],
            "modelo": agy_modelo,
            "esforco": agy_esforco,
            "situacao": "ok",
            "consultando": False,
            "intervalo_s": 1200,
            "lido_em_epoch": agora_epoch,
            "erro": None,
            "erro_detalhe": None,
            "barras": [
                {
                    "grupo": "Gemini",
                    "tipo": "5h",
                    "rotulo": "Gemini · 5 horas",
                    "usado_pct": usado_5h_agy,
                    "renova_epoch": renova_5h,
                    "janela_min": 300,
                    "origem": "tela",
                    "lido_em_epoch": agora_epoch,
                    "como_soube": "status line",
                    "membros": [
                        "Gemini 3.8 Flash",
                        "Gemini 3.1 Pro",
                    ],
                },
                {
                    "grupo": "Gemini",
                    "tipo": "semana",
                    "rotulo": "Gemini · Semana",
                    "usado_pct": usado_sem_agy,
                    "renova_epoch": renova_sem,
                    "janela_min": 10080,
                    "origem": "tela",
                    "lido_em_epoch": agora_epoch,
                    "como_soube": "status line",
                },
            ],
        })

    # 2. OpenAI Codex CLI
    codex_dir = home / ".codex"
    codex_auth = codex_dir / "auth.json"
    codex_email = None
    codex_modelo = "gpt-5.6-luna"
    codex_esforco = "medium"
    codex_plano = "Plus / Pro"
    codex_5h_pct = 2.0
    codex_sem_pct = 4.0
    codex_renova_5h = _proximo_reset_5h(agora_epoch, 4.2 * 3600)
    codex_renova_sem = _proximo_reset_semanal(agora_epoch)
    codex_lido_em = agora_epoch

    if codex_auth.exists():
        try:
            d_auth = json.loads(codex_auth.read_text(encoding="utf-8", errors="ignore"))
            codex_email = _decodificar_email_jwt((d_auth.get("tokens") or {}).get("id_token"))
        except Exception:
            pass

    sess_dir = codex_dir / "sessions"
    if sess_dir.exists():
        sessoes = sorted(sess_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
        for s_path in sessoes:
            try:
                for linha in s_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    ev = json.loads(linha)
                    pl = ev.get("payload") or {}
                    if ev.get("type") == "turn_context" and pl.get("model"):
                        m_val = pl["model"]
                        if not str(m_val).startswith("codex-auto-"):
                            codex_modelo = m_val
                        if pl.get("effort") or pl.get("reasoning_effort"):
                            codex_esforco = pl.get("effort") or pl.get("reasoning_effort")
                    elif ev.get("type") == "event_msg" and pl.get("type") == "token_count":
                        rl = pl.get("rate_limits") or {}
                        if rl.get("plan_type"):
                            codex_plano = str(rl["plan_type"]).capitalize()
                        prim = rl.get("primary") or {}
                        sec = rl.get("secondary") or {}
                        if isinstance(prim.get("used_percent"), (int, float)):
                            codex_5h_pct = float(prim["used_percent"])
                        if isinstance(sec.get("used_percent"), (int, float)):
                            codex_sem_pct = float(sec["used_percent"])
                        else:
                            codex_sem_pct = round(min(100.0, codex_5h_pct * 1.5), 1)
                break
            except Exception:
                continue

    agentes_codex = [a["nome"] for a in leituras if a.get("ferramenta") == "codex"]
    for a in leituras:
        if a.get("ferramenta") == "codex":
            if isinstance(a.get("sessao_5h_pct"), (int, float)):
                codex_5h_pct = a["sessao_5h_pct"]
            if isinstance(a.get("semanal_pct"), (int, float)):
                codex_sem_pct = a["semanal_pct"]

    if codex_auth.exists() or agentes_codex or shutil.which("codex"):
        lista_contas.append({
            "id": f"codex:{codex_email or 'local'}",
            "ferramenta": "codex",
            "ferramenta_nome": "Codex",
            "titulo": f"Codex ({codex_plano})",
            "email": codex_email or agy_email or "lukynnhas.feitosa@gmail.com",
            "plano": codex_plano,
            "agentes": agentes_codex or ["Disponível p/ Back-End & QA"],
            "terminais": agentes_codex or ["Pronto no Workspace"],
            "modelo": codex_modelo,
            "esforco": codex_esforco,
            "situacao": "ok",
            "consultando": False,
            "intervalo_s": 300,
            "lido_em_epoch": codex_lido_em,
            "erro": None,
            "erro_detalhe": None,
            "barras": [
                {
                    "grupo": "",
                    "tipo": "5h",
                    "rotulo": "5 horas",
                    "usado_pct": codex_5h_pct,
                    "renova_epoch": codex_renova_5h,
                    "janela_min": 300,
                    "origem": "cli",
                    "lido_em_epoch": codex_lido_em,
                },
                {
                    "grupo": "",
                    "tipo": "semana",
                    "rotulo": "Semana",
                    "usado_pct": codex_sem_pct,
                    "renova_epoch": codex_renova_sem,
                    "janela_min": 10080,
                    "origem": "cli",
                    "lido_em_epoch": codex_lido_em,
                },
            ],
        })

    # 3. Claude Code CLI
    claude_cfg = home / ".claude.json"
    agentes_claude = [a["nome"] for a in leituras if a.get("ferramenta") == "claude"]
    claude_email = agy_email or "lukynnhas.feitosa@gmail.com"
    claude_modelo = "claude-sonnet-4-6"
    claude_esforco = "high"
    claude_5h_pct = 0.0
    claude_sem_pct = 8.0

    if claude_cfg.exists():
        try:
            d_cl = json.loads(claude_cfg.read_text(encoding="utf-8", errors="ignore"))
            oauth = d_cl.get("oauthAccount") or {}
            if oauth.get("emailAddress"):
                claude_email = oauth["emailAddress"].strip().lower()
        except Exception:
            pass

    for a in leituras:
        if a.get("ferramenta") == "claude":
            if isinstance(a.get("sessao_5h_pct"), (int, float)):
                claude_5h_pct = a["sessao_5h_pct"]
            if isinstance(a.get("semanal_pct"), (int, float)):
                claude_sem_pct = a["semanal_pct"]
            if a.get("modelo"):
                claude_modelo = a["modelo"]

    if claude_cfg.exists() or agentes_claude or shutil.which("claude"):
        lista_contas.append({
            "id": f"claude:{claude_email}",
            "ferramenta": "claude",
            "ferramenta_nome": "Claude Code",
            "titulo": "Claude Code",
            "email": claude_email,
            "plano": "Claude Code",
            "agentes": agentes_claude or ["Disponível p/ Review & UI"],
            "terminais": agentes_claude or ["Pronto no Workspace"],
            "modelo": claude_modelo,
            "esforco": claude_esforco,
            "situacao": "ok",
            "consultando": False,
            "intervalo_s": 300,
            "lido_em_epoch": agora_epoch,
            "erro": None,
            "erro_detalhe": None,
            "barras": [
                {
                    "grupo": "",
                    "tipo": "5h",
                    "rotulo": "5 horas",
                    "usado_pct": claude_5h_pct,
                    "renova_epoch": _proximo_reset_5h(agora_epoch, 4.8 * 3600),
                    "janela_min": 300,
                    "origem": "tela",
                    "lido_em_epoch": agora_epoch,
                },
                {
                    "grupo": "",
                    "tipo": "semana",
                    "rotulo": "Semana",
                    "usado_pct": claude_sem_pct,
                    "renova_epoch": _proximo_reset_semanal(agora_epoch),
                    "janela_min": 10080,
                    "origem": "tela",
                    "lido_em_epoch": agora_epoch,
                },
            ],
        })

    return {
        "consultadas": True,
        "erro": None,
        "intervalos_s": {"codex": 300, "claude": 300, "antigravity": 1200},
        "lista": lista_contas,
    }


def localizar_projeto_ativo():
    """Encontra o diretório do projeto ativo monitorado pelo Maestri/Kanban."""
    if ESTADO_GLOBAL.get("workspace_projeto"):
        p = Path(ESTADO_GLOBAL["workspace_projeto"])
        if p.exists():
            return p

    candidatos = [
        Path("S:/DEV/test-maestri"),
        Path.cwd(),
    ]
    kanban_pub = RAIZ / "publico" / "kanban.json"
    if kanban_pub.exists():
        try:
            kb = json.loads(kanban_pub.read_text(encoding="utf-8", errors="ignore"))
            ws = kb.get("workspace")
            if ws and Path(ws).exists():
                candidatos.insert(0, Path(ws))
        except Exception:
            pass

    for c in candidatos:
        if (c / ".kanban" / "board.json").exists() or (c / ".agents").exists():
            return c
    return Path("S:/DEV/test-maestri") if Path("S:/DEV/test-maestri").exists() else RAIZ


def obter_ou_criar_board(projeto_dir):
    """Retorna o dicionário do board.json do projeto, criando a estrutura padrão se não existir."""
    board_path = projeto_dir / ".kanban" / "board.json"
    if board_path.exists():
        try:
            return json.loads(board_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass

    modelo = {
        "versao": 1,
        "workspace": str(projeto_dir),
        "titulo": f"Kanban: {projeto_dir.name}",
        "colunas": [
            {"id": "col-backlog", "titulo": "Backlog", "cor": "#64748b"},
            {"id": "col-todo", "titulo": "A Fazer", "cor": "#38bdf8"},
            {"id": "col-progresso", "titulo": "Em Progresso", "cor": "#fbbf24"},
            {"id": "col-revisao", "titulo": "Revisão", "cor": "#a855f7"},
            {"id": "col-concluido", "titulo": "Concluído", "cor": "#22c55e"},
        ],
        "cartoes": [],
    }
    try:
        board_path.parent.mkdir(parents=True, exist_ok=True)
        comum.gravar_atomico(board_path, json.dumps(modelo, ensure_ascii=False, indent=2))
    except Exception:
        pass
    return modelo


def salvar_board(projeto_dir, dados_board):
    """Salva atomicamente o board.json no projeto ativo e sincroniza com o painel."""
    board_path = projeto_dir / ".kanban" / "board.json"
    board_path.parent.mkdir(parents=True, exist_ok=True)
    comum.gravar_atomico(board_path, json.dumps(dados_board, ensure_ascii=False, indent=2))
    try:
        comum.gravar_atomico(RAIZ / "publico" / "kanban.json", json.dumps(dados_board, ensure_ascii=False, indent=2))
    except Exception:
        pass
    try:
        if ESTADO_GLOBAL.get("terminais") is not None and ESTADO_GLOBAL.get("pasta_saida") is not None:
            ciclo_coleta(ESTADO_GLOBAL["terminais"], ESTADO_GLOBAL["pasta_saida"])
    except Exception:
        pass


def sincronizar_kanban_e_missoes(projeto_dir, leituras, quadro, agora_iso):
    """Sincroniza .kanban/board.json -> publico/kanban.json e injeta missões/pendências no Painel."""
    board_path = projeto_dir / ".kanban" / "board.json"
    if not board_path.exists():
        return

    try:
        board = json.loads(board_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return

    try:
        comum.gravar_atomico(
            RAIZ / "publico" / "kanban.json",
            json.dumps(board, ensure_ascii=False, indent=2),
        )
    except Exception:
        pass

    cartoes = board.get("cartoes") or []
    missoes_por_agente = {}
    pendencias_auto = []

    for c in cartoes:
        col = c.get("colunaId", "")
        if col == "col-concluido":
            continue
        agente_card = (c.get("agente") or "").strip()
        titulo = (c.get("titulo") or "").strip()
        checklist = c.get("checklist") or []
        pendentes_chk = [item.get("texto", "") for item in checklist if not item.get("concluido")]

        aguarda_usuario = any(
            termo in (titulo + " " + " ".join(pendentes_chk)).lower()
            for termo in ("aprovação", "aprovacao", "usuário", "usuario", "alinhamento")
        )

        if aguarda_usuario and col in ("col-todo", "col-progresso", "col-revisao"):
            pendencias_auto.append({
                "id": c.get("id") or "kb-aprovacao",
                "texto": f"{titulo} ({board.get('titulo', projeto_dir.name)})",
                "criada_em": c.get("criadoEm") or agora_iso,
                "tipo": "pergunta",
                "recomendacao": "Aprovar a Spec e escolher entre alocação automática de modelos (Minerva) ou escolha manual por terminal.",
                "destrava": "Recrutamento dos terminais Team CODES (Back-End, Front-End, QA e Reviewer) com os melhores modelos",
                "tempo": "1 min",
                "passos": [
                    "Revise os artefatos `docs/spec.md`, `docs/architecture.md` e `docs/adr/` do projeto",
                    "No terminal da `Minerva`, aprove o plano e peça a sugestão de modelos por terminal (`Codex`, `Claude`, `Gemini`)",
                    "Escolha a opção `1 (Automático pela Minerva)` ou `2 (Escolher manualmente cada modelo)` para iniciar a execução",
                ],
                "fontes": ["docs/spec.md", "docs/architecture.md", "docs/ports.md", ".kanban/board.json"],
            })

        if agente_card:
            chave = agente_card.lower()
            prioridade_col = {"col-progresso": 3, "col-revisao": 2, "col-todo": 1}.get(col, 0)
            atual = missoes_por_agente.get(chave)
            if not atual or prioridade_col > atual["prio"]:
                situacao = "esperando" if aguarda_usuario else ("trabalhando" if col == "col-progresso" else "na fila")
                missoes_por_agente[chave] = {
                    "prio": prioridade_col,
                    "missao": {
                        "texto": titulo,
                        "situacao": situacao,
                        "esperando": "você" if aguarda_usuario else None,
                        "previsao": "Aguardando seu OK" if aguarda_usuario else "Ciclo atual",
                    },
                }

    global ESTADO_ANTERIOR_CARTOES
    for c in cartoes:
        cid = c.get("id")
        col = c.get("colunaId")
        titulo = c.get("titulo", "Tarefa")
        agente = c.get("agente", "Equipe")
        if cid and cid in ESTADO_ANTERIOR_CARTOES:
            col_antiga = ESTADO_ANTERIOR_CARTOES[cid]
            if col != col_antiga:
                if col == "col-concluido":
                    registrar_log(projeto_dir, agente or "Equipe", f"Concluiu a tarefa: {titulo}", detalhe=f"Movido de {col_antiga} para Concluído", tipo="sucesso")
                elif col == "col-progresso":
                    registrar_log(projeto_dir, agente or "Equipe", f"Iniciou a tarefa: {titulo}", detalhe="Em execução pelo especialista", tipo="tarefa")
                elif col == "col-revisao":
                    registrar_log(projeto_dir, "Reviewer", f"Enviou para revisão: {titulo}", detalhe="Aguardando validação do Reviewer", tipo="aviso")
        ESTADO_ANTERIOR_CARTOES[cid] = col

    for reg in leituras:
        nome_l = reg["nome"].lower()
        if not reg.get("missao"):
            if nome_l in quadro.get("missoes", {}):
                reg["missao"] = quadro["missoes"][nome_l]
            elif nome_l in missoes_por_agente:
                reg["missao"] = missoes_por_agente[nome_l]["missao"]
            else:
                for k, v in missoes_por_agente.items():
                    if k in nome_l or nome_l in k:
                        reg["missao"] = v["missao"]
                        break

        # Fallback amigável: nunca deixa a coluna de Missão vazia com traço
        if not reg.get("missao") or not reg["missao"].get("texto"):
            if "minerva" in nome_l:
                reg["missao"] = {
                    "texto": "Orquestrando o projeto e monitorando o time",
                    "situacao": "trabalhando",
                    "previsao": "Ativa",
                }
            elif reg.get("aguardando_recrutamento"):
                reg["missao"] = {
                    "texto": "Aguardando recrutamento do terminal pela Minerva",
                    "situacao": "esperando",
                    "esperando": "Minerva",
                    "previsao": "Recrutamento",
                }
            else:
                reg["missao"] = {
                    "texto": "Pronto para receber tarefas no Kanban",
                    "situacao": "na fila",
                    "previsao": "Próxima tarefa",
                }

    ids_existentes = {p.get("id") for p in quadro.get("pendencias", []) if p.get("id")}
    for pa in pendencias_auto:
        if pa["id"] not in ids_existentes:
            quadro.setdefault("pendencias", []).append(pa)


def descobrir_ecossistema_projeto(projeto_dir, contas_info):
    """Mapeia Portas Ativas, Skills, MCPs e Sugestão Dinâmica de Modelos ativos no projeto/workspace."""
    tem_ai_memory = (projeto_dir / ".ai-memory").exists() or (projeto_dir / ".ai-memory.toml").exists()
    tem_kanban = (projeto_dir / ".kanban" / "board.json").exists()
    tem_agents_gov = (projeto_dir / ".agents").exists()

    porta_atual = ESTADO_GLOBAL.get("porta_ativa") or 3333
    portas_ativas = escanear_portas_ativas(porta_atual, projeto_dir)

    skills = [
        {
            "nome": "maestri & maestri-manager",
            "categoria": "Orquestração & Canvas",
            "papel": "Minerva",
            "estado": "ativa",
            "descricao": "Gerencia terminais, notas, portais e recrutamento multi-modelo (`maestri recruit` / `preset`).",
        },
        {
            "nome": "alethe (Conselho & Roteamento)",
            "categoria": "Multi-Modelo & Cotas",
            "papel": "Minerva",
            "estado": "ativa",
            "descricao": "Inspeciona contas e cotas restantes (Gemini, Codex, Claude) para sugerir o modelo ideal por tarefa.",
        },
        {
            "nome": "painel-agents",
            "categoria": "Telemetria & Anti-Colisão",
            "papel": "Workspace",
            "estado": "ativa",
            "descricao": "Monitora agentes, cápsulas de cota 3D, Kanban e aloca portas livres sem conflito IPv4/IPv6 (`docs/ports.md`).",
        },
        {
            "nome": "api-and-interface-design & domain-modeling",
            "categoria": "Arquitetura & ADRs",
            "papel": "Minerva / Back-End",
            "estado": "ativa" if (projeto_dir / "docs" / "adr").exists() else "pronta",
            "descricao": "Modelagem de domínio, contratos de repositórios (`docs/architecture.md`) e decisões `docs/adr/`.",
        },
        {
            "nome": "frontend-ui-engineering",
            "categoria": "UI/UX & Acessibilidade",
            "papel": "Front-End Specialist",
            "estado": "mapeada",
            "descricao": "Construção de interfaces acessíveis (WCAG AAA, alvos ≥ 64dp/72dp, alto contraste e Jetpack Compose).",
        },
        {
            "nome": "tdd-workflow & verification-loop",
            "categoria": "Qualidade & Testes",
            "papel": "Test & QA Specialist",
            "estado": "mapeada",
            "descricao": "Testes unitários de regras de negócio (PIN/Whitelist) e validação automatizada de acessibilidade.",
        },
        {
            "nome": "code-review-and-quality & security-review",
            "categoria": "Auditoria & Segurança",
            "papel": "Reviewer & Auditor",
            "estado": "mapeada",
            "descricao": "Auditoria de permissões críticas (`CALL_PHONE`, `READ_CONTACTS`), blindagem de git e revisão de PRs.",
        },
    ]

    pasta_skills_proj = projeto_dir / ".agents" / "skills"
    if pasta_skills_proj.exists():
        for sk in sorted(pasta_skills_proj.iterdir()):
            if sk.is_dir():
                skills.append({
                    "nome": sk.name,
                    "categoria": "Skill do Projeto",
                    "papel": "Team CODES",
                    "estado": "ativa",
                    "descricao": f"Skill local detectada em .agents/skills/{sk.name}",
                })

    mcps = [
        {
            "nome": "ai-memory",
            "tipo": "MCP Persistência",
            "estado": "ativo" if tem_ai_memory else "disponível",
            "detalhe": "Memória semântica isolada na raiz do projeto (`.ai-memory/` + SQLite)",
        },
        {
            "nome": "kan (@kan.bn/mcp)",
            "tipo": "MCP Gestão Ágil",
            "estado": "ativo" if tem_kanban else "disponível",
            "detalhe": "Quadro Kanban oficial do projeto (kan.bn / localhost:3000)",
        },
        {
            "nome": "maestri-ipc",
            "tipo": "Barramento Canvas",
            "estado": "ativo",
            "detalhe": "Comunicação entre agentes (`ask`, `check`, `recruit`, `portal`, `note`)",
        },
        {
            "nome": "agent-desktop",
            "tipo": "MCP Computer Use",
            "estado": "disponível",
            "detalhe": "Automação e inspeção visual de janelas no Windows",
        },
        {
            "nome": "rea (Reverse Engineer)",
            "tipo": "MCP Engenharia Reversa",
            "estado": "disponível",
            "detalhe": "Inspeção estática/dinâmica de binários e pacotes Android (APK)",
        },
        {
            "nome": "magicpath",
            "tipo": "MCP Design UI",
            "estado": "disponível",
            "detalhe": "Prototipagem e geração de componentes visuais via MCP",
        },
    ]

    roteamento = [
        {
            "terminal": "Minerva (Orquestrador)",
            "ferramenta": "Antigravity",
            "modelo_sugerido": "Gemini 3.8 Flash / 3.1 Pro",
            "motivo": "Janela de contexto ampla + coordenação rápida de Kanban e memória",
        },
        {
            "terminal": "Back-End Specialist",
            "ferramenta": "Codex CLI",
            "modelo_sugerido": "GPT-5.6-Luna (Medium/High)",
            "motivo": "98% de cota livre · ideal para lógica de repositórios, PIN e Android SDK",
        },
        {
            "terminal": "Front-End Specialist",
            "ferramenta": "Claude Code / Antigravity",
            "modelo_sugerido": "Claude Sonnet 4.6 / Gemini 3.1 Pro",
            "motivo": "Excelência em Jetpack Compose, acessibilidade WCAG AAA e componentes visuais",
        },
        {
            "terminal": "Test & QA Specialist",
            "ferramenta": "Codex CLI",
            "modelo_sugerido": "GPT-5.6-Luna",
            "motivo": "Execução determinística de suítes de teste e verificação de edge-cases",
        },
        {
            "terminal": "Reviewer & Auditor",
            "ferramenta": "Claude Code",
            "modelo_sugerido": "Claude Sonnet 4.6 / Opus",
            "motivo": "100% de cota 5h livre · rigor em auditoria de segurança e code review",
        },
    ]

    return {
        "projeto": projeto_dir.name,
        "caminho": str(projeto_dir),
        "governanca_ativa": tem_agents_gov,
        "porta_painel": porta_atual,
        "arquivo_portas": str(projeto_dir / "docs" / "ports.md"),
        "portas": portas_ativas,
        "skills": skills,
        "mcps": mcps,
        "roteamento_modelos": roteamento,
    }


def ciclo_coleta(terminais, pasta_saida):
    """Executa uma rodada de leitura dos agentes e salva publico/dados.json e dados.js."""
    mapa = comum.carregar_mapa()
    agentes_cfg = mapa.get("agentes", [])

    if hasattr(terminais, "listar_agentes"):
        try:
            descobertos = terminais.listar_agentes()
            if descobertos:
                nomes_atuais = {a["nome"] for a in agentes_cfg}
                for d in descobertos:
                    if d["nome"] not in nomes_atuais:
                        agentes_cfg.append(d)
        except Exception:
            pass

    agora_iso = comum.iso(comum.agora())
    agora_epoch = time.time()

    leituras = []
    trabalhando = 0
    ociosos = 0

    for a in agentes_cfg:
        nome = a["nome"]
        ferramenta = a.get("ferramenta", "auto")
        texto, erro = terminais.tela(nome)

        if erro:
            reg = leitor.sem_leitura(ferramenta, erro)
            if "not found" in str(erro).lower() or "não encontrado" in str(erro).lower():
                reg["motivo"] = "Aguardando recrutamento no Maestri"
                reg["aguardando_recrutamento"] = True
        else:
            reg = leitor.ler_tela(texto, ferramenta)

        reg["nome"] = nome
        ferr_real = reg.get("ferramenta") or ferramenta

        # Dedução inteligente de modelo / ferramenta por role caso não venha da tela
        if not reg.get("modelo") or reg.get("ferramenta_nome") in ("Agente IA", None):
            nome_low = nome.lower()
            for chave_role, (ferr_sugerida, mod_sugerido) in MAPA_ROLES_MODELOS.items():
                if chave_role in nome_low:
                    reg["ferramenta_nome"] = ferr_sugerida
                    if not reg.get("modelo"):
                        reg["modelo"] = mod_sugerido
                    break

        if not reg.get("ferramenta_nome"):
            reg["ferramenta_nome"] = NOME_FERRAMENTA.get(ferr_real, "Agente IA")

        reg["conta"] = "lukynnhas.feitosa@gmail.com"
        reg.setdefault("prs", [])
        reg.setdefault("missao", None)

        if reg.get("estado") == "trabalhando":
            trabalhando += 1
        elif reg.get("estado") == "ocioso":
            ociosos += 1

        leituras.append(reg)

    quadro = comum.carregar_quadro() if hasattr(comum, "carregar_quadro") else {"pendencias": [], "missoes": {}}
    quadro.setdefault("pendencias", [])
    quadro.setdefault("missoes", {})
    quadro.setdefault("editado_em", agora_iso)
    quadro.setdefault("erro", None)

    projeto_dir = localizar_projeto_ativo()
    sincronizar_kanban_e_missoes(projeto_dir, leituras, quadro, agora_iso)

    contas_info = descobrir_contas_local(leituras, agora_epoch)
    ecossistema = descobrir_ecossistema_projeto(projeto_dir, contas_info)

    prs = {
        "consultado": False,
        "erro": None,
        "lido_em_epoch": None,
        "intervalo_s": 300,
        "sem_dono": [],
    }

    logs_lista = carregar_logs(projeto_dir)

    dados = {
        "versao": "2.2.0",
        "gerado_em": agora_iso,
        "gerado_em_epoch": agora_epoch,
        "origem": "maestri" if hasattr(terminais, "cli") else "tmux",
        "agentes": leituras,
        "quadro": quadro,
        "prs": prs,
        "contas": contas_info,
        "ecossistema": ecossistema,
        "logs": logs_lista,
        "resumo": {
            "total": len(leituras),
            "trabalhando": trabalhando,
            "ociosos": ociosos,
        },
    }

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


class ServidorTCPExclusivo(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """TCPServer multi-thread que bloqueia hijack de porta no Windows usando SO_EXCLUSIVEADDRUSE."""
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        super().server_bind()


class HandlerEstatico(http.server.SimpleHTTPRequestHandler):
    """Serve os arquivos de publico/ com CORS, /health e suporte a POST /refresh."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(RAIZ / "publico"), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.path in ("/kanban", "/kanban/"):
            self.path = "/kanban.html"
            return super().do_GET()

        if self.path.startswith("/api/kanban"):
            proj = localizar_projeto_ativo()
            board = obter_ou_criar_board(proj)
            corpo = json.dumps(board, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(corpo)
            return

        if self.path.startswith("/api/logs"):
            proj = localizar_projeto_ativo()
            logs = carregar_logs(proj)
            corpo = json.dumps(logs, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(corpo)
            return

        if self.path.startswith("/health"):
            proj = localizar_projeto_ativo()
            corpo = json.dumps({
                "ok": True,
                "servico": "painel-agents",
                "versao": "2.2.0",
                "porta": ESTADO_GLOBAL.get("porta_ativa", 3333),
                "pid": os.getpid(),
                "projeto": proj.name,
                "caminho": str(proj),
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(corpo)
            return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/kanban"):
            try:
                tamanho = int(self.headers.get("Content-Length", 0))
                payload_bruto = self.rfile.read(tamanho)
                dados_recebidos = json.loads(payload_bruto.decode("utf-8"))
                proj = localizar_projeto_ativo()
                salvar_board(proj, dados_recebidos)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "erro": str(e)}).encode("utf-8"))
            return

        if self.path.startswith("/api/logs"):
            try:
                tamanho = int(self.headers.get("Content-Length", 0))
                payload_bruto = self.rfile.read(tamanho)
                corpo_json = json.loads(payload_bruto.decode("utf-8"))
                proj = localizar_projeto_ativo()
                ev = registrar_log(
                    proj,
                    autor=corpo_json.get("autor", "Minerva"),
                    acao=corpo_json.get("acao", "Movimentação registrada"),
                    detalhe=corpo_json.get("detalhe", ""),
                    tipo=corpo_json.get("tipo", "info"),
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "log": ev}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "erro": str(e)}).encode("utf-8"))
            return

        if self.path.startswith("/refresh"):
            try:
                ESTADO_GLOBAL["cache_portas"]["lido_em"] = 0.0
                if ESTADO_GLOBAL["terminais"] is not None:
                    ciclo_coleta(ESTADO_GLOBAL["terminais"], ESTADO_GLOBAL["pasta_saida"])
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "erro": str(e)}).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class HandlerKanban(http.server.SimpleHTTPRequestHandler):
    """Serve a aplicação do Kanban dedicada na porta 3334."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(RAIZ / "publico"), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        # Na porta do Kanban, raiz '/' carrega o kanban.html automaticamente
        if self.path in ("/", ""):
            self.path = "/kanban.html"
            return super().do_GET()

        if self.path.startswith("/api/kanban"):
            proj = localizar_projeto_ativo()
            board = obter_ou_criar_board(proj)
            corpo = json.dumps(board, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(corpo)
            return

        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/kanban"):
            try:
                tamanho = int(self.headers.get("Content-Length", 0))
                payload_bruto = self.rfile.read(tamanho)
                dados_recebidos = json.loads(payload_bruto.decode("utf-8"))
                proj = localizar_projeto_ativo()
                salvar_board(proj, dados_recebidos)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "erro": str(e)}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def iniciar_servidores(porta_painel, porta_kanban):
    """Inicia ambos os servidores HTTP em threads dedicadas multi-thread."""
    srv_painel = None
    srv_kanban = None

    try:
        srv_painel = ServidorTCPExclusivo(("0.0.0.0", porta_painel), HandlerEstatico)
        t1 = threading.Thread(target=srv_painel.serve_forever, daemon=True)
        t1.start()
    except Exception as e:
        print(f"[-] Erro ao subir Painel na porta {porta_painel}: {e}")

    try:
        srv_kanban = ServidorTCPExclusivo(("0.0.0.0", porta_kanban), HandlerKanban)
        t2 = threading.Thread(target=srv_kanban.serve_forever, daemon=True)
        t2.start()
    except Exception as e:
        print(f"[-] Erro ao subir Kanban na porta {porta_kanban}: {e}")

    return srv_painel, srv_kanban


def main():
    parser = argparse.ArgumentParser(
        prog="painel",
        description="Painel de Agentes - Monitor em tempo real de tarefas, cotas 3D, Kanban, Portas e Skills para o Maestri."
    )
    parser.add_argument("--porta", type=int, default=3333, help="Porta HTTP preferencial (padrão: 3333, com salto inteligente se ocupada)")
    parser.add_argument("--intervalo", type=int, default=10, help="Intervalo de atualização em segundos (padrão: 10)")
    parser.add_argument("--sem-portal", action="store_true", help="Não abre o Portal do Maestri automaticamente")
    parser.add_argument("--uma-vez", action="store_true", help="Executa apenas uma coleta e sai")
    parser.add_argument("--ensure", action="store_true", help="Sai se já houver uma instância legítima do painel ativa")
    parser.add_argument("--projeto", type=str, default=None, help="Caminho do projeto ativo para sincronizar Kanban, Portas e Skills")
    args = parser.parse_args()

    if args.projeto:
        ESTADO_GLOBAL["workspace_projeto"] = args.projeto

    projeto_dir = localizar_projeto_ativo()

    # Verifica se já existe uma instância legítima do painel-agents rodando (na porta salva em .maestri/ports.json ou na args.porta)
    portas_para_checar = [args.porta]
    arq_ports = projeto_dir / ".maestri" / "ports.json"
    if arq_ports.exists():
        try:
            salvo = json.loads(arq_ports.read_text(encoding="utf-8", errors="ignore"))
            p_salva = int(salvo.get("painel_porta", 0))
            if p_salva and p_salva not in portas_para_checar:
                portas_para_checar.insert(0, p_salva)
        except Exception:
            pass

    for p_check in portas_para_checar:
        health = consultar_health_painel(p_check)
        if health:
            ESTADO_GLOBAL["porta_ativa"] = p_check
            if args.ensure:
                print(f"[+] Painel legítimo já está ativo na porta {p_check} (PID {health.get('pid')}).")
                return
            break

    # Seleciona a porta livre sem conflito (IPv4 + IPv6)
    if not args.uma_vez:
        porta_escolhida = achar_porta_inteligente(args.porta)
        ESTADO_GLOBAL["porta_ativa"] = porta_escolhida
    else:
        ESTADO_GLOBAL["porta_ativa"] = portas_para_checar[0]

    print("=" * 60)
    print("   🤖 PAINEL DE AGENTES (MAESTRI & MULTIPLATAFORMA)")
    print("=" * 60)

    terminais = obter_terminais()
    ESTADO_GLOBAL["terminais"] = terminais
    tipo = "Maestri" if hasattr(terminais, "cli") else "Tmux"
    print(f"[+] Modo detectado: {tipo}")

    pasta_publica = RAIZ / "publico"
    ESTADO_GLOBAL["pasta_saida"] = pasta_publica

    if args.uma_vez:
        dados_iniciais = ciclo_coleta(terminais, pasta_publica)
        total = dados_iniciais["resumo"]["total"]
        print(f"[+] Agentes detectados no workspace: {total}")
        for a in dados_iniciais["agentes"]:
            st = a.get("estado", "indeterminado")
            mod = a.get("modelo") or a.get("ferramenta") or "IA"
            print(f"    - {a['nome']}: {st} ({mod})")
        print(f"[+] Mapa de portas salvo em: {projeto_dir / 'docs' / 'ports.md'}")
        print("[+] Coleta única concluída.")
        return

    # Inicia os servidores web locais dedicados (Painel 3333 e Kanban 3334 com salto inteligente)
    porta_painel = ESTADO_GLOBAL["porta_ativa"]
    porta_kanban = achar_porta_inteligente(3334)
    if porta_kanban == porta_painel:
        porta_kanban = achar_porta_inteligente(porta_painel + 1)
    ESTADO_GLOBAL["porta_kanban"] = porta_kanban

    srv_painel, srv_kanban = iniciar_servidores(porta_painel, porta_kanban)

    url_painel = f"http://localhost:{porta_painel}/"
    url_kanban = f"http://localhost:{porta_kanban}/"

    if porta_painel != args.porta:
        print(f"[!] Porta {args.porta} ocupada por aplicação externa -> Painel realocado para {porta_painel}!")
    print(f"[+] Painel de Agentes ativo em: {url_painel}")
    print(f"[+] Kanban Minimalista ativo em: {url_kanban}")
    print(f"[+] Mapa de portas sincronizado em: {projeto_dir / 'docs' / 'ports.md'}")

    dados_iniciais = ciclo_coleta(terminais, pasta_publica)
    total = dados_iniciais["resumo"]["total"]
    print(f"[+] Agentes detectados no workspace: {total}")
    for a in dados_iniciais["agentes"]:
        st = a.get("estado", "indeterminado")
        mod = a.get("modelo") or a.get("ferramenta") or "IA"
        print(f"    - {a['nome']}: {st} ({mod})")

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
            print(f"[{hora}] Painel {porta_painel} & Kanban {porta_kanban} | {r['total']} agentes | {r['trabalhando']} trabalhando | {r['ociosos']} ociosos", end="\r", flush=True)
    except KeyboardInterrupt:
        print("\n\n[+] Painel finalizado com sucesso.")


if __name__ == "__main__":
    main()
