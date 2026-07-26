"""Bench commands for kreativ_notification."""

import click
import frappe
from frappe.utils import get_bench_path
import os
import subprocess


@click.command("setup-worker-service")
@click.option("--site", "-s", required=True, help="Site name (e.g., kreativ316)")
@click.option("--user", "-u", default="mitesh", help="System user running workers")
@click.option("--force", "-f", is_flag=True, help="Overwrite existing service")
def setup_worker_service(site: str, user: str, force: bool):
    """Create and enable systemd service for RQ workers on a site.

    Example:
        bench --site kreativ316 setup-worker-service
        bench --site kreativ216 setup-worker-service --user frappe
    """
    bench_path = get_bench_path()
    # Resolve python from bench env
    python_bin = os.path.join(bench_path, "env", "bin", "python")
    if not os.path.exists(python_bin):
        python_bin = "python3"

    service_name = f"frappe-worker-{site}"
    service_path = f"/etc/systemd/system/{service_name}.service"

    if os.path.exists(service_path) and not force:
        click.secho(f"Service {service_path} already exists. Use --force to overwrite.", fg="yellow")
        return

    service_content = f"""[Unit]
Description=Frappe RQ Worker {site}
After=redis.service
Requires=redis.service

[Service]
Type=simple
User={user}
WorkingDirectory={bench_path}/sites
ExecStart={python_bin} -m frappe.utils.bench_helper frappe worker --queue short,default,long
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
MemoryMax=2G
OOMScoreAdjust=100

[Install]
WantedBy=multi-user.target
"""

    try:
        with open(service_path, "w") as f:
            f.write(service_content)
        click.secho(f"Created {service_path}", fg="green")

        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", service_name], check=True)
        subprocess.run(["systemctl", "restart", service_name], check=True)

        click.secho(f"Enabled and started {service_name}", fg="green")
        click.echo(f"Check status: systemctl status {service_name}")
        click.echo(f"View logs: journalctl -u {service_name} -f")

    except subprocess.CalledProcessError as e:
        click.secho(f"Failed to setup service: {e}", fg="red")
        raise click.Abort()
    except PermissionError:
        click.secho("Permission denied. Run with sudo or ensure user can write /etc/systemd/system/", fg="red")
        raise click.Abort()


@click.command("remove-worker-service")
@click.option("--site", "-s", required=True, help="Site name (e.g., kreativ316)")
def remove_worker_service(site: str):
    """Remove systemd service for RQ workers on a site."""
    service_name = f"frappe-worker-{site}"
    service_path = f"/etc/systemd/system/{service_name}.service"

    try:
        subprocess.run(["systemctl", "stop", service_name], check=False)
        subprocess.run(["systemctl", "disable", service_name], check=False)
        if os.path.exists(service_path):
            os.remove(service_path)
            click.secho(f"Removed {service_path}", fg="green")
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        click.secho(f"Stopped and removed {service_name}", fg="green")
    except subprocess.CalledProcessError as e:
        click.secho(f"Failed to remove service: {e}", fg="red")
        raise click.Abort()


@click.command("worker-service-status")
@click.option("--site", "-s", required=True, help="Site name (e.g., kreativ316)")
def worker_service_status(site: str):
    """Show systemd service status for site workers."""
    service_name = f"frappe-worker-{site}"
    try:
        subprocess.run(["systemctl", "status", service_name, "--no-pager"], check=False)
    except subprocess.CalledProcessError:
        click.secho(f"Service {service_name} not found", fg="yellow")


# Frappe bench discovers commands from this list
commands = [
    setup_worker_service,
    remove_worker_service,
    worker_service_status,
]