copy .\dist\EspAdc\settings.ini .\dist\settings\settings.ini
pyinstaller main.py --clean -n EspAdc --onedir --icon="./assets/volt64.png" --noconsole -y --add-data="assets:assets"
copy .\dist\settings\settings.ini .\dist\EspAdc\settings.ini
mkdir .\dist\EspAdc\assets
copy .\assets\* .\dist\EspAdc\assets
