FROM python:3.5.7-slim-buster

RUN mkdir /app
WORKDIR /app
ADD requirements.txt /app/
ADD . /app

RUN pip3 install --no-cache-dir -r ./requirements.txt
