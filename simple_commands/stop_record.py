# Этот код ОСТАНАВЛИВАЕТ запись даных в файл
import socket

with socket.socket() as sock:
    try:
        sock.settimeout(3)
        sock.connect(("192.168.4.1", 80))

        command = "stop".encode("ascii")
        sock.send(command)
        response = sock.recv(1024 * 1024)
        response = response.decode("ascii").rstrip().replace("\n", "")
        print(f"Response: {response}")
    except (Exception, OSError, TimeoutError) as e:
        print(f"Error: {e}")
