@echo off
setlocal enabledelayedexpansion

echo ========================================================
echo    Instalador do Painel de Agentes (Windows / Maestri)
echo ========================================================

set "BIN_DIR=%USERPROFILE%\.local\bin"
set "SKILL_DIR=%USERPROFILE%\.agents\skills\painel-agents"
set "REPO_DIR=%~dp0"

:: 1. Criar diretorio .local\bin se nao existir
if not exist "%BIN_DIR%" (
    mkdir "%BIN_DIR%"
)

:: 2. Criar script launcher em ~/.local/bin/painel.cmd
echo [1/3] Configurando comando global 'painel'...
(
echo @echo off
echo python "%REPO_DIR%painel.py" %%*
) > "%BIN_DIR%\painel.cmd"
echo   -> Criado: %BIN_DIR%\painel.cmd

:: 3. Instalar Skill no Maestri / Agentes
echo [2/3] Instalando Skill para o Maestri...
if not exist "%SKILL_DIR%" (
    mkdir "%SKILL_DIR%"
)
copy /Y "%REPO_DIR%skills\painel-agents\SKILL.md" "%SKILL_DIR%\SKILL.md" >nul
echo   -> Skill instalada em: %SKILL_DIR%\SKILL.md

:: 4. Verificacao de Python
echo [3/3] Verificando ambiente Python...
python --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   -> Python detectado com sucesso.
) else (
    echo   -> AVISO: Python nao foi encontrado no PATH! Instale o Python 3.
)

echo.
echo ========================================================
echo   SUCESSO! O Painel de Agentes esta instalado.
echo   Para usar no Maestri:
echo   Abra qualquer terminal do seu workspace e digite:
echo      painel
echo ========================================================
echo.
pause
