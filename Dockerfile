FROM python:3.7-slim-buster

RUN pip install pipenv

COPY . .
RUN pipenv install --deploy --system

ENTRYPOINT [ "python", "app.py" ]