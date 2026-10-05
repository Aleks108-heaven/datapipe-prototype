@echo off
rem Double-click to start datapipe. Your files go in %USERPROFILE%\datapipe\files ; results are written to %USERPROFILE%\datapipe\work
setlocal
set "ROOT=%USERPROFILE%\datapipe"
if not exist "%ROOT%\files" mkdir "%ROOT%\files"
where datapipe >nul 2>nul
if %errorlevel%==0 (
  datapipe --workdir "%ROOT%\work" app --data-dir "%ROOT%\files" %*
  goto :done
)
where python >nul 2>nul
if %errorlevel%==0 (
  python -m datapipe --workdir "%ROOT%\work" app --data-dir "%ROOT%\files" %*
  goto :done
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m datapipe --workdir "%ROOT%\work" app --data-dir "%ROOT%\files" %*
  goto :done
)
echo datapipe is not installed, or Python was not found.
echo Install Python 3.10 or newer from python.org, then run:   python -m pip install git+https://github.com/Aleks108-heaven/datapipe-prototype.git
echo (see docs\INSTALL.md)
pause
:done
if errorlevel 1 pause
