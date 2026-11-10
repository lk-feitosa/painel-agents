# -*- coding: utf-8 -*-
"""Módulo de Análise e Mapa Dinâmico de Arquitetura do Workspace.

Varre o repositório ativo e constrói o grafo de módulos, arquivos e dependências
para exibição visual em tempo real no Maestri (60 FPS, sem bibliotecas pesadas).
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# Cache leve em memória para evitar releituras desnecessárias de disco
CACHE_GRAFO = {
    "caminho": None,
    "timestamp": 0,
    "dados": None,
}
CACHE_TTL_SEGUNDOS = 3

PASTAS_IGNORADAS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    ".obsidian",
    ".gemini",
    ".system_generated",
    "target",
    "bin",
    "obj",
    "out",
    "tmp",
}

EXTENSOES_CODIGO = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (React)",
    ".jsx": "React",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".json": "JSON",
    ".sql": "SQL",
    ".sh": "Shell",
    ".bat": "Batch",
    ".cmd": "Batch",
    ".ps1": "PowerShell",
    ".md": "Markdown",
    ".kt": "Kotlin",
    ".java": "Java",
    ".rs": "Rust",
    ".go": "Go",
    ".c": "C",
    ".cpp": "C++",
}


def categorizar_modulo(nome_pasta: str):
    """Categoriza o módulo e define cor e ícone para o grafo visual."""
    nl = nome_pasta.lower()
    if any(k in nl for k in ["api", "route", "endpoint", "controller"]):
        return {"categoria": "api", "rotulo": "API & Rotas", "cor": "#22c55e", "icone": "⚡"}
    if any(k in nl for k in ["core", "lib", "service", "domain", "logic"]):
        return {"categoria": "core", "rotulo": "Core & Serviços", "cor": "#38bdf8", "icone": "⚙️"}
    if any(k in nl for k in ["ui", "component", "view", "public", "page", "asset"]):
        return {"categoria": "ui", "rotulo": "Interface & UI", "cor": "#a855f7", "icone": "🎨"}
    if any(k in nl for k in ["db", "model", "database", "migration", "cota", "repo", "store"]):
        return {"categoria": "database", "rotulo": "Banco & Dados", "cor": "#f59e0b", "icone": "🗄️"}
    if any(k in nl for k in ["skill", "agent", "toolkit", "prompt", "bot"]):
        return {"categoria": "agents", "rotulo": "Agentes & Skills", "cor": "#ec4899", "icone": "🤖"}
    if any(k in nl for k in ["doc", "spec", "adr", "rfc"]):
        return {"categoria": "docs", "rotulo": "Docs & Specs", "cor": "#64748b", "icone": "📄"}
    if any(k in nl for k in ["test", "qa", "e2e", "spec"]):
        return {"categoria": "tests", "rotulo": "Testes & QA", "cor": "#10b981", "icone": "🧪"}
    return {"categoria": "modulo", "rotulo": nome_pasta, "cor": "#6366f1", "icone": "📁"}


def extrair_imports_python(conteudo: str):
    """Detecta imports básicos em código Python para criar arestas de dependência."""
    modulos = set()
    for match in re.finditer(r"^(?:from|import)\s+([a-zA-Z0-9_\.]+)", conteudo, re.MULTILINE):
        base = match.group(1).split(".")[0]
        if base and not base.startswith("_"):
            modulos.add(base)
    return modulos


def extrair_imports_js(conteudo: str):
    """Detecta imports básicos em JavaScript / TypeScript."""
    modulos = set()
    for match in re.finditer(r"(?:import\s+.*?from\s+['\"](.*?)['\"]|require\(['\"](.*?)['\"]\))", conteudo):
        alvo = match.group(1) or match.group(2)
        if alvo and alvo.startswith("."):
            partes = alvo.replace("\\", "/").split("/")
            nome = [p for p in partes if p and p not in (".", "..")]
            if nome:
                modulos.add(nome[0])
    return modulos


def escanear_arquitetura(workspace):
    """Varre o projeto e monta o grafo de nós e conexões."""
    if not isinstance(workspace, Path):
        workspace = Path(workspace)
    workspace = workspace.resolve()
    agora = time.time()
    caminho_str = str(workspace)

    # Retorna do cache se recente
    if (
        CACHE_GRAFO["caminho"] == caminho_str
        and (agora - CACHE_GRAFO["timestamp"]) < CACHE_TTL_SEGUNDOS
        and CACHE_GRAFO["dados"] is not None
    ):
        return CACHE_GRAFO["dados"]

    if not workspace.exists():
        return {
            "workspace": str(workspace),
            "nome": workspace.name,
            "nodes": [],
            "edges": [],
            "stats": {"total_arquivos": 0, "total_linhas": 0, "linguagens": {}},
        }

    modulos_map = {}
    arquivos_raiz = []
    linguagens_contagem = {}
    total_linhas = 0
    total_arquivos = 0
    arquivos_recentes = []

    # 1. Varre os diretórios e arquivos
    for raiz_dir, subpastas, arquivos in os.walk(workspace):
        # Filtra pastas ignoradas in-place
        subpastas[:] = [d for d in subpastas if d not in PASTAS_IGNORADAS and not d.startswith(".")]

        rel_raiz = Path(raiz_dir).relative_to(workspace)
        partes = rel_raiz.parts

        # Define a qual módulo principal este caminho pertence
        modulo_nome = partes[0] if partes else "raiz"

        if modulo_nome not in modulos_map and modulo_nome != "raiz":
            info_cat = categorizar_modulo(modulo_nome)
            modulos_map[modulo_nome] = {
                "id": modulo_nome,
                "label": modulo_nome,
                "categoria": info_cat["categoria"],
                "rotulo": info_cat["rotulo"],
                "cor": info_cat["cor"],
                "icone": info_cat["icone"],
                "arquivos": [],
                "arquivos_count": 0,
                "linhas_count": 0,
                "modificado_recente": False,
                "imports_detectados": set(),
            }

        for arq in arquivos:
            if arq.startswith(".") or arq.endswith((".pyc", ".log", ".tmp", ".lock")):
                continue

            caminho_completo = Path(raiz_dir) / arq
            rel_arq = caminho_completo.relative_to(workspace)
            ext = caminho_completo.suffix.lower()

            try:
                stat = caminho_completo.stat()
                mtime = stat.st_mtime
                tamanho = stat.st_size
            except Exception:
                continue

            # Conta linhas aproximadas para arquivos de código
            linhas = 0
            is_codigo = ext in EXTENSOES_CODIGO
            if is_codigo and tamanho < 1_000_000:  # < 1MB
                try:
                    with open(caminho_completo, "r", encoding="utf-8", errors="ignore") as f:
                        conteudo = f.read()
                        linhas = conteudo.count("\n") + 1
                        # Análise de imports
                        if ext == ".py" and modulo_nome != "raiz":
                            modulos_map[modulo_nome]["imports_detectados"].update(extrair_imports_python(conteudo))
                        elif ext in (".js", ".ts", ".jsx", ".tsx") and modulo_nome != "raiz":
                            modulos_map[modulo_nome]["imports_detectados"].update(extrair_imports_js(conteudo))
                except Exception:
                    pass

            total_arquivos += 1
            total_linhas += linhas
            lang = EXTENSOES_CODIGO.get(ext, "Outro")
            linguagens_contagem[lang] = linguagens_contagem.get(lang, 0) + 1

            # Modificado nos últimos 15 minutos (900s)
            recente = (agora - mtime) < 900
            data_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

            item_arq = {
                "nome": arq,
                "caminho": str(rel_arq).replace("\\", "/"),
                "linhas": linhas,
                "tamanho": tamanho,
                "ext": ext,
                "linguagem": lang,
                "modificado": data_iso,
                "recente": recente,
            }

            if recente:
                arquivos_recentes.append(item_arq)

            if modulo_nome == "raiz":
                arquivos_raiz.append(item_arq)
            else:
                mod = modulos_map[modulo_nome]
                mod["arquivos"].append(item_arq)
                mod["arquivos_count"] += 1
                mod["linhas_count"] += linhas
                if recente:
                    mod["modificado_recente"] = True

    # 2. Constrói Nós (Nodes) do Grafo
    nodes = []

    # Nó Central do Projeto / Root
    nodes.append({
        "id": "root",
        "label": workspace.name,
        "categoria": "root",
        "rotulo": "Workspace Raiz",
        "cor": "#ffffff",
        "icone": "🏛️",
        "tamanho": 32,
        "arquivos_count": len(arquivos_raiz),
        "linhas_count": sum(a["linhas"] for a in arquivos_raiz),
        "arquivos": sorted(arquivos_raiz, key=lambda x: x["linhas"], reverse=True)[:12],
        "modificado_recente": any(a["recente"] for a in arquivos_raiz),
    })

    # Nós dos Módulos
    for mod_id, mod in modulos_map.items():
        nodes.append({
            "id": mod_id,
            "label": mod["label"],
            "categoria": mod["categoria"],
            "rotulo": mod["rotulo"],
            "cor": mod["cor"],
            "icone": mod["icone"],
            "tamanho": max(16, min(28, 14 + mod["arquivos_count"] * 2)),
            "arquivos_count": mod["arquivos_count"],
            "linhas_count": mod["linhas_count"],
            "arquivos": sorted(mod["arquivos"], key=lambda x: x["linhas"], reverse=True)[:15],
            "modificado_recente": mod["modificado_recente"],
        })

    # 3. Constrói Arestas (Edges / Conexões)
    edges = []
    arestas_vistas = set()

    for mod_id, mod in modulos_map.items():
        # Conecta da raiz para o módulo se for subpasta direta
        par_raiz = ("root", mod_id)
        if par_raiz not in arestas_vistas:
            edges.append({
                "from": "root",
                "to": mod_id,
                "tipo": "hierarquia",
                "cor": "rgba(255,255,255,0.15)",
            })
            arestas_vistas.add(par_raiz)

        # Conecta dependências detectadas entre módulos
        for imp in mod.get("imports_detectados", set()):
            if imp in modulos_map and imp != mod_id:
                par_dep = (mod_id, imp)
                if par_dep not in arestas_vistas:
                    edges.append({
                        "from": mod_id,
                        "to": imp,
                        "tipo": "import",
                        "cor": "#38bdf8",
                    })
                    arestas_vistas.add(par_dep)

    resultado = {
        "workspace": str(workspace),
        "nome": workspace.name,
        "atualizadoEm": int(agora * 1000),
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_arquivos": total_arquivos,
            "total_linhas": total_linhas,
            "total_modulos": len(modulos_map),
            "linguagens": dict(sorted(linguagens_contagem.items(), key=lambda x: x[1], reverse=True)[:6]),
            "arquivos_recentes_count": len(arquivos_recentes),
        },
    }

    # Salva no cache
    CACHE_GRAFO["caminho"] = caminho_str
    CACHE_GRAFO["timestamp"] = agora
    CACHE_GRAFO["dados"] = resultado

    return resultado


def despachar_tarefa_modulo(agente: str, modulo_nome: str, acao_tipo: str, prompt_custom: str = None):
    """Envia uma tarefa referente a um módulo diretamente para o agente via maestri ask."""
    if not agente or agente.lower() == "humano":
        return False, "Agente inválido ou Humano selecionado."

    prompts = {
        "refatorar": f"Por favor, revise e refatore o módulo/pasta '{modulo_nome}', aplicando clean code, tipagens robustas e modularidade.",
        "testes": f"Por favor, analise os arquivos do módulo '{modulo_nome}' e crie ou atualize os testes unitários e de integração com cobertura sólida.",
        "seguranca": f"Por favor, realize uma auditoria de segurança, sanitização de dados e tratamento de erros no módulo '{modulo_nome}'.",
        "documentar": f"Por favor, gere ou atualize a documentação técnica e arquitetural referente ao módulo '{modulo_nome}'.",
    }

    prompt_final = prompt_custom if prompt_custom else prompts.get(acao_tipo, f"Trabalhe no módulo '{modulo_nome}'.")

    # Localiza o binário do maestri
    from terminais import resolver_binario_maestri
    cli = resolver_binario_maestri()

    try:
        proc = subprocess.Popen(
            [cli, "ask", agente, prompt_final],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return True, f"Tarefa sobre '{modulo_nome}' despachada para {agente} (PID {proc.pid})."
    except Exception as e:
        return False, f"Falha ao despachar para {agente}: {str(e)}"
