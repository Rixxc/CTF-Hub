import multiprocessing

wsgi_app = "app:app"
bind = "0.0.0.0:1234"
workers = multiprocessing.cpu_count()
threads = 4
chdir = "/app"
worker_class = "gevent"
