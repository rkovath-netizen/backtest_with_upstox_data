import os

# Define the folder structure
folders = [
    "common",
    "strategies",
    "strategies/comparative_options",
    "strategies/rsi_ubb_bull_put",
    "data_outputs"
]

# Define the baseline files to create
files = {
    "pyproject.toml": """[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "upstox-trading-hub"
version = "0.1.0"
dependencies = [
    "streamlit>=1.30.0",
    "pandas>=2.0.0",
    "numpy>=1.24.0",
    "requests>=2.31.0",
    "PyGithub>=2.1.0",
    "pytz>=2023.3",
    "pandas-ta>=0.3.14b0"
]

[tool.setuptools.packages.find]
where = ["."]
""",
    "requirements.txt": "-e .\nstreamlit>=1.30.0\npandas>=2.0.0\nrequests>=2.31.0\nPyGithub>=2.1.0\npytz>=2023.3\npandas-ta>=0.3.14b0\n",
    ".gitignore": "__pycache__/\n*.pyc\n.streamlit/secrets.toml\n.venv/\n",
    "main_dashboard.py": "# Main Streamlit Launcher\nimport streamlit as st\nst.title('⚡ Upstox Trading Hub')\n",
    "common/__init__.py": "",
    "common/market_data.py": "# Upstox API & Data Layer (Paste your robust code here)\n",
    "common/calculations.py": "# Metrics & PnL Logic\n",
    "common/github_uploader.py": "# GitHub Push Logic\n",
    "strategies/__init__.py": "",
    "strategies/comparative_options/__init__.py": "",
    "strategies/comparative_options/strategy_engine.py": "# Comparative Engine Logic\n",
    "strategies/comparative_options/app.py": "# Comparative UI\n",
    "strategies/rsi_ubb_bull_put/__init__.py": "",
    "strategies/rsi_ubb_bull_put/strategy_engine.py": "# 15m RSI & UBB Engine Logic\n",
    "strategies/rsi_ubb_bull_put/app.py": "# RSI & UBB UI\n",
    "data_outputs/.gitkeep": ""
}

print("🛠️ Scaffolding Upstox Trading Hub...")

# Create Folders
for folder in folders:
    os.makedirs(folder, exist_ok=True)
    print(f"📁 Created directory: {folder}/")

# Create Files
for filepath, content in files.items():
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"📄 Created file: {filepath}")

print("✅ Architecture successfully generated!")