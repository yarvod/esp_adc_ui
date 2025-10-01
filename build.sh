#! /bin/bash

pyinstaller main.py --clean -n EspAdc --onedir --icon="./assets/volt64.png" --noconsole -y --add-data="assets:assets"
cp ./settings.ini ./dist/EspAdc/settings.ini
mkdir ./dist/EspAdc/assets
cp ./assets/* ./dist/EspAdc/assets
