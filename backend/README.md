# 后端

## 开发启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

## 初始化

```powershell
python -m app.cli init-db
python -m app.cli import-default
```

如果使用上面的命令，请在 `PYTHONPATH` 包含 `backend`，或从 README 根目录命令改为：

```powershell
$env:PYTHONPATH = "backend"
python -m app.cli init-db
python -m app.cli import-default
```
