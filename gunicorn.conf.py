"""Render 512MB 方案的穩定性設定。"""

workers = 1
worker_class = "gthread"
threads = 2
timeout = 90
graceful_timeout = 30

# OAuth callbacks contain short-lived authorization codes. Do not log query
# strings or referrers; retain method, URL path and status for diagnostics.
access_log_format = '%(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s'

# 不使用 max_requests 定期更換 worker。這個專案載入 OpenAI、Google 套件後
# 單一 worker 已佔用不少記憶體；在 512MB Starter 上，Gunicorn 更換 worker
# 時舊、新程序會短暫重疊，反而造成 Render OOM 並整台重啟。
