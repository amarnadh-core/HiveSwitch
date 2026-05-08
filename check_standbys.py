import socket
import json
import urllib.request
import concurrent.futures
import ipaddress

def get_local_subnet():
    # Attempt to guess the local subnet by connecting a dummy socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "192.168.1.1"
    finally:
        s.close()
    
    # Return a basic /24 subnet based on the local IP
    parts = local_ip.split('.')
    return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"

def check_helper(ip):
    url = f"http://{ip}:8765/session"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                return ip, data
    except Exception:
        pass
    return ip, None

def scan_network():
    subnet = get_local_subnet()
    print(f"Scanning local network {subnet} for Serverless MC Helpers (Port 8765)...")
    
    network = ipaddress.IPv4Network(subnet, strict=False)
    hosts = [str(ip) for ip in network.hosts()]
    
    # Also explicitly add known ZeroTier IPs if you use them
    hosts.extend(["10.136.97.92", "10.136.97.35", "127.0.0.1"])
    
    # Deduplicate
    hosts = list(set(hosts))
    
    found_any = False
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        results = executor.map(check_helper, hosts)
        
        for ip, data in results:
            if data:
                found_any = True
                
                # Try to extract useful info from the payload
                # Note: The web API returns the session payload by default
                session = data.get("session", {})
                player = session.get("host", "Unknown") if isinstance(session, dict) else data.get("host_player", "Unknown")
                
                # The raw web API returns a lot of info, we just check if it responded
                print(f"\n[+] Found Helper at {ip}")
                print(f"    Raw Payload: {data}")

    if not found_any:
        print("\nNo Serverless MC Helpers found on the network.")

if __name__ == "__main__":
    scan_network()
