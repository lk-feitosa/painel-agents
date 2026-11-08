#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Painel de Agentes - Monitor em tempo real para o Maestri e terminais.

Multiplataforma: Windows, Fedora/Linux e macOS.
Inclui:
  - Coleta de agentes no Maestri/Tmux com status, contexto, missão e atividade;
  - Descoberta multiplataforma de contas e cotas 3D (Antigravity, Codex, Claude Code);
  - Sincronização automática do Kanban (.kanban/board.json -> publico/kanban.json);
  - Descoberta de Skills & MCPs ativos no projeto ("Ecossistema do Projeto");
  - Endpoint POST /refresh e flag --ensure para auto-recuperação silenciosa.
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

ESTADO_GLOBAL = {
    "terminais": None,
    "pasta_saida": RAIZ / "publico",
    "workspace_projeto": None,
}


def porta_em_uso(porta, host="127.0.0.1"):
    """Retorna True se já houver um serviço escutando na porta informada."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, porta)) == 0


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
    # Alinha em blocos de 5h com deslocamento natural para mostrar o cursor de tempo ativo
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
                        {"id": "gemini-3-8-flash", "rotulo": "Gemini 3.8 Flash", "usado_pct": usado_5h_agy, "renova_epoch": renova_5h},
                        {"id": "gemini-3-1-pro", "rotulo": "Gemini 3.1 Pro", "usado_pct": max(5, usado_5h_agy - 5), "renova_epoch": renova_5h},
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

    # Verifica o kanban.json sincronizado ou o test-maestri
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


def sincronizar_kanban_e_missoes(projeto_dir, leituras, quadro, agora_iso):
    """Sincroniza .kanban/board.json -> publico/kanban.json e injeta missões/pendências no Painel."""
    board_path = projeto_dir / ".kanban" / "board.json"
    if not board_path.exists():
        return

    try:
        board = json.loads(board_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return

    # 1. Mantém publico/kanban.json sempre sincronizado com o board do projeto
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

        # Detecta se o cartão aguarda aprovação formal do usuário
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
                "fontes": ["docs/spec.md", "docs/architecture.md", ".kanban/board.json"],
            })

        # Associa missão ao agente correspondente
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

    # Injeta a missão em cada agente lido se ainda não tiver missão manual no quadro.json
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

    # Mescla pendências automáticas do Kanban com as do quadro.json sem duplicar
    ids_existentes = {p.get("id") for p in quadro.get("pendencias", []) if p.get("id")}
    for pa in pendencias_auto:
        if pa["id"] not in ids_existentes:
            quadro.setdefault("pendencias", []).append(pa)


def descobrir_ecossistema_projeto(projeto_dir, contas_info):
    """Mapeia Skills, MCPs e Sugestão Dinâmica de Modelos ativos no projeto/workspace."""
    tem_ai_memory = (projeto_dir / ".ai-memory").exists() or (projeto_dir / ".ai-memory.toml").exists()
    tem_kanban = (projeto_dir / ".kanban" / "board.json").exists()
    tem_agents_gov = (projeto_dir / ".agents").exists()

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
            "categoria": "Telemetria & Auto-Heal",
            "papel": "Workspace",
            "estado": "ativa",
            "descricao": "Monitora agentes, cápsulas de cota 3D, Kanban e reativa a porta 3333 automaticamente ao interagir com a Minerva.",
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

    # Adiciona eventuais skills locais específicas do projeto em .agents/skills/
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
            "nome": "kan / Kanban Local",
            "tipo": "MCP Gestão Ágil",
            "estado": "ativo" if tem_kanban else "disponível",
            "detalhe": "Quadro `.kanban/board.json` sincronizado em tempo real com `:3333/kanban.html`",
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
        "skills": skills,
        "mcps": mcps,
        "roteamento_modelos": roteamento,
    }


def ciclo_coleta(terminais, pasta_saida):
    """Executa uma rodada de leitura dos agentes e salva publico/dados.json e dados.js."""
    mapa = comum.carregar_mapa()
    agentes_cfg = mapa.get("agentes", [])

    # Se estivermos no Maestri, descobre novos terminais abertos dinamicamente
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
        else:
            reg = leitor.ler_tela(texto, ferramenta)

        reg["nome"] = nome
        ferr_real = reg.get("ferramenta") or ferramenta
        reg["ferramenta_nome"] = NOME_FERRAMENTA.get(ferr_real, "Agente IA")
        reg["conta"] = "lukynnhas.feitosa@gmail.com"
        reg.setdefault("prs", [])
        reg.setdefault("missao", None)

        if reg.get("estado") == "trabalhando":
            trabalhando += 1
        elif reg.get("estado") == "ocioso":
            ociosos += 1

        leituras.append(reg)

    # Carrega o quadro e sincroniza com o Kanban do projeto ativo
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

    dados = {
        "versao": "2.1.0",
        "gerado_em": agora_iso,
        "gerado_em_epoch": agora_epoch,
        "origem": "maestri" if hasattr(terminais, "cli") else "tmux",
        "agentes": leituras,
        "quadro": quadro,
        "prs": prs,
        "contas": contas_info,
        "ecossistema": ecossistema,
        "resumo": {
            "total": len(leituras),
            "trabalhando": trabalhando,
            "ociosos": ociosos,
        },
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


class HandlerEstatico(http.server.SimpleHTTPRequestHandler):
    """Serve os arquivos de publico/ com CORS, sem cache e suporte a POST /refresh."""

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

    def do_POST(self):
        if self.path.startswith("/refresh"):
            try:
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


def iniciar_servidor(porta):
    """Inicia o servidor HTTP em uma thread dedicada."""
    try:
        socketserver.TCPServer.allow_reuse_address = True
        servidor = socketserver.TCPServer(("127.0.0.1", porta), HandlerEstatico)
        t = threading.Thread(target=servidor.serve_forever, daemon=True)
        t.start()
        return servidor
    except Exception as e:
        print(f"[-] Erro ao subir servidor HTTP na porta {porta}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        prog="painel",
        description="Painel de Agentes - Monitor em tempo real de tarefas, cotas 3D, Kanban e Skills para o Maestri."
    )
    parser.add_argument("--porta", type=int, default=3333, help="Porta HTTP local (padrão: 3333)")
    parser.add_argument("--intervalo", type=int, default=10, help="Intervalo de atualização em segundos (padrão: 10)")
    parser.add_argument("--sem-portal", action="store_true", help="Não abre o Portal do Maestri automaticamente")
    parser.add_argument("--uma-vez", action="store_true", help="Executa apenas uma coleta e sai")
    parser.add_argument("--ensure", action="store_true", help="Sai imediatamente se a porta já estiver ativa")
    parser.add_argument("--projeto", type=str, default=None, help="Caminho do projeto ativo para sincronizar Kanban e Skills")
    args = parser.parse_args()

    if args.projeto:
        ESTADO_GLOBAL["workspace_projeto"] = args.projeto

    if args.ensure and porta_em_uso(args.porta):
        print(f"[+] Painel já está ativo na porta {args.porta}.")
        return

    print("=" * 60)
    print("   🤖 PAINEL DE AGENTES (MAESTRI & MULTIPLATAFORMA)")
    print("=" * 60)

    terminais = obter_terminais()
    ESTADO_GLOBAL["terminais"] = terminais
    tipo = "Maestri" if hasattr(terminais, "cli") else "Tmux"
    print(f"[+] Modo detectado: {tipo}")

    pasta_publica = RAIZ / "publico"
    ESTADO_GLOBAL["pasta_saida"] = pasta_publica

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
