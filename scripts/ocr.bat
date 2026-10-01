@echo off
setlocal
set "ROOT1=%~dp0.."
set "ROOT2=%~dp0..\.."
set "ROOT3=%~dp0..\..\.."
for %%I in ("%ROOT1%") do set "ROOT1=%%~fI"
for %%I in ("%ROOT2%") do set "ROOT2=%%~fI"
for %%I in ("%ROOT3%") do set "ROOT3=%%~fI"

set "PYTHON="
set "PYTHON_ROOT="
for %%R in ("%ROOT1%" "%ROOT2%" "%ROOT3%") do (
  if not defined PYTHON if exist "%%~R\.venv\Scripts\python.exe" (
    set "PYTHON=%%~R\.venv\Scripts\python.exe"
    set "PYTHON_ROOT=%%~R"
  )
  if not defined PYTHON if exist "%%~R\venv\Scripts\python.exe" (
    set "PYTHON=%%~R\venv\Scripts\python.exe"
    set "PYTHON_ROOT=%%~R"
  )
)
if not defined PYTHON (
  echo [ERROR] No project-local virtual environment found near this OCR launcher.
  exit /b 1
)

set "OCR_HOME="
for %%R in ("%ROOT1%" "%ROOT2%" "%ROOT3%") do (
  if not defined OCR_HOME if exist "%%~R\ocr_tool\OCR.py" set "OCR_HOME=%%~R\ocr_tool"
)
if defined PYTHON_ROOT if exist "%PYTHON_ROOT%\ocr_tool\OCR.py" set "OCR_HOME=%PYTHON_ROOT%\ocr_tool"
if not defined OCR_HOME (
  echo [ERROR] OCR tool folder not found near this OCR launcher.
  exit /b 1
)
set "HF_HOME=%OCR_HOME%\cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=%HF_HOME%\hub"
set "TRANSFORMERS_CACHE=%HF_HOME%\transformers"
set "TORCH_HOME=%OCR_HOME%\cache\torch"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%HF_HOME%" mkdir "%HF_HOME%"
if not exist "%HUGGINGFACE_HUB_CACHE%" mkdir "%HUGGINGFACE_HUB_CACHE%"
if not exist "%TRANSFORMERS_CACHE%" mkdir "%TRANSFORMERS_CACHE%"
if not exist "%TORCH_HOME%" mkdir "%TORCH_HOME%"

"%PYTHON%" -u "%OCR_HOME%\OCR.py" %*
exit /b %ERRORLEVEL%
