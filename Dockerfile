FROM python:3.11-alpine

RUN apk add --no-cache gcc musl-dev libffi-dev

RUN pip3 install --no-cache-dir midea-local paho-mqtt

WORKDIR /app
COPY midea_mqtt_bridge.py .

CMD ["python3", "-u", "midea_mqtt_bridge.py"]
