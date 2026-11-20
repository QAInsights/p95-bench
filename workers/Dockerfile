FROM grafana/k6@sha256:f21270290d702cbf0a7d6ba5d7ed100b63bcb233b558b885ed787547b3488279 AS k6-source
FROM eclipse-temurin@sha256:aa6381384f8db99762fa7d0d3c2b5de58874e5f12c96df4c13a1455376ae0ed1 AS java-source
FROM eclipse-temurin@sha256:fbe9d94debbe24e529acfe235b98f065edea050a2dc00129b7d86261ce3248c8 AS jdk-source
FROM python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS base
ENV PYTHONDONTWRITEBYTECODE=1

FROM base AS k6
COPY --from=k6-source /usr/bin/k6 /usr/local/bin/k6
LABEL org.p95bench.tool="k6" org.p95bench.version="1.0.0"
RUN k6 version

FROM base AS jmeter
COPY --from=java-source /opt/java/openjdk /opt/java/openjdk
COPY apache-jmeter-5.6.3.tgz /tmp/jmeter.tgz
RUN python3 -c 'import hashlib,tarfile; p="/tmp/jmeter.tgz"; assert hashlib.sha512(open(p,"rb").read()).hexdigest()=="5978a1a35edb5a7d428e270564ff49d2b1b257a65e17a759d259a9283fc17093e522fe46f474a043864aea6910683486340706d745fcdf3db1505fd71e689083"; tarfile.open(p).extractall("/opt", filter="data")' && rm /tmp/jmeter.tgz
ENV JAVA_HOME=/opt/java/openjdk PATH=/opt/java/openjdk/bin:/opt/apache-jmeter-5.6.3/bin:/usr/local/bin:/usr/bin:/bin
LABEL org.p95bench.tool="jmeter" org.p95bench.version="5.6.3"
RUN java -Djava.awt.headless=true -jar /opt/apache-jmeter-5.6.3/bin/ApacheJMeter.jar --version

FROM base AS locust
COPY locust-requirements.txt /tmp/locust-requirements.txt
COPY locust-wheels /tmp/locust-wheels
RUN python3 -m pip install --no-index --no-deps --require-hashes \
    --find-links=/tmp/locust-wheels -r /tmp/locust-requirements.txt \
    && rm -rf /tmp/locust-requirements.txt /tmp/locust-wheels
LABEL org.p95bench.tool="locust" org.p95bench.version="2.47.0"
RUN locust --version

FROM base AS gatling
COPY --from=jdk-source /opt/java/openjdk /opt/java/openjdk
COPY gatling-libs/ /opt/gatling/lib/
COPY gatling-logback.xml /opt/gatling/logback.xml
ENV JAVA_HOME=/opt/java/openjdk PATH=/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin
LABEL org.p95bench.tool="gatling" org.p95bench.version="3.15.1"
RUN javac -version && java -version
