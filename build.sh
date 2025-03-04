#! /bin/bash

pyinstaller main.py --clean -n DAQ122 --onedir --icon="./assets/daq-logo-small.png" --noconsole -y --add-data="assets:assets" --add-data="daq122.so:." --add-data="libdaq-2.0.0.dll:."
mkdir ./dist/DAQ122/assets
cp ./assets/* ./dist/DAQ122/assets
