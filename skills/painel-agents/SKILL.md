---
name: painel-agents
description: Abre e gerencia o Painel de Agentes do Maestri em tempo real. Use quando o usuário pedir para ver o status dos agentes, abrir o painel, monitorar cotas ou tarefas ativas no canvas.
user-invocable: true
---

# Painel de Agentes (Maestri)

Monitor em tempo real para visualizar as tarefas, modelos e cotas dos agentes conectados no workspace atual do Maestri.

## Como acionar

Qualquer agente pode abrir ou verificar o painel executando o comando `painel` no terminal.

### Comandos disponíveis:

- `painel` — Inicia o monitor em segundo plano e abre automaticamente o card de Portal no canvas do Maestri (`http://localhost:3333`).
- `painel --uma-vez` — Faz uma passada única de leitura dos agentes conectados e exibe o resumo no terminal.
- `painel --porta 3333` — Define uma porta customizada para o servidor web local.
- `painel --sem-portal` — Inicia apenas o servidor e o coletor, sem criar o nó de Portal no canvas.

## Integração com o Maestri

O painel descobre os agentes automaticamente pelo comando `maestri list` e lê o status de cada um com `maestri check "<nome>"`.
Ele abre um Portal visual diretamente no canvas com `maestri portal create <url> "Painel de Agentes"`.
