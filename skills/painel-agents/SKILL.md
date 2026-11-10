---
name: painel-agents
description: Abre e gerencia o Painel de Agentes do Maestri em tempo real. Use quando o usuário pedir para ver o status dos agentes, abrir o painel, monitorar cotas ou tarefas ativas no canvas.
user-invocable: true
---

# Painel de Agentes (Maestri)

Monitor em tempo real para visualizar as tarefas, cápsulas 3D de cotas por conta (`Antigravity`, `Codex`, `Claude Code`), sincronização do Kanban, aprovações pendentes e o ecossistema de **Skills & MCPs do Projeto** no workspace atual do Maestri.

## Como acionar

Qualquer agente pode abrir, verificar ou reativar silenciosamente o painel:

### Comandos disponíveis:

- `painel` — Inicia o monitor e abre automaticamente o card de Portal no canvas do Maestri (`http://localhost:3333`).
- `wscript.exe "S:\DEV\painel-agents\iniciar-silencioso.vbs"` — **(Auto-Heal no Windows)** Verifica se a porta `3333` já está ativa (`--ensure`) e, se estiver desligada (ex.: após reiniciar o PC), sobe o servidor silenciosamente em segundo plano sem bloquear o terminal.
- `python "S:\DEV\painel-agents\painel.py" --sem-portal --ensure` — Garante que o coletor/servidor na porta `3333` esteja ativo sem criar janelas duplicadas.
- `painel --uma-vez` — Faz uma passada única de leitura dos agentes, cotas, Kanban e Skills/MCPs e atualiza `publico/dados.json` e `publico/dados.js`.
- `painel --porta 3333` — Define a porta HTTP local.

## O que o Painel exibe (`http://localhost:3333`)

1. **Tabela de Agentes (Frente e Verso 3D):** Estado (`Trabalhando` / `Ocioso`), modelo, contexto (`%`), PR/CI e a **Missão** sincronizada automaticamente com os cartões ativos do `.kanban/board.json`.
2. **Cards de Cota 3D por Conta (`cotas.js`):** Cápsulas interativas com régua de tempo decorrido (5h e Semana), tilt 3D e tarja pixelada de privacidade no e-mail para **Antigravity (Google AI Pro)**, **Codex CLI** e **Claude Code**.
3. **Precisa de Você:** Cartões 3D com passo a passo para aprovações pendentes do usuário (ex.: aprovação da Spec e escolha consultiva de modelos do Team CODES).
4. **Skills & MCPs do Projeto (`#ecossistema`):**
   - Tabela de **Alocação Consultiva de Modelos (Team CODES)** sugerida pela Minerva;
   - **Skills em Uso no Projeto** (`maestri`, `alethe`, `painel-agents`, `frontend-ui-engineering`, `tdd-workflow`, `security-review`, etc.);
   - **Servidores MCP & Barramentos Conectados** (`ai-memory`, `kan`, `maestri-ipc`, `agent-desktop`, `rea`, `magicpath`).
5. **Mapa Dinâmico de Arquitetura (`🗺️ Arquitetura`):** Grafo interativo em tempo real dos módulos do repositório, com estatísticas de código e arquivos, nós arrastáveis (SVG 60 FPS com pan/zoom), destaque de arquivos alterados recentemente (< 15 min com pulso luminoso) e drawer para despachar missões (refatoração, testes, segurança e docs) diretamente aos agentes conectados no Maestri.
