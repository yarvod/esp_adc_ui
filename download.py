import requests


def download_file(url, local_filename):
    # Отправляем GET-запрос на указанный URL
    with requests.get(url, stream=True) as response:
        # Проверяем, успешен ли запрос
        response.raise_for_status()

        # Открываем локальный файл для записи в бинарном режиме
        with open(local_filename, "wb") as file:
            # Записываем содержимое файла по частям
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)

    print(f"Файл успешно скачан и сохранен как {local_filename}")


# Пример использования функции
download_file("http://192.168.1.33/test.txt", "test.txt")
