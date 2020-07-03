FROM python:3.8-buster

ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN pip install gunicorn

ARG JS9_VERSION=3.0
RUN curl -LJ -o js9.tar.gz https://github.com/ericmandel/js9/archive/v${JS9_VERSION}.tar.gz \
    && tar -xzvf js9.tar.gz \
    && cd js9-${JS9_VERSION} \
    && ./configure --with-webdir=/app/static/js9 \
    && make \
    && make install \
    && cd - \
    && rm -rf js9.tar.gz js9-${JS9_VERSION}

COPY requirements.txt /app/
RUN pip install -r /app/requirements.txt

WORKDIR /app
EXPOSE 80

ENV PYTHONUNBUFFERED TRUE

COPY ./assets/* /app/assets/
COPY ./static/js/* /app/static/js/
COPY *.py /app/

ENTRYPOINT ["gunicorn", "-w4", "-b0.0.0.0:80", "main:server()"]
