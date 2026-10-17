# Quick start

## 1. See it without touching anything

```bash
pipx install homelabscribe
homelabscribe demo
```

Open `homelabscribe-demo/index.md`. It is a made-up homelab with every Pro page enabled.

## 2. Scan your own Docker host

1. Run a read-only [socket proxy](installing.md#docker-compose) next to Docker (or point at the socket directly if HomelabScribe™ runs on the same machine).
2. Copy `homelabscribe.example.yaml` to `homelabscribe.yaml` and set your host:

   ```yaml
   output_dir: ./homelabscribe-docs
   docker_hosts:
     - name: nova
       url: unix:///var/run/docker.sock
   ```
3. Check the connection, then run:

   ```bash
   homelabscribe doctor -c homelabscribe.yaml
   homelabscribe run -c homelabscribe.yaml
   ```
4. Open `homelabscribe-docs/index.md` (or point `output_dir` at a folder in your Obsidian vault).

Write your own notes in the **Notes** block at the bottom of any page; they survive every run. See the [user guide](user-guide/README.md).
