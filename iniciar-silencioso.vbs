' Inicializador silencioso sob demanda para projetos Maestri/Minerva (nao roda no boot global do Windows).
' Verifica se a porta 3333 ja esta ativa via --ensure e, se nao estiver, sobe o daemon em background.
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "S:\DEV\painel-agents"
WshShell.Run "pythonw ""S:\DEV\painel-agents\painel.py"" --sem-portal --ensure", 0, False
