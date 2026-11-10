<div align="center">

<img src="assets/logo.png" alt="Minerva Logo" width="180" style="border-radius: 20px; box-shadow: 0 8px 24px rgba(0,0,0,0.4);" />

# painel-agents

**Telemetria espacial e monitor de cotas em tempo real para agentes de IA no Maestri.**

[![Licença MIT](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-3776AB.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Multiplataforma](https://img.shields.io/badge/plataforma-Windows%20|%20Linux%20|%20macOS-informational.svg?style=flat-square)](#instalação)
[![Zero Token Cost](https://img.shields.io/badge/tokens-zero%20cost-success.svg?style=flat-square)](#arquitetura)

</div>

---

## O que é

`painel-agents` é um painel local, leve e não intrusivo para acompanhar visualmente múltiplos agentes de IA (**Claude Code**, **Codex** e **Antigravity**) operando simultaneamente no **Maestri**.

Diferente de soluções pesadas ou sondas que consomem chamadas de LLM, o `painel-agents` utiliza **telemetria passiva**: lê as telas dos terminais nativos via IPC do Maestri e extrai estado, modelo, esforço, contexto e cotas sem disparar tokens de inferência.

```
┌────────────────────────────────────────────────────────┐
│                    MAESTRI CANVAS                      │
│                                                        │
│   [ Terminal 1: Claude ]  ───┐                         │
│   [ Terminal 2: Codex  ]  ───┼── (maestri check) ──┐   │
│   [ Terminal 3: Agy    ]  ───┘                     │   │
│                                                    ▼   │
│   [ Portal: Painel ] <── http://127.0.0.1:3333 ── [Core]
└────────────────────────────────────────────────────────┘
```

---

## Destaques

- **Auto-Descoberta de Workspace**: Descobre dinamicamente os agentes e terminais pertencentes ao canvas ativo (`maestri list`). Não requer cadastro estático nem arquivos de configuração prévios.
- **Gerenciador Inteligente de Portas & Anti-Colisão IPv4/IPv6 (`docs/ports.md`)**: Escaneia portas locais no intervalo `3000..9999`, detecta conflitos com aplicações externas e salta automaticamente para portas livres. Registra o mapeamento vivo em `docs/ports.md` e `.maestri/ports.json`, exibido em uma seção dedicada e recolhível no painel.
- **Cápsulas de Cota 3D Interativas (`Overview-Painel`)**: Renderiza os cards squircle com tilt 3D, tarja de privacidade pixelada no e-mail, agulha do "Agora" com régua de tempo e consumo permanentemente expandidas, e verso compacto sem corte nem rolagem.
- **Sincronização com Missões do Kanban**: Leitura contínua dos cartões ativos em execução para preencher automaticamente as missões de cada terminal na tabela de agentes e alertar na seção **Precisa de você** quando houver pendências de alinhamento com o usuário.
- **Seção "Skills, MCPs & Modelos do Projeto"**: Exibe a tabela consultiva de modelos do Team CODES (`Minerva`, `Back-End`, `Front-End`, `QA`, `Review`), as Skills em uso e os servidores MCP conectados (`ai-memory`, `kan`, `maestri-ipc`, `agent-desktop`, `rea`, `magicpath`).
- **Auto-Heal Silencioso pela Memória do Projeto**: Nenhum processo desnecessário no boot do Windows. Ao interagir com a Minerva no projeto, ela verifica o endpoint `/health` na porta ativa e, se preciso, aciona `iniciar-silencioso.vbs` (`CREATE_NO_WINDOW`) 100% em segundo plano sem piscar janelas de console.
- **Zero Gasto de Tokens**: Toda a extração é analítica e baseada em expressões regulares determinísticas sobre a status line, sessões locais e buffer do terminal.
- **Multiplataforma Real**: Desenvolvido exclusivamente com a biblioteca padrão do Python 3 (sem dependências externas). Roda de forma idêntica no **Windows**, **Fedora / Ubuntu / Arch** e **macOS**.

---

## Início Rápido

### Instalação (Uma única vez)

```bash
# Clone o repositório
git clone https://github.com/lk-feitosa/painel-agents.git
cd painel-agents

# No Windows:
instalar.bat

# No Fedora / Linux / macOS:
./instalar.sh
```

O instalador registra o comando global `painel` em `~/.local/bin/` e instala a skill correspondente em `~/.agents/skills/painel-agents/`.

---

## Uso

Em qualquer terminal dentro do seu workspace no Maestri, digite:

```bash
painel
```

O executável:
1. Identifica os agentes ativos no canvas atual.
2. Inicia o daemon de telemetria e o servidor HTTP local (`127.0.0.1:3333`).
3. Invoca a API de canvas do Maestri (`maestri portal create`) para abrir o card do painel instantaneamente.

### Opções de Linha de Comando

```
Uso: painel [OPÇÕES]

Opções:
  --porta INT        Porta do servidor local (padrão: 3333, com auto-fallback)
  --intervalo INT    Taxa de amostragem das telas em segundos (padrão: 10s)
  --sem-portal       Inicia o coletor e o servidor web sem abrir o nó de Portal
  --uma-vez          Executa uma única amostragem analítica e encerra no terminal
  -h, --help         Exibe a mensagem de ajuda e opções
```

---

## Como Funciona

### 1. Descoberta de Topologia
O daemon consulta a árvore de nós do canvas através da interface de linha de comando do Maestri. O comando `maestri list` devolve a relação de terminais, fichários e conexões ativas. Nós não interativos ou notas são descartados da cadeia de leitura.

### 2. Leitura e Decodificação de Telas
A cada intervalo configurado (padrão: 10 segundos), o coletor faz uma amostragem em memória de cada agente via `maestri check "<agente>"`.

O módulo analítico [`lib/leitor.py`](lib/leitor.py) extrai:
- **Ferramenta**: Identificação automática de assinaturas de runtime (`Claude Code`, `Codex`, `Antigravity`).
- **Estado Operacional**: `trabalhando`, `pensando`, `ocioso`, `aguardando confirmação` ou `em segundo plano`.
- **Métricas de Sessão**: Uso de janela de contexto (%), cota periódica (5h), cota semanal (%) e modelo atualmente carregado.
- **Atividade Recente**: A última ação ou intenção declarada pelo agente, com higienização estrita de tokens, caminhos e segredos (`mascarar()`).

### 3. Visualização Reativa
A interface servida em [`publico/`](publico/) utiliza componentes leves com renderização via Vanilla JS e CSS moderno. Os dados são consumidos via requisições assíncronas atômicas (`dados.json`), garantindo que o painel nunca trave nem exiba estados parciais.

---

## Estrutura do Repositório

```
painel-agents/
├── painel               # Launcher executável Unix (Linux / macOS)
├── painel.cmd           # Launcher executável Windows
├── painel.py            # Servidor HTTP, orquestrador e daemon de coleta
├── instalar.bat         # Instalador 1-click para Windows
├── instalar.sh          # Instalador 1-click para Linux / macOS
├── lib/
│   ├── terminais.py     # Adaptador unificado IPC (Maestri-first + Tmux fallback)
│   ├── leitor.py        # Parser determinístico de telas e statuslines
│   ├── contas.py        # Coletor de limites das CLIs instaladas
│   ├── cotas.py         # Resolução e agrupamento de cotas compartilhadas
│   ├── prs.py           # Integração com GitHub CLI (gh) para pull requests
│   └── comum.py         # Gravação atômica e travas multiplataforma (msvcrt / fcntl)
├── publico/
│   ├── index.html       # Dashboard visual
│   ├── cotas.js         # Widgets de exibição de cotas
│   └── cartao-apoio.js  # Painéis contextuais
└── skills/
    └── painel-agents/   # Skill para agentes operarem o painel autonomamente
```

---

## Integração via Skill (Agentes Autônomos)

O `painel-agents` acompanha uma skill pronta para agentes Maestri / Codex / Antigravity em [`skills/painel-agents/SKILL.md`](skills/painel-agents/SKILL.md).

Ao conversar com qualquer agente no seu canvas, você pode pedir:
- *"Abra o painel de agentes"*
- *"Verifique o status do time no painel"*
- *"Mostre o consumo de cotas atual"*

O agente invocará o comando nativo sem requerer nenhuma intervenção manual.

---

## Segurança e Privacidade

- **Operação 100% Local**: O servidor se vincula exclusivamente a `127.0.0.1`. Nenhuma métrica, telemetria ou tela é enviada para a internet.
- **Filtro de Segredos**: O leitor de telas possui padrões de rejeição automática para chaves de API (`sk-`, `ghp_`, `sbp_`), senhas e variáveis sensíveis.
- **Somente Leitura**: As leituras de tela utilizam rotinas não destrutivas que não injetam caracteres nem interrompem o fluxo de pensamento dos agentes.

---

## Créditos e Licença

Distribuído sob a licença [MIT](LICENSE).

Inspirado e construído com base nos trabalhos originais de:
- [Overview-Painel](https://github.com/cauecarneiro/Overview-Painel) por Cauê Carneiro ([@cauenovic](https://github.com/cauenovic))
- [cotas-dashboard](https://github.com/fbtostadev/cotas-dashboard) por Fábio Tosta ([@fbtostadev](https://github.com/fbtostadev))
