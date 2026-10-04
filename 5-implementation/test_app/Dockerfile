# Built from the repository root (see compose.yaml).
FROM python:3.12-slim

WORKDIR /app
COPY demo_data/requirements.txt demo_data/requirements.txt
RUN pip install --no-cache-dir -r demo_data/requirements.txt httpx==0.28.1 pyjwt==2.15.1
COPY demo_data/ demo_data/
COPY test_app/ test_app/
# The corpus is deterministic from the config, so it is generated once, inside the image.
RUN python -m demo_data generate --config demo_data/config.json --output demo_data/generated/treasury

ENTRYPOINT ["python", "-m", "test_app"]
CMD ["--list"]
