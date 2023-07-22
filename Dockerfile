FROM python:3.11-slim-buster

WORKDIR /app

RUN pip install gunicorn[gevent]=="20.1.0"
RUN pip install pipenv

COPY Pipfile .
COPY Pipfile.lock .
RUN pipenv install --deploy --system

COPY . .

CMD python init_app.py && gunicorn -c gunicorn.conf.py
