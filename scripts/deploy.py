#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import tarfile
import time

def run(cmd, check=True):
    print(f"+ {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.stdout:
        print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed with code {res.returncode}: {' '.join(cmd)}")
    return res

def main():
    ts = time.strftime("%Y%m%d_%H%M%S")
    print(f"=== DEPLOYING TELNUSA NMS R4 ({ts}) ===")

    # 1. Backups
    os.makedirs("/opt/telnusa-nms/backups", exist_ok=True)
    os.makedirs("/var/www/telnusa-nms/backups", exist_ok=True)
    
    if os.path.exists("/opt/telnusa-nms/backend"):
        bak_backend = f"/opt/telnusa-nms/backups/backend.bak_{ts}"
        shutil.copytree("/opt/telnusa-nms/backend", bak_backend)
        print(f"Backed up backend to {bak_backend}")
        
    if os.path.exists("/var/www/telnusa-nms/dist"):
        bak_dist = f"/var/www/telnusa-nms/backups/dist.bak_{ts}"
        shutil.copytree("/var/www/telnusa-nms/dist", bak_dist)
        print(f"Backed up dist to {bak_dist}")

    if os.path.exists("/etc/apache2/sites-available/telnusa-nms.conf"):
        bak_apache = f"/etc/apache2/sites-available/telnusa-nms.conf.bak_{ts}"
        shutil.copy2("/etc/apache2/sites-available/telnusa-nms.conf", bak_apache)
        print(f"Backed up apache conf to {bak_apache}")

    if os.path.exists("/etc/grafana/grafana.ini"):
        bak_grafana = f"/etc/grafana/grafana.ini.bak_{ts}"
        shutil.copy2("/etc/grafana/grafana.ini", bak_grafana)
        print(f"Backed up grafana.ini to {bak_grafana}")

    # 2. Extract tarball
    extract_dir = "/tmp/r4-deploy-extracted"
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir, exist_ok=True)
    with tarfile.open("/tmp/r4-deploy.tar.gz", "r:gz") as tar:
        tar.extractall(extract_dir)
    print("Extracted deploy tarball.")

    # 3. Deploy Backend
    src_backend = os.path.join(extract_dir, "backend")
    dst_backend = "/opt/telnusa-nms/backend"
    for item in os.listdir(src_backend):
        s = os.path.join(src_backend, item)
        d = os.path.join(dst_backend, item)
        if os.path.isdir(s):
            if os.path.exists(d):
                shutil.rmtree(d)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    run(["chown", "-R", "telnusa:telnusa", dst_backend])
    print("Backend deployed.")

    # 4. Deploy Frontend
    src_dist = os.path.join(extract_dir, "dist")
    dst_dist = "/var/www/telnusa-nms/dist"
    for item in os.listdir(dst_dist):
        p = os.path.join(dst_dist, item)
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    for item in os.listdir(src_dist):
        s = os.path.join(src_dist, item)
        d = os.path.join(dst_dist, item)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    run(["chown", "-R", "www-data:www-data", dst_dist])
    print("Frontend dist deployed.")

    # 5. Deploy Grafana Dashboards & Provisioning
    os.makedirs("/var/lib/grafana/dashboards", exist_ok=True)
    src_dash = os.path.join(extract_dir, "ops/grafana/dashboards/telnusa-nms-overview.json")
    dst_dash = "/var/lib/grafana/dashboards/telnusa-nms-overview.json"
    shutil.copy2(src_dash, dst_dash)
    run(["chown", "-R", "grafana:grafana", "/var/lib/grafana/dashboards"])
    run(["chmod", "644", dst_dash])

    os.makedirs("/etc/grafana/provisioning/dashboards", exist_ok=True)
    src_prov = os.path.join(extract_dir, "ops/grafana/provisioning/dashboards/telnusa-dashboards.yaml")
    dst_prov = "/etc/grafana/provisioning/dashboards/telnusa-dashboards.yaml"
    shutil.copy2(src_prov, dst_prov)
    run(["chown", "root:grafana", dst_prov])
    run(["chmod", "640", dst_prov])
    print("Grafana provisioning & dashboards deployed.")

    # 6. Update grafana.ini for subpath
    with open("/etc/grafana/grafana.ini", "r") as f:
        graf_content = f.read()

    # ensure root_url and serve_from_sub_path are set
    if "serve_from_sub_path = true" not in graf_content:
        graf_content = graf_content.replace(";serve_from_sub_path = false", "serve_from_sub_path = true")
        graf_content = graf_content.replace("serve_from_sub_path = false", "serve_from_sub_path = true")
    if "root_url = %(protocol)s://%(domain)s:%(http_port)s/grafana/" not in graf_content:
        graf_content = graf_content.replace(";root_url = %(protocol)s://%(domain)s:%(http_port)s/", "root_url = %(protocol)s://%(domain)s:%(http_port)s/grafana/")

    with open("/etc/grafana/grafana.ini", "w") as f:
        f.write(graf_content)
    print("Grafana.ini updated for /grafana/ subpath.")

    # 7. Update Apache site configuration
    with open("/etc/apache2/sites-available/telnusa-nms.conf", "r") as f:
        ap_conf = f.read()

    if "!^/grafana/" not in ap_conf:
        ap_conf = ap_conf.replace(
            "RewriteCond %{REQUEST_URI} !^/guacamole/",
            "RewriteCond %{REQUEST_URI} !^/guacamole/\n        RewriteCond %{REQUEST_URI} !^/grafana/"
        )

    if "<Location /grafana/>" not in ap_conf:
        graf_stanzas = """
    # Grafana reverse proxy and WebSocket live updates
    <Location /grafana/api/live/ws>
        ProxyPass ws://127.0.0.1:3000/grafana/api/live/ws
        ProxyPassReverse ws://127.0.0.1:3000/grafana/api/live/ws
    </Location>

    <Location /grafana/>
        ProxyPass http://127.0.0.1:3000/grafana/
        ProxyPassReverse http://127.0.0.1:3000/grafana/
        Header always set X-Frame-Options "SAMEORIGIN"
    </Location>
"""
        ap_conf = ap_conf.replace(
            "</VirtualHost>",
            f"{graf_stanzas}\n</VirtualHost>"
        )

    with open("/etc/apache2/sites-available/telnusa-nms.conf", "w") as f:
        f.write(ap_conf)
    print("Apache configuration updated.")

    # 8. Test Apache
    run(["apache2ctl", "configtest"])

    # 9. Restart / Reload services
    run(["systemctl", "reload", "apache2"])
    run(["systemctl", "restart", "grafana-server"])
    run(["systemctl", "restart", "telnusa-nms.service"])
    print("Services restarted successfully.")

if __name__ == "__main__":
    main()
