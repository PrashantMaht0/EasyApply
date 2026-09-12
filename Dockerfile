FROM public.ecr.aws/docker/library/python:3.12-slim-trixie

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN useradd -m -u 1000 bedrock_agentcore

COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY --chown=bedrock_agentcore:bedrock_agentcore . .
USER bedrock_agentcore

# the AgentCore Runtime HTTP contract listens here
EXPOSE 8080

# opentelemetry-instrument is what sends spans to CloudWatch, see core/trace.py
CMD ["opentelemetry-instrument", "python", "-m", "agentcore_app"]
