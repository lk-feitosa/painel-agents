#!/usr/bin/env bash
set -e

echo "========================================================"
echo "   Instalador do Painel de Agentes (Fedora / Linux)"
echo "========================================================"

BIN_DIR="$HOME/.local/bin"
SKILL_DIR="$HOME/.agents/skills/painel-agents"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$BIN_DIR"
mkdir -p "$SKILL_DIR"

echo "[1/3] Configurando comando global 'painel' em $BIN_DIR..."
chmod +x "$REPO_DIR/painel"
ln -sf "$REPO_DIR/painel" "$BIN_DIR/painel"
echo "  -> Link criado: $BIN_DIR/painel"

echo "[2/3] Instalando Skill para o Maestri..."
cp -f "$REPO_DIR/skills/painel-agents/SKILL.md" "$SKILL_DIR/SKILL.md"
echo "  -> Skill instalada em: $SKILL_DIR/SKILL.md"

echo "[3/3] Verificando Python..."
if command -v python3 >/dev/null 2>&1; then
    echo "  -> Python 3 detectado: $(python3 --version)"
else
    echo "  -> AVISO: python3 não encontrado! No Fedora execute: sudo dnf install python3"
fi

echo ""
echo "========================================================"
echo "  SUCESSO! O Painel de Agentes está instalado."
echo "  Certifique-se de que ~/.local/bin está no seu PATH."
echo "  No Maestri, basta abrir um terminal e digitar:"
echo "     painel"
echo "========================================================"
echo ""
