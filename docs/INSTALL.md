# Zien Quant - Installation & Deployment Guide

This guide covers deployment procedures for Windows, Linux (including systemd service automation), macOS, and air-gapped / offline enterprise environments.

---

## 1. System Requirements

- **Operating System**: Linux (Ubuntu 20.04+, Debian 11+, RHEL 9+), Windows 10/11, macOS 12+
- **Python**: Version 3.12.0 or newer
- **Disk Space**: At least 250 MB free on the installation volume
- **RAM**:
  - Without Ollama LLM: 512 MB to 2 GB
  - With Ollama LLM (`gemma4:12b`): 16 GB RAM (or 8 GB VRAM on GPU)
- **External Dependencies**: Zero pip packages required; built entirely on Python Standard Library.

---

## 2. Linux / macOS Installation

### 2.1 Automated Setup
```bash
git clone https://github.com/ekanth14/zien-quant.git
cd zien-quant
bash scripts/setup.sh
```

### 2.2 Manual Step-by-Step Setup
If you prefer manual execution:
```bash
# 1. Create runtime storage
mkdir -p work/runtime work/backups

# 2. Configure environment
cp .env.example .env

# 3. Initialize database tables
python3 -c "from marketscope.security import DatabaseManager; DatabaseManager('work/runtime/marketscope.db')"

# 4. Populate stock master database
python3 manage.py import-stock-master

# 5. Create initial owner account
python3 manage.py init-owner
```

### 2.3 Production Deployment via systemd
To run Zien Quant as a reliable background service on Linux:

1. Create a service file `/etc/systemd/system/zien-quant.service`:
```ini
[Unit]
Description=Zien Quant - Order Block Terminal
After=network.target

[Service]
Type=simple
User=trading
Group=trading
WorkingDirectory=/opt/zien-quant
ExecStart=/usr/bin/python3 /opt/zien-quant/server.py --host 127.0.0.1 --port 8765
Restart=always
RestartSec=5
EnvironmentFile=/opt/zien-quant/.env
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

2. Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable zien-quant
sudo systemctl start zien-quant
sudo systemctl status zien-quant
```

---

## 3. Windows Installation

### 3.1 Automated Setup (PowerShell)
```powershell
git clone https://github.com/ekanth14/zien-quant.git
cd zien-quant
.\scripts\setup.ps1
```

### 3.2 Manual Setup (PowerShell)
```powershell
New-Item -ItemType Directory -Force -Path work/runtime, work/backups
Copy-Item .env.example .env
python -c "from marketscope.security import DatabaseManager; DatabaseManager('work/runtime/marketscope.db')"
python manage.py import-stock-master
python manage.py init-owner
```

### 3.3 Starting the Terminal
```powershell
.\scripts\run.ps1
```

---

## 4. Setting up Ollama for Local AI Synthesis (Optional)

Zien Quant features an optional deep analysis engine powered by Ollama.

1. **Install Ollama**: Follow instructions at [https://ollama.ai](https://ollama.ai).
2. **Download Models**:
   - **Laya Decision Model (System 1)**: Ultra-fast 421M parameter ModernBERT model for trade bias classification (~200ms):
     ```bash
     ollama pull laya
     ```
   - **Laya Generative Model (System 2)**: Specialized quantitative commentary and Copilot chat:
     ```bash
     # Either pull the base model:
     ollama pull gemma4:12b

     # Or build the specialized Laya AI assistant from the included Modelfile:
     ollama create laya-ai -f Modelfile
     ```
3. **Verify Ollama is Listening**:
   ```bash
   python manage.py check-ollama
   # or
   curl http://127.0.0.1:11434/api/tags
   ```
4. **Configure `.env`**:
   ```env
   OLLAMA_HOST=http://127.0.0.1:11434
   OLLAMA_MODEL=gemma4:12b
   # Or use the specialized model:
   # OLLAMA_MODEL=laya-ai
   ```

*Note: If Ollama is offline or uninstalled, Zien Quant gracefully defaults to its 0ms quantitative rules engine without degradation of trade signals.*

---

## 5. Air-Gapped / Offline Environments

Zien Quant requires no external pip packages, CDN connections, or third-party fonts at runtime. To deploy in an isolated, network-restricted environment:

1. Clone or archive the repo on an internet-connected workstation:
   ```bash
   git archive --format=zip HEAD -o zien-quant.zip
   ```
2. Transfer `zien-quant.zip` to the air-gapped machine.
3. Unzip and run:
   ```bash
   python manage.py import-stock-master
   python manage.py init-owner
   python server.py --host 127.0.0.1 --port 8765
   ```
4. In air-gapped environments, the terminal uses local persisted historical candles in `candles.db` to render the Previous Session replay view.

