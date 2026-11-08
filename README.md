# Painel de Agentes (Maestri & Multiplataforma)

Monitor local e visual em tempo real para acompanhar agentes de inteligência artificial (Claude Code, Codex e Antigravity) rodando em workspaces no **Maestri** (e em terminais convencionais com Tmux).

Projetado para rodar nativamente em **Windows**, **Fedora / Linux** e **macOS**.

---

## Recursos

- **Auto-Descoberta de Workspace**: Descobre automaticamente todos os agentes ativos no canvas atual via `maestri list` (sem necessidade de cadastrar nada manualmente).
- **Sem gasto de tokens**: Apenas lê as telas com comandos de leitura do Maestri (`maestri check`) e verifica as cotas administrativas das ferramentas locais.
- **Criação Automática de Portal**: Ao iniciar, já abre automaticamente o card de Portal no canvas do Maestri.
- **Multiplataforma Real**: Código 100% Python padrão, sem dependências externas nem pacotes pesados.

---

## Como Usar no Dia a Dia

Dentro de qualquer terminal do seu Workspace no Maestri, execute:

```bash
painel
```

O comando:
1. Detecta quais agentes estão conectados ao canvas daquele workspace.
2. Inicia o coletor em segundo plano e sobe o servidor HTTP local (`http://localhost:3333`).
3. Abre automaticamente o card do **Portal** no seu canvas do Maestri.

### Opções:

```bash
painel                # Modo contínuo com abertura automática de portal
painel --uma-vez      # Faz apenas uma leitura e mostra o resumo no terminal
painel --sem-portal   # Inicia o servidor e coletor sem abrir o portal automaticamente
painel --porta 3334   # Escolhe uma porta HTTP diferente
```

---

## Como Instalar em Novas Máquinas

### No Windows:
1. Clone o repositório:
   ```cmd
   git clone https://github.com/seu-usuario/painel-agents.git
   cd painel-agents
   ```
2. Execute o instalador:
   ```cmd
   instalar.bat
   ```

### No Fedora / Linux:
1. Clone o repositório:
   ```bash
   git clone https://github.com/seu-usuario/painel-agents.git
   cd painel-agents
   ```
2. Dê permissão e execute o instalador:
   ```bash
   chmod +x instalar.sh
   ./instalar.sh
   ```

---

## Estrutura do Projeto

- `painel.py`: Core do painel (servidor web + orquestrador de coleta + abertura de portal).
- `painel`: Executável direto para Linux / macOS.
- `painel.cmd`: Executável direto para Windows.
- `lib/`:
  - `terminais.py`: Adaptador unificado para o Maestri e fallback para Tmux.
  - `leitor.py`: Parser das telas de Claude Code, Codex e Antigravity.
  - `comum.py`: Utilitários e gravação atômica compatível com Windows e Linux.
- `publico/`: Interface web estática visualizada pelo Portal do Maestri.
- `skills/`: Skill de integração para agentes do Maestri.

---

## Créditos e Licença

- Baseado e aprimorado a partir dos projetos [Overview-Painel](https://github.com/cauecarneiro/Overview-Painel) de Caue Carneiro (@cauenovic) e [cotas-dashboard](https://github.com/fbtostadev/cotas-dashboard) de fbtostadev (Licença MIT).
