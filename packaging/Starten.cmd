@echo off
setlocal
pushd "%~dp0App"
start "" "AtracsysLiveTracker_Messskripte.exe" --mode hardware
popd
