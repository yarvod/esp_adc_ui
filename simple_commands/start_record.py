# Этот код ЗАПУСКАЕТ запись даных в файл
# Необходимо указать имя файла:
FILE_NAME = "/test_2025_05_04.txt"
import socket

with socket.socket() as sock:
    try:
        sock.settimeout(3)
        sock.connect(("192.168.4.1", 80))

        if not FILE_NAME.startswith("/"):
            FILE_NAME = "/" + FILE_NAME
        if not FILE_NAME.endswith(".txt"):
            FILE_NAME += ".txt"
        command = f"start={FILE_NAME}\n".encode("ascii")
        sock.send(command)
        response = sock.recv(1024 * 1024)
        response = response.decode("ascii").rstrip().replace("\n", "")
        print(f"Response: {response}")
    except (Exception, OSError, TimeoutError) as e:
        print(f"Error: {e}")
