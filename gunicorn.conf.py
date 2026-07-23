"""Render 512MB 方案的穩定性設定。"""

workers = 1
worker_class = "gthread"
threads = 2
timeout = 90
graceful_timeout = 30

# Render 健康檢查會持續呼叫服務。定期更換 worker 可釋放 OpenAI、Google
# 套件累積的記憶體；SQLite 佇列保存在磁碟，新 worker 會接續未完成訂單。
max_requests = 8
max_requests_jitter = 2
