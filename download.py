import socket


def download_file_from_esp(host, port, file_name, output_file):
    # Create a socket object
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        # Connect to the server
        client_socket.connect((host, port))
        print(f"Connected to {host}:{port}")

        # Send the request to download the file
        request = f"hostFile={file_name}\n"
        client_socket.sendall(request.encode())

        # Open a file to write the received data
        with open(output_file, "wb") as file:
            while True:
                # Receive data in chunks
                data = client_socket.recv(1024)
                if not data:
                    break
                file.write(data)

        print(f"File '{file_name}' downloaded successfully as '{output_file}'")

    except Exception as e:
        print(f"Error: {e}")

    finally:
        # Close the socket
        client_socket.close()


# Example usage
download_file_from_esp("10.211.187.19", 80, "/test.txt", "test.txt")
