import socket
import threading
import ipaddress
import ifaddr


class AutoDiscoverer:

    # ip_range = "192.168.1."
    port = 80  # Standard HTTP port
    static_ips = ["127.0.0.1"]

    def get_local_network_range(self):
        # Get all network adapters
        adapters = ifaddr.get_adapters()

        for adapter in adapters:
            for ip in adapter.ips:
                if isinstance(ip.ip, tuple):  # Skip IPv6 addresses
                    continue

                ip_address = ip.ip
                netmask = ip.network_prefix

                # Calculate the network using the IP address and prefix length
                network = ipaddress.IPv4Network(f"{ip_address}/{netmask}", strict=False)

                # Return the network range
                return str(network.network_address), network.prefixlen

    def scan_ip(self, ip):
        try:
            # Create a socket object
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)  # Set timeout to 1 second
            result = sock.connect_ex((ip, self.port))
            if result == 0:
                print(f"Server found at {ip}:{self.port}")
            sock.close()
        except Exception as e:
            print(f"Error scanning {ip}: {e}")

    def get_ip_range(self):

        

    def discover(self):

        ip_range = 

        # Scan the IP range
        threads = []
        for i in range(1, 255):  # Assuming a /24 subnet
            ip = f"{ip_range}{i}"
            thread = threading.Thread(target=scan_ip, args=(ip,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()
