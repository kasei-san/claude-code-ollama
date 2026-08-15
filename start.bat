@echo off
setlocal enabledelayedexpansion
rem ---------------------------------------------------------------
rem  Launch Claude Code against the local Ollama endpoint.
rem  See README.md for the measurements these defaults come from.
rem ---------------------------------------------------------------

set "OLLAMA_URL=http://localhost:11434"
if "%CCL_MODEL%"=="" set "CCL_MODEL=hf.co/unsloth/Qwen3-Coder-Next-GGUF:UD-IQ2_M"
if "%CCL_FIT_TARGET%"=="" set "CCL_FIT_TARGET=384"

rem --- is a server already up? ---
curl -s -m 3 "%OLLAMA_URL%/api/version" >nul 2>&1
if errorlevel 1 (
    echo [ccl] starting ollama serve with LLAMA_ARG_FIT_TARGET=%CCL_FIT_TARGET%
    start "" /b cmd /c "set LLAMA_ARG_FIT_TARGET=%CCL_FIT_TARGET%&& ollama serve"
    set /a TRIES=0
    :waitloop
    rem timeout.exe dies when stdin is redirected; ping is the headless-safe sleep
    ping -n 2 127.0.0.1 >nul
    curl -s -m 3 "%OLLAMA_URL%/api/version" >nul 2>&1
    if errorlevel 1 (
        set /a TRIES+=1
        if !TRIES! lss 30 goto waitloop
        echo [ccl] ERROR: ollama did not come up in 30s
        exit /b 1
    )
) else (
    echo [ccl] NOTE: an ollama server is already running.
    echo [ccl] If it was not started with LLAMA_ARG_FIT_TARGET=%CCL_FIT_TARGET%,
    echo [ccl] a 160K context will spill to CPU. Restart it to be sure.
)

rem --- start the normalizing proxy (see README: system-message position) ---
if "%CCL_PROXY_PORT%"=="" set "CCL_PROXY_PORT=11435"
set "PROXY_URL=http://localhost:%CCL_PROXY_PORT%"
curl -s -m 3 "%PROXY_URL%/api/version" >nul 2>&1
if errorlevel 1 (
    echo [ccl] starting normalize_proxy on %CCL_PROXY_PORT%
    start "" /b cmd /c "python ""%~dp0normalize_proxy.py"" %CCL_PROXY_PORT% %OLLAMA_URL%"
    set /a PTRIES=0
    :proxyloop
    rem timeout.exe dies when stdin is redirected; ping is the headless-safe sleep
    ping -n 2 127.0.0.1 >nul
    curl -s -m 3 "%PROXY_URL%/api/version" >nul 2>&1
    if errorlevel 1 (
        set /a PTRIES+=1
        if !PTRIES! lss 15 goto proxyloop
        echo [ccl] ERROR: normalize_proxy did not come up
        exit /b 1
    )
)

rem --- point Claude Code at it ---
set "ANTHROPIC_BASE_URL=%PROXY_URL%"
set "ANTHROPIC_AUTH_TOKEN=ollama"
set "ANTHROPIC_API_KEY="
set "ANTHROPIC_MODEL=%CCL_MODEL%"
set "ANTHROPIC_SMALL_FAST_MODEL=%CCL_MODEL%"
set "ANTHROPIC_DEFAULT_HAIKU_MODEL=%CCL_MODEL%"
rem Claude Code does not know this model, so tell it the real window
rem (otherwise it assumes 200k and auto-compacts at the wrong point).
if "%CCL_CONTEXT%"=="" set "CCL_CONTEXT=131072"
set "CLAUDE_CODE_MAX_CONTEXT_TOKENS=%CCL_CONTEXT%"

echo [ccl] model = %CCL_MODEL%
echo [ccl] base  = %ANTHROPIC_BASE_URL%
claude %*
